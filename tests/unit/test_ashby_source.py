from datetime import datetime, timezone
from unittest.mock import MagicMock

from collector.sources.ashby import AshbySource


def _response(payload):
    response = MagicMock()
    response.json.return_value = payload
    return response


def _row(job_id="job-1", location="Remote - Europe"):
    return {
        "id": job_id, "title": "Python Engineer", "location": location,
        "publishedAt": datetime.now(timezone.utc).isoformat(),
        "jobUrl": f"https://jobs.ashbyhq.com/acme/{job_id}",
        "descriptionHtml": "<p>Build APIs</p>", "isListed": True,
    }


def test_maps_schema_and_native_compensation():
    source = AshbySource()
    source._client = MagicMock()
    row = _row()
    row["isRemote"] = True
    row["compensation"] = {"summaryComponents": [{
        "compensationType": "Salary",
        "minValue": 100,
        "maxValue": 150,
        "currencyCode": "USD",
        "interval": "1 YEAR",
    }]}
    source._client.get.return_value = _response({"jobs": [row]})
    job = source._fetch_board({"name": "Acme", "slug": "acme"})[0]
    assert job.source_id == "acme:job-1"
    assert job.description == "Build APIs"
    assert job.source_structured_data["remote_regions"] == ["Remote - Europe"]
    assert job.source_structured_data["salary_min"] == 100
    assert job.source_structured_data["salary_period"] == "yearly"


def test_supports_secondary_locations_and_poland_bulgaria():
    source = AshbySource()
    source._client = MagicMock()
    row = _row(location="Poland")
    row["secondaryLocations"] = [{"location": "Bulgaria"}]
    row["workplaceType"] = "remote"
    source._client.get.return_value = _response({"jobs": [row]})
    job = source._fetch_board({"name": "Acme", "slug": "acme"})[0]
    assert job.location == "Remote - Poland, Bulgaria"
    source._fetch_jobs = MagicMock(return_value=[job])
    assert source.search("Python", "Poland") == [job]
    assert source.search("Python", "Bulgaria") == [job]


def test_known_url_filter_uses_canonical_key_and_fallback_id():
    source = AshbySource()
    source._client = MagicMock()
    row = _row()
    row.pop("id")
    row["jobUrl"] += "?source=career"
    source._client.get.return_value = _response({"jobs": [row]})
    job = source._fetch_board({"name": "Acme", "slug": "acme"})[0]
    assert job.source_id == "https://jobs.ashbyhq.com/acme/job-1"
    source._fetch_jobs = MagicMock(return_value=[job])
    assert source.search("Python", "Poland", known_urls={"https://jobs.ashbyhq.com/acme/job-1/"}) == []


def test_failed_board_is_visible_in_diagnostics():
    source = AshbySource()
    source._client = MagicMock()
    source._client.get.side_effect = RuntimeError("upstream unavailable")
    source._boards = [{"name": "Acme", "slug": "acme"}]
    assert source.search("Python", "Poland") == []
    assert source.last_search_diagnostics["source_status"] == "error"
    assert "upstream unavailable" in source.last_search_diagnostics["source_error"]


def test_remote_address_country_is_used_as_eligibility_region():
    source = AshbySource()
    source._client = MagicMock()
    row = _row(location="Remote")
    row["isRemote"] = True
    row["address"] = {"postalAddress": {"addressCountry": "United States"}}
    source._client.get.return_value = _response({"jobs": [row]})

    job = source._fetch_board({"name": "Acme", "slug": "acme"})[0]

    assert job.location == "Remote - United States"
    assert job.source_structured_data["remote_regions"] == ["United States"]
    source._fetch_jobs = MagicMock(return_value=[job])
    assert source.search("Python", "Poland") == []


def test_description_only_remote_text_is_not_a_native_fact():
    source = AshbySource()
    source._client = MagicMock()
    row = _row(location="Berlin")
    row["descriptionHtml"] = "<p>Remote work may occasionally be available.</p>"
    source._client.get.return_value = _response({"jobs": [row]})

    job = source._fetch_board({"name": "Acme", "slug": "acme"})[0]

    assert job.source_structured_data is None
