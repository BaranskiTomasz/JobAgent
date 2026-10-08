from datetime import datetime, timezone
from unittest.mock import MagicMock

from collector.sources.lever import LeverSource


def _response(payload):
    response = MagicMock()
    response.json.return_value = payload
    return response


def _row(i, location="Remote - Europe"):
    return {
        "id": f"job-{i}", "text": "Python Engineer", "categories": {"allLocations": [location]},
        "createdAt": int(datetime.now(timezone.utc).timestamp() * 1000),
        "hostedUrl": f"https://jobs.lever.co/acme/job-{i}", "descriptionPlain": "Build APIs remotely",
    }


def test_paginates_full_board_and_preserves_stable_ids():
    source = LeverSource()
    source._client = MagicMock()
    source._client.get.side_effect = [_response([_row(i) for i in range(100)]), _response([_row(100)])]
    jobs = source._fetch_board({"name": "Acme", "slug": "acme"})
    assert len(jobs) == 101
    assert jobs[-1].source_id == "acme:job-100"
    assert source._client.get.call_args_list[1].kwargs["params"]["skip"] == 100


def test_fallback_id_and_canonical_known_url_filter():
    source = LeverSource()
    source._client = MagicMock()
    row = _row(1)
    row.pop("id")
    row["hostedUrl"] += "/?source=career"
    source._fetch_jobs = MagicMock(return_value=source._fetch_board({"name": "Acme", "slug": "acme"}))
    assert source.search("Python", "Poland", known_urls={"https://jobs.lever.co/acme/job-1/"}) == []
    assert source._client.get.called


def test_remote_facts_accept_workplace_type_and_poland_bulgaria_geo():
    source = LeverSource()
    source._client = MagicMock()
    row = _row(2, "Poland, Bulgaria")
    row["categories"] = {"location": "Poland, Bulgaria"}
    row["workplaceType"] = "remote"
    source._client.get.return_value = _response([row])
    job = source._fetch_board({"name": "Acme", "slug": "acme"})[0]
    assert job.source_structured_data == {
        "remote": True,
        "remote_available": True,
        "remote_regions": ["Poland, Bulgaria"],
    }
    source._fetch_jobs = MagicMock(return_value=[job])
    assert source.search("Python", "Poland") == [job]
    assert source.search("Python", "Bulgaria") == [job]


def test_failed_board_is_reported_in_diagnostics():
    source = LeverSource()
    source._client = MagicMock()
    source._client.get.side_effect = RuntimeError("rate limited")
    source._boards = [{"name": "Acme", "slug": "acme"}]
    assert source.search("Python", "Poland") == []
    assert source.last_search_diagnostics["source_status"] == "error"
    assert "rate limited" in source.last_search_diagnostics["source_error"]


def test_later_page_failure_preserves_first_page_and_is_partial():
    source = LeverSource()
    source._client = MagicMock()
    first = [_row(i) for i in range(100)]
    source._client.get.side_effect = [
        _response(first),
        RuntimeError("second page failed"),
    ]

    jobs = source._fetch_board({"name": "Acme", "slug": "acme"})

    assert len(jobs) == 100
    assert "acme" in source._board_errors


def test_description_only_remote_text_is_not_a_native_fact():
    source = LeverSource()
    source._client = MagicMock()
    row = _row(1, "Berlin")
    row["descriptionPlain"] = "Occasional remote work may be available."
    source._client.get.return_value = _response([row])
    job = source._fetch_board({"name": "Acme", "slug": "acme"})[0]

    assert job.source_structured_data is None
