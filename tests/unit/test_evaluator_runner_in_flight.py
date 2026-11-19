import json
from datetime import datetime, timedelta, timezone

from evaluator import runner as evaluator_runner


def _set_in_flight_path(monkeypatch, tmp_path):
    path = tmp_path / "_in_flight.json"
    monkeypatch.setattr(evaluator_runner, "_IN_FLIGHT_PATH", path)
    return path


def test_load_in_flight_drops_entry_older_than_ttl(monkeypatch, tmp_path):
    path = _set_in_flight_path(monkeypatch, tmp_path)
    stale_at = (datetime.now(timezone.utc) - evaluator_runner._IN_FLIGHT_TTL - timedelta(hours=1)).isoformat()
    path.write_text(json.dumps({
        "stale-job": {"batch_id": "batch-old", "submitted_at": stale_at},
    }), encoding="utf-8")

    result = evaluator_runner.load_in_flight()

    assert result == {}
    # The prune is persisted, not just filtered in memory.
    assert json.loads(path.read_text(encoding="utf-8")) == {}


def test_load_in_flight_keeps_entry_within_ttl(monkeypatch, tmp_path):
    path = _set_in_flight_path(monkeypatch, tmp_path)
    fresh_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    on_disk = {"fresh-job": {"batch_id": "batch-new", "submitted_at": fresh_at}}
    path.write_text(json.dumps(on_disk), encoding="utf-8")

    result = evaluator_runner.load_in_flight()

    assert result == on_disk
    # Nothing to prune, so the file is left untouched.
    assert json.loads(path.read_text(encoding="utf-8")) == on_disk


def test_load_in_flight_missing_file_returns_empty(monkeypatch, tmp_path):
    _set_in_flight_path(monkeypatch, tmp_path)

    assert evaluator_runner.load_in_flight() == {}


def test_load_in_flight_fails_open_on_malformed_file(monkeypatch, tmp_path):
    path = _set_in_flight_path(monkeypatch, tmp_path)
    path.write_text("{not valid json", encoding="utf-8")

    # Must not raise: a corrupt local state file should never block scoring.
    assert evaluator_runner.load_in_flight() == {}
