from datetime import datetime, timezone
from unittest.mock import MagicMock

from collector.base import RawJob
from collector.sources.ashby import AshbySource
from collector.sources.ats import _eligible_remote
from collector.sources.greenhouse import GreenhouseSource
from collector.sources.lever import LeverSource


def _response(payload):
    response = MagicMock()
    response.json.return_value = payload
    return response


def test_remote_eligibility_requires_remote_and_poland_compatible_region():
    assert _eligible_remote("Remote - Europe", "", "Poland")
    assert _eligible_remote("Remote", "We hire remotely across EMEA", "Poland")
    assert _eligible_remote("Poland", "This is a remote role", "Poland")
    assert not _eligible_remote("Remote - United States", "US applicants only", "Poland")
    assert not _eligible_remote("Remote", "Remote within the US", "Poland")
    assert not _eligible_remote("Berlin", "Hybrid position", "Poland")


def test_greenhouse_maps_public_board_response():
    source = GreenhouseSource()
    source._client = MagicMock()
    source._client.get.return_value = _response({"jobs": [{
        "id": 42,
        "title": "Python Engineer",
        "company_name": "Acme",
        "location": {"name": "Remote - Europe"},
        "absolute_url": "https://boards.greenhouse.io/acme/jobs/42",
        "first_published": datetime.now(timezone.utc).isoformat(),
        "content": "&lt;p&gt;Build APIs&lt;/p&gt;",
    }]})
    result = source._fetch_board({"name": "Acme", "slug": "acme"})[0]
    assert result.source == "greenhouse"
    assert result.source_id == "acme:42"
    assert result.description == "Build APIs"
    assert result.source_structured_data == {
        "remote": True,
        "remote_available": True,
        "remote_regions": ["Remote - Europe"],
    }


def test_greenhouse_falls_back_to_canonical_url_without_board_job_id():
    source = GreenhouseSource()
    source._client = MagicMock()
    url = "https://boards.greenhouse.io/acme/jobs/no-id"
    source._client.get.return_value = _response({"jobs": [{
        "title": "Python Engineer",
        "company_name": "Acme",
        "location": {"name": "Remote - Europe"},
        "absolute_url": url,
        "first_published": datetime.now(timezone.utc).isoformat(),
        "content": "Build APIs",
    }]})
    result = source._fetch_board({"name": "Acme", "slug": "acme"})[0]
    assert result.source_id == url


def test_greenhouse_reports_failed_boards_and_filters_canonical_known_urls():
    source = GreenhouseSource()
    source._boards = [{"name": "Acme", "slug": "acme"}, {"name": "Broken", "slug": "broken"}]
    source._client = MagicMock()
    url = "https://boards.greenhouse.io/acme/jobs/42/"
    response = _response({"jobs": [{
        "id": 42,
        "title": "Python Engineer",
        "company_name": "Acme",
        "location": {"name": "Remote - Europe"},
        "absolute_url": url,
        "first_published": datetime.now(timezone.utc).isoformat(),
        "content": "Remote Europe",
    }]})
    source._client.get.side_effect = [response, RuntimeError("upstream unavailable")]
    assert source.search("Python", "Poland", known_urls={url.rstrip("/")}) == []
    assert source.last_search_diagnostics["boards_failed"] == 1
    assert source.last_search_diagnostics["source_status"] == "partial"
    assert "broken" in source.last_search_diagnostics["source_error"]


def test_greenhouse_does_not_promote_description_only_remote_to_source_fact():
    source = GreenhouseSource()
    source._client = MagicMock()
    source._client.get.return_value = _response({"jobs": [{
        "id": 42,
        "title": "Python Engineer",
        "company_name": "Acme",
        "location": {"name": "Berlin"},
        "absolute_url": "https://boards.greenhouse.io/acme/jobs/42",
        "first_published": datetime.now(timezone.utc).isoformat(),
        "content": "Work from home may be available.",
    }]})

    result = source._fetch_board({"name": "Acme", "slug": "acme"})[0]

    assert result.source_structured_data is None


def test_lever_maps_public_board_response():
    source = LeverSource()
    source._client = MagicMock()
    source._client.get.return_value = _response([{
        "id": "job-42",
        "text": "Python Engineer",
        "categories": {"location": "Remote - Europe", "allLocations": ["Remote - Europe"]},
        "createdAt": int(datetime.now(timezone.utc).timestamp() * 1000),
        "hostedUrl": "https://jobs.lever.co/acme/job-42",
        "descriptionPlain": "Build APIs",
        "lists": [{"text": "Requirements", "content": "Python"}],
    }])
    result = source._fetch_board({"name": "Acme", "slug": "acme"})[0]
    assert result.source == "lever"
    assert result.source_id == "acme:job-42"
    assert result.source_structured_data == {"remote": True, "remote_regions": ["Remote - Europe"]}


def test_ashby_maps_public_board_response():
    source = AshbySource()
    source._client = MagicMock()
    source._client.get.return_value = _response({"jobs": [{
        "id": "job-42",
        "title": "Python Engineer",
        "location": "Europe",
        "publishedAt": datetime.now(timezone.utc).isoformat(),
        "jobUrl": "https://jobs.ashbyhq.com/acme/job-42",
        "descriptionHtml": "<p>Build APIs</p>",
        "isListed": True,
        "isRemote": True,
        "secondaryLocations": [],
    }]})
    result = source._fetch_board({"name": "Acme", "slug": "acme"})[0]
    assert result.source == "ashby"
    assert result.description == "Build APIs"
    assert result.source_structured_data == {"remote": True, "remote_regions": ["Europe"]}


def test_ats_search_filters_title_date_location_and_known_url():
    source = GreenhouseSource()
    now = datetime.now(timezone.utc).isoformat()
    source._fetch_jobs = MagicMock(return_value=[
        RawJob("Python Engineer", "A", "Remote - Europe", "https://a/1", "greenhouse", description="Remote Europe", posted_at=now),
        RawJob("Python Engineer", "B", "Remote - US", "https://b/1", "greenhouse", description="US only", posted_at=now),
        RawJob("Product Manager", "C", "Remote - Europe", "https://c/1", "greenhouse", description="Remote Europe", posted_at=now),
    ])
    result = source.search("Python", "Poland", known_urls={"https://c/1"})
    assert [job.url for job in result] == ["https://a/1"]
