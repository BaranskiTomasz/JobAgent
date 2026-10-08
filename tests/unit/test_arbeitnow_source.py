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
    ("source_class", "source_name"),
    [(ArbeitnowSource, "arbeitnow"), (ArbeitnowUKSource, "arbeitnow_uk")],
)
def test_maps_remote_jobs(source_class, source_name):
    source = source_class()
    source._fetch_jobs = MagicMock(return_value=[_job()])
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


def test_fetch_jobs_paginates_until_cutoff_and_caches():
    recent = [_job("recent")]
    old = [_job("old", days_ago=10)]
    first = MagicMock()
    first.json.return_value = {"data": recent, "links": {"next": "page-2"}}
    second = MagicMock()
    second.json.return_value = {"data": old, "links": {"next": "page-3"}}
    source = ArbeitnowSource(days_back=7)
    source._client = MagicMock()
    source._client.get.side_effect = [first, second]
    assert len(source._fetch_jobs()) == 2
    assert len(source._fetch_jobs()) == 2
    assert source._client.get.call_count == 2


def test_fetch_jobs_stops_before_provider_rate_limit_page():
    response = MagicMock()
    response.json.return_value = {"data": [_job("recent")], "links": {"next": "next"}}
    source = ArbeitnowSource(days_back=7)
    source._client = MagicMock()
    source._client.get.return_value = response

    source._fetch_jobs()

    assert source._client.get.call_count == _MAX_PAGES


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
