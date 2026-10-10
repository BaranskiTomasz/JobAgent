from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import httpx
import pytest

from collector.sources.arbeitnow import _MAX_PAGES, ArbeitnowSource, ArbeitnowUKSource


def _job(slug="job-1", location="", days_ago=0, title="Senior Python Developer", remote=True):
    created = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "slug": slug,
        "company_name": "Acme",
        "title": title,
        "description": "&lt;p&gt;Build APIs&lt;/p&gt;",
        "remote": remote,
        "url": f"https://arbeitnow.example/jobs/{slug}",
        "location": location,
        "created_at": int(created.timestamp()),
    }


@pytest.mark.parametrize(
    ("source_class", "source_name", "job_location"),
    [
        (ArbeitnowSource, "arbeitnow", ""),
        (ArbeitnowUKSource, "arbeitnow_uk", "Europe"),
    ],
)
def test_maps_remote_jobs(source_class, source_name, job_location):
    source = source_class()
    source._fetch_jobs = MagicMock(return_value=[_job(location=job_location)])
    result = source.search("Python", "Poland")[0]
    assert result.source == source_name
    assert result.source_id == "job-1"
    assert result.description == "Build APIs"
    assert result.source_structured_data["remote"] is True
    assert result.source_structured_data["remote_available"] is True


def test_filters_non_remote_title_location_date_and_known_urls():
    source = ArbeitnowSource()
    source._fetch_jobs = MagicMock(return_value=[
        _job("onsite", remote=False),
        _job("wrong-title", title="Product Manager"),
        _job("germany", location="Germany"),
        _job("old", days_ago=10),
        _job("known"),
    ])
    result = source.search(
        "Python", "Poland", known_urls={"https://arbeitnow.example/jobs/known"},
    )
    assert result == []


def test_fetch_jobs_follows_next_even_when_a_page_contains_old_jobs_and_caches():
    recent = [_job("recent")]
    old = [_job("old", days_ago=10)]
    first = MagicMock()
    first.json.return_value = {"data": recent, "links": {"next": "page-2"}}
    second = MagicMock()
    second.json.return_value = {"data": old, "links": {"next": "page-3"}}
    third = MagicMock()
    third.json.return_value = {"data": [], "links": {"next": None}}
    source = ArbeitnowSource(days_back=7)
    source._client = MagicMock()
    source._client.get.side_effect = [first, second, third]
    assert len(source._fetch_jobs()) == 2
    assert len(source._fetch_jobs()) == 2
    assert source._client.get.call_count == 3


def test_fetch_jobs_retries_rate_limit_using_bounded_retry_after():
    limited = MagicMock(status_code=429, headers={"Retry-After": "120"})
    success = MagicMock(status_code=200, headers={})
    success.json.return_value = {"data": [_job("recent")], "links": {"next": None}}
    sleep = MagicMock()
    source = ArbeitnowSource(days_back=7, sleep=sleep)
    source._client = MagicMock()
    source._client.get.side_effect = [limited, success]

    assert len(source._fetch_jobs()) == 1
    assert source._client.get.call_count == 2
    sleep.assert_called_once_with(30.0)


def test_fetch_jobs_reports_error_after_rate_limit_retries_are_exhausted():
    request = httpx.Request("GET", "https://www.arbeitnow.com/api/job-board-api")

    def limited_response():
        response = httpx.Response(429, headers={"Retry-After": "0"}, request=request)
        return response

    sleep = MagicMock()
    source = ArbeitnowSource(days_back=7, sleep=sleep)
    source._client = MagicMock()
    source._client.get.side_effect = [limited_response(), limited_response()]

    assert source._fetch_jobs() == []
    assert source._fetch_diagnostics["source_status"] == "error"
    assert source._fetch_diagnostics["api_complete"] is False
    assert sleep.call_count == 1


def test_fetch_jobs_stops_before_provider_rate_limit_page():
    response = MagicMock()
    response.json.return_value = {"data": [_job("recent")], "links": {"next": "next"}}
    source = ArbeitnowSource(days_back=7)
    source._client = MagicMock()
    source._client.get.return_value = response

    source._fetch_jobs()

    assert source._client.get.call_count == _MAX_PAGES
    assert source._fetch_diagnostics["api_page_limit_reached"] is True
    assert source._fetch_diagnostics["api_complete"] is False


def test_later_page_failure_is_reported_as_partial():
    first = MagicMock()
    first.json.return_value = {
        "data": [_job("recent")],
        "links": {"next": "page-2"},
    }
    request = httpx.Request("GET", "https://www.arbeitnow.com/api/job-board-api")
    source = ArbeitnowSource(days_back=7)
    source._client = MagicMock()
    source._client.get.side_effect = [
        first,
        httpx.ReadTimeout("timeout", request=request),
    ]

    assert len(source._fetch_jobs()) == 1
    assert source._fetch_diagnostics["source_status"] == "partial"
    assert source._fetch_diagnostics["api_complete"] is False


def test_query_matches_description_and_preserves_restricted_location():
    source = ArbeitnowSource()
    source._fetch_jobs = MagicMock(return_value=[_job(location="Poland", title="Backend Engineer", remote=True)])

    result = source.search("Engineer", "Poland")

    assert len(result) == 1
    assert result[0].location == "Poland"
    assert result[0].source_id == "job-1"


def test_remote_tag_is_used_when_api_remote_flag_is_false():
    item = _job(title="Software Engineer", remote=False)
    item["location"] = "Remote"
    source = ArbeitnowSource()
    source._fetch_jobs = MagicMock(return_value=[item])

    assert len(source.search("Software", "Poland")) == 1


def test_missing_date_is_excluded_when_cutoff_is_active():
    item = _job()
    item["created_at"] = None
    source = ArbeitnowSource()
    source._fetch_jobs = MagicMock(return_value=[item])

    assert source.search("Python", "Poland", days_back=7) == []


def test_duplicate_urls_and_query_variants_are_collapsed():
    first = _job("first")
    second = _job("second")
    second["url"] = first["url"] + "?utm_source=feed"
    source = ArbeitnowSource()
    source._fetch_jobs = MagicMock(return_value=[first, second])

    result = source.search("Python", "Poland")

    assert len(result) == 1


@pytest.mark.parametrize(
    "location",
    ["United Kingdom", "Remote United Kingdom", "Remote (UK hours)", "London"],
)
@pytest.mark.parametrize("candidate", ["Poland", "Bulgaria"])
def test_uk_source_rejects_uk_restricted_remote_jobs(location, candidate):
    source = ArbeitnowUKSource()
    source._fetch_jobs = MagicMock(return_value=[_job(location=location)])

    assert source.search("Python", candidate) == []


@pytest.mark.parametrize("location", ["Europe", "Worldwide", "Poland", "Bulgaria"])
def test_uk_source_accepts_explicit_broad_remote_regions(location):
    source = ArbeitnowUKSource()
    item = _job(location=location)
    item["visa_sponsorship"] = True
    source._fetch_jobs = MagicMock(return_value=[item])

    candidate = "Bulgaria" if location == "Bulgaria" else "Poland"
    result = source.search("Python", candidate)

    assert len(result) == 1
    assert result[0].source_structured_data["visa_sponsorship"] is True


def test_uk_source_rejects_ambiguous_remote_location():
    source = ArbeitnowUKSource()
    source._fetch_jobs = MagicMock(return_value=[_job(location="Remote job")])

    assert source.search("Python", "Poland") == []
