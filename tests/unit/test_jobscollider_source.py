from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from collector.sources.jobscollider import JobsColliderSource


def _job(job_id="job-1", location=None, days_ago=0, title="Senior Python Developer"):
    published = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "id": job_id,
        "url": f"https://jobscollider.com/jobs/{job_id}",
        "company_name": "Acme",
        "title": title,
        "category": "software_development",
        "seniority": "middle",
        "description": "<p>Build APIs</p>",
        "salary_min": 70000,
        "salary_max": 100000,
        "locations": location if location is not None else ["Europe"],
        "published_at": published.isoformat(),
    }


def _source(jobs, days_back=7):
    source = JobsColliderSource(days_back=days_back)
    source._fetch_jobs = MagicMock(return_value=jobs)
    return source


def test_maps_jobscollider_fields_and_native_data():
    result = _source([_job()]).search("Python", "Poland")[0]
    assert result.source == "jobscollider"
    assert result.location == "Europe"
    assert result.description == "Build APIs"
    assert result.source_structured_data == {
        "remote_regions": ["Europe"],
        "seniority": "mid",
        "salary_min": 70000,
        "salary_currency": "USD",
        "salary_period": "yearly",
        "salary_max": 100000,
        "_salary_disclosed": True,
    }


def test_filters_location_date_and_known_urls_but_trusts_api_query_match():
    jobs = [
        _job("known"),
        _job("us", ["United States"]),
        _job("old", days_ago=10),
        _job("wrong-title", title="Product Manager"),
    ]
    result = _source(jobs).search(
        "Python", "Poland", known_urls={"https://jobscollider.com/jobs/known"},
    )
    assert result == []


def test_fetch_jobs_paginates_and_caches():
    first = MagicMock()
    first.json.return_value = {"jobs": [_job(str(i)) for i in range(100)]}
    second = MagicMock()
    second.json.return_value = {"jobs": [_job("last")]}
    source = JobsColliderSource()
    source._client = MagicMock()
    source._client.get.side_effect = [first, second]
    assert len(source._fetch_jobs("Python")) == 101
    assert len(source._fetch_jobs("Python")) == 101
    assert source._client.get.call_count == 2


def test_matches_query_in_description_and_handles_country_objects():
    job = _job(location=[{"country": "Poland"}], title="Software Engineer")
    job["description"] = "Work with Python and Django"
    result = _source([job]).search("Python", "Poland")
    assert len(result) == 1
    assert result[0].location == "Poland"
    assert result[0].source_structured_data["remote_regions"] == ["Poland"]


def test_filters_missing_dates_and_duplicate_urls_and_normalizes_known_url():
    first = _job("first")
    duplicate = dict(first)
    duplicate["id"] = "second"
    duplicate["url"] = first["url"] + "/"
    missing_date = _job("missing")
    missing_date.pop("published_at")
    assert [job.source_id for job in _source([first, duplicate, missing_date]).search("Python", "Poland", known_urls={first["url"] + "/"})] == []


def test_rejects_country_restriction_in_remote_title_for_poland():
    job = _job(title="AI Security Engineer (Remote in the U.S.)")
    job["locations"] = ["Remote"]
    assert _source([job]).search("AI Security Engineer", "Poland") == []


def test_reads_job_location_payload_field_for_geo_filtering():
    job = _job(title="AI Engineer @ Draftline India")
    job.pop("locations")
    job["job_location"] = "India"
    assert _source([job]).search("AI Engineer", "Poland") == []


def test_applies_the_same_geo_restriction_for_bulgaria():
    job = _job(title="AI Security Engineer (Remote in the U.S.)")
    job["locations"] = ["Remote"]
    assert _source([job]).search("AI Security Engineer", "Bulgaria") == []


def test_rejects_dash_separated_us_only_restriction():
    job = _job(title="Senior Software Engineer - Javascript (Remote- US only)")
    job["locations"] = ["Remote"]
    assert _source([job]).search("Software Developer", "Poland") == []


def test_software_query_does_not_admit_pure_security_role():
    job = _job(title="Security Engineer")
    job["description"] = "Protect the platform from threats."
    assert _source([job]).search("Software Developer", "Poland") == []


def test_software_security_role_remains_searchable():
    job = _job(title="Software Security Engineer")
    job["description"] = "Build secure software for our customers."
    assert len(_source([job]).search("Software Developer", "Poland")) == 1


def test_software_query_rejects_application_security_role():
    job = _job(title="Application Security Engineer")
    job["description"] = "Review applications for security vulnerabilities."
    assert _source([job]).search("Software Developer", "Poland") == []


def test_mobile_query_rejects_marketing_and_design_roles():
    marketing = _job(title="User Acquisition Manager - Mobile Apps")
    designer = _job(job_id="designer", title="Senior Product Designer - Mobile")
    assert _source([marketing, designer]).search("Mobile Developer", "Poland") == []


def test_dotnet_query_rejects_devops_role_from_broad_stack_description():
    job = _job(title="DevOps Engineer")
    job["description"] = "Our broad stack includes .NET, Java, Python, Go and Ruby."
    assert _source([job]).search(".NET Developer", "Poland") == []
