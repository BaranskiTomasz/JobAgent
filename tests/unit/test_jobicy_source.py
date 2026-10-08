from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import httpx
import pytest

from collector.sources.jobicy import JobicySource


def _job(job_id=1, location="Europe", days_ago=0, title="Senior Python Developer"):
    published = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "id": job_id,
        "url": f"https://jobicy.com/jobs/{job_id}",
        "jobTitle": title,
        "companyName": "Acme",
        "jobGeo": location,
        "jobLevel": "Senior",
        "jobDescription": "<p>Build APIs</p>",
        "pubDate": published.isoformat(),
        "salaryMin": 90000,
        "salaryMax": 120000,
        "salaryCurrency": "EUR",
        "salaryPeriod": "yearly",
    }


def _source(jobs, days_back=7):
    source = JobicySource(days_back=days_back)
    source._fetch_jobs = MagicMock(return_value=jobs)
    return source


def test_maps_jobicy_fields_and_native_data():
    result = _source([_job()]).search("Python", "Poland")[0]
    assert result.source == "jobicy"
    assert result.source_id == "1"
    assert result.description == "Build APIs"
    assert result.source_structured_data == {
        "remote": True,
        "remote_available": True,
        "remote_regions": ["Europe"],
        "seniority": "senior",
        "salary_min": 90000,
        "salary_max": 120000,
        "salary_currency": "EUR",
        "salary_period": "yearly",
        "_salary_disclosed": True,
    }


def test_filters_location_date_known_urls_and_limit():
    jobs = [
        _job(1),
        _job(2, location="USA Only"),
        _job(3, days_ago=10),
        _job(4),
    ]
    result = _source(jobs).search(
        "Python", "Poland", max_results=1, known_urls={"https://jobicy.com/jobs/1"},
    )
    assert [job.source_id for job in result] == ["4"]


def test_fetch_jobs_is_cached_per_title():
    source = JobicySource()
    response = MagicMock()
    response.json.return_value = {"jobs": [_job()]}
    source._client = MagicMock()
    source._client.get.return_value = response
    assert source._fetch_jobs("Python") == source._fetch_jobs("Python")
    source._client.get.assert_called_once()


def test_qa_fetch_uses_unfiltered_feed_when_api_rejects_qa_tag():
    source = JobicySource()
    response = MagicMock()
    response.json.return_value = {"jobs": []}
    source._client = MagicMock()
    source._client.get.return_value = response

    source._fetch_jobs("qa")

    source._client.get.assert_called_once_with(
        "https://jobicy.com/api/v2/remote-jobs", params={"count": 200},
    )


def test_fetch_follows_cursor_and_caches_complete_feed():
    source = JobicySource()
    first = MagicMock()
    first.json.return_value = {"jobs": [_job(1)], "nextCursor": "opaque-token"}
    second = MagicMock()
    second.json.return_value = {"jobs": [_job(2)], "nextCursor": None}
    source._client = MagicMock()
    source._client.get.side_effect = [first, second]

    jobs = source._fetch_jobs("Python")

    assert [job["id"] for job in jobs] == [1, 2]
    assert source._client.get.call_count == 2
    assert source._client.get.call_args_list[1].kwargs["params"] == {
        "count": 200,
        "tag": "Python",
        "cursor": "opaque-token",
    }
    assert source._fetch_jobs("python") is jobs
    assert source._client.get.call_count == 2


def test_fetch_diagnostics_are_isolated_per_tag():
    source = JobicySource()
    partial = MagicMock()
    partial.json.return_value = {"jobs": [_job(1)], "hasMore": True}
    complete = MagicMock()
    complete.json.return_value = {"jobs": [_job(2)], "hasMore": False}
    source._client = MagicMock()
    source._client.get.side_effect = [partial, complete]

    source._fetch_jobs("Python")
    assert source._fetch_diagnostics["source_status"] == "partial"
    source._fetch_jobs("Java")
    assert "source_status" not in source._fetch_diagnostics
    source._fetch_jobs("Python")
    assert source._fetch_diagnostics["source_status"] == "partial"
    assert source._client.get.call_count == 2


def test_search_reports_funnel_and_matches_short_query_locally():
    source = _source([_job(title="QA Engineer"), _job(2, title="Python Developer")])

    result = source.search("QA", "Poland")

    assert [job.source_id for job in result] == ["1"]
    assert source.last_search_diagnostics == {
        "upstream_found": 2,
        "query_matched": 1,
        "date_matched": 1,
        "geo_matched": 1,
        "known_url_filtered": 0,
        "source_status": "ok",
    }


def test_http_errors_are_not_mistaken_for_empty_source():
    source = JobicySource()
    source._client = MagicMock()
    request = httpx.Request("GET", "https://jobicy.com/api/v2/remote-jobs")
    source._client.get.side_effect = httpx.ConnectError("offline", request=request)

    with pytest.raises(httpx.ConnectError):
        source.search("Python", "Poland")
