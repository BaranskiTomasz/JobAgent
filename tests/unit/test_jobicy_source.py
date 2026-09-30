from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

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
