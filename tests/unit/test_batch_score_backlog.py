import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from evaluator import runner as evaluator_runner
from scripts import batch_score_backlog


@pytest.fixture(autouse=True)
def _isolate_in_flight_state(monkeypatch, tmp_path):
    monkeypatch.setattr(
        evaluator_runner, "_IN_FLIGHT_PATH", tmp_path / "_in_flight.json"
    )


def _scoring_batch(jobs=None, early_result=None):
    jobs = jobs or []
    return {
        "early_result": early_result,
        "jobs": jobs,
        "system_prompt": "Shared scoring prompt",
        "fingerprints": {job["id"]: f"fingerprint-{job['id']}" for job in jobs},
        "dealbreaker_stats": {"auto_rejected": 0},
    }


def _client(monkeypatch):
    client = MagicMock()
    anthropic = MagicMock()
    anthropic.Anthropic.return_value = client
    monkeypatch.setattr(batch_score_backlog, "anthropic", anthropic)
    return client


def _result(custom_id, result_type, message=None):
    return SimpleNamespace(
        custom_id=str(custom_id),
        result=SimpleNamespace(type=result_type, message=message),
    )


def _write_manifest(tmp_path, batch_id, manifest):
    path = tmp_path / f"{batch_id}.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_submit_does_not_create_batch_for_early_result(monkeypatch):
    anthropic = MagicMock()
    monkeypatch.setattr(batch_score_backlog, "anthropic", anthropic)
    monkeypatch.setattr(
        batch_score_backlog,
        "gather_scoring_batch",
        lambda: _scoring_batch(early_result={"jobs_scored": 0}),
    )

    assert batch_score_backlog.submit() == 0
    anthropic.Anthropic.assert_not_called()


def test_submit_creates_requests_and_writes_manifest(monkeypatch, tmp_path):
    jobs = [
        {
            "id": "job-1",
            "title": "Backend Engineer",
            "company": "Acme",
            "location": "Remote",
            "description": "Build APIs",
        }
    ]
    client = _client(monkeypatch)
    client.messages.batches.create.return_value = SimpleNamespace(id="batch-1")
    monkeypatch.setattr(batch_score_backlog, "_MANIFEST_DIR", tmp_path)
    monkeypatch.setattr(
        batch_score_backlog, "gather_scoring_batch", lambda: _scoring_batch(jobs=jobs)
    )

    assert batch_score_backlog.submit() == 0

    request = client.messages.batches.create.call_args.kwargs["requests"][0]
    assert request["custom_id"] == "job-1"
    assert request["params"]["system"] == [{
        "type": "text",
        "text": "Shared scoring prompt",
        "cache_control": {"type": "ephemeral"},
    }]
    assert request["params"]["tools"] == [batch_score_backlog.SCORE_TOOL]
    assert request["params"]["tool_choice"] == {
        "type": "tool",
        "name": "submit_score",
    }
    assert json.loads((tmp_path / "batch-1.json").read_text(encoding="utf-8")) == {
        "job-1": "fingerprint-job-1"
    }
    in_flight = json.loads((tmp_path / "_in_flight.json").read_text(encoding="utf-8"))
    assert in_flight.keys() == {"job-1"}
    assert in_flight["job-1"]["batch_id"] == "batch-1"
    assert "submitted_at" in in_flight["job-1"]


def test_collect_returns_before_reading_manifest_when_batch_not_ended(
    monkeypatch, tmp_path,
):
    client = _client(monkeypatch)
    client.messages.batches.retrieve.return_value = SimpleNamespace(
        processing_status="in_progress"
    )
    monkeypatch.setattr(batch_score_backlog, "_MANIFEST_DIR", tmp_path)
    manifest_path = _write_manifest(tmp_path, "batch-1", {"job-1": "fingerprint"})
    original = manifest_path.read_text(encoding="utf-8")

    assert batch_score_backlog.collect("batch-1") == 0
    client.messages.batches.results.assert_not_called()
    assert manifest_path.read_text(encoding="utf-8") == original


def test_collect_succeeded_updates_score_and_removes_manifest_entry(
    monkeypatch, tmp_path,
):
    client = _client(monkeypatch)
    client.messages.batches.retrieve.return_value = SimpleNamespace(processing_status="ended")
    message = SimpleNamespace(content=["dummy-block"])
    client.messages.batches.results.return_value = [_result("job-1", "succeeded", message)]
    monkeypatch.setattr(batch_score_backlog, "_MANIFEST_DIR", tmp_path)
    manifest_path = _write_manifest(tmp_path, "batch-1", {"job-1": "fingerprint-1"})
    monkeypatch.setattr(
        batch_score_backlog,
        "parse_score_response",
        lambda response: {"score": 8, "score_reason": "Strong match", "breakdown": {}},
    )
    update_score = MagicMock()
    monkeypatch.setattr(batch_score_backlog.job_repository, "update_score", update_score)
    update_score_and_status = MagicMock()
    monkeypatch.setattr(
        batch_score_backlog.job_repository,
        "update_score_and_status",
        update_score_and_status,
    )
    log_anthropic = MagicMock()
    monkeypatch.setattr(batch_score_backlog, "log_anthropic", log_anthropic)

    assert batch_score_backlog.collect("batch-1") == 0

    update_score.assert_called_once_with(
        "job-1", 8, "Strong match", {}, "fingerprint-1"
    )
    update_score_and_status.assert_not_called()
    log_anthropic.assert_called_once_with(
        message,
        "scorer_batch",
        batch_score_backlog.CLAUDE_MODEL,
        discount=0.5,
    )
    assert not manifest_path.exists()


def test_collect_auto_rejects_score_at_threshold(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    client.messages.batches.retrieve.return_value = SimpleNamespace(processing_status="ended")
    message = SimpleNamespace(content=[])
    client.messages.batches.results.return_value = [_result("job-1", "succeeded", message)]
    monkeypatch.setattr(batch_score_backlog, "_MANIFEST_DIR", tmp_path)
    manifest_path = _write_manifest(tmp_path, "batch-1", {"job-1": "fingerprint-1"})
    threshold = batch_score_backlog.SCORING["auto_reject_at_or_below"]
    monkeypatch.setattr(
        batch_score_backlog,
        "parse_score_response",
        lambda response: {
            "score": threshold,
            "score_reason": "Weak match",
            "breakdown": None,
        },
    )
    update_score = MagicMock()
    monkeypatch.setattr(batch_score_backlog.job_repository, "update_score", update_score)
    update_score_and_status = MagicMock()
    monkeypatch.setattr(
        batch_score_backlog.job_repository,
        "update_score_and_status",
        update_score_and_status,
    )
    monkeypatch.setattr(batch_score_backlog, "log_anthropic", MagicMock())

    assert batch_score_backlog.collect("batch-1") == 0

    update_score_and_status.assert_called_once_with(
        "job-1",
        threshold,
        "Weak match",
        "auto_rejected",
        None,
        "fingerprint-1",
    )
    update_score.assert_not_called()
    assert not manifest_path.exists()


@pytest.mark.parametrize("result_type", ["errored", "expired", "canceled"])
def test_collect_failed_result_removes_manifest_entry(
    monkeypatch, tmp_path, result_type, capsys,
):
    client = _client(monkeypatch)
    client.messages.batches.retrieve.return_value = SimpleNamespace(processing_status="ended")
    client.messages.batches.results.return_value = [_result("job-1", result_type)]
    monkeypatch.setattr(batch_score_backlog, "_MANIFEST_DIR", tmp_path)
    manifest_path = _write_manifest(tmp_path, "batch-1", {"job-1": "fingerprint-1"})
    update_score = MagicMock()
    monkeypatch.setattr(batch_score_backlog.job_repository, "update_score", update_score)
    update_score_and_status = MagicMock()
    monkeypatch.setattr(
        batch_score_backlog.job_repository,
        "update_score_and_status",
        update_score_and_status,
    )

    assert batch_score_backlog.collect("batch-1") == 0

    update_score.assert_not_called()
    update_score_and_status.assert_not_called()
    assert "errored/expired/canceled: 1" in capsys.readouterr().out
    assert not manifest_path.exists()


def test_collect_skips_job_missing_from_manifest(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    client.messages.batches.retrieve.return_value = SimpleNamespace(processing_status="ended")
    client.messages.batches.results.return_value = [
        _result("already-applied", "succeeded", SimpleNamespace(content=[])),
        _result("job-1", "errored"),
    ]
    monkeypatch.setattr(batch_score_backlog, "_MANIFEST_DIR", tmp_path)
    manifest_path = _write_manifest(tmp_path, "batch-1", {"job-1": "fingerprint-1"})
    parse_score_response = MagicMock()
    monkeypatch.setattr(
        batch_score_backlog, "parse_score_response", parse_score_response
    )

    assert batch_score_backlog.collect("batch-1") == 0

    parse_score_response.assert_not_called()
    assert not manifest_path.exists()


def test_collect_leaves_unrecognized_result_type_in_manifest(monkeypatch, tmp_path, capsys):
    client = _client(monkeypatch)
    client.messages.batches.retrieve.return_value = SimpleNamespace(processing_status="ended")
    client.messages.batches.results.return_value = [_result("job-1", "some_future_status")]
    monkeypatch.setattr(batch_score_backlog, "_MANIFEST_DIR", tmp_path)
    manifest_path = _write_manifest(tmp_path, "batch-1", {"job-1": "fingerprint-1"})

    assert batch_score_backlog.collect("batch-1") == 0

    # Left in the manifest for investigation, not silently discarded.
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == {"job-1": "fingerprint-1"}
    assert "1 job(s) still pending" in capsys.readouterr().out
