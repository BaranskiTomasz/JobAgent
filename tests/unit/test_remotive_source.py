"""Unit tests for the Remotive.io scraper, no HTTP calls made."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import httpx
import pytest

from collector.sources.remotive import RemotiveSource
from collector.location import location_matches as _location_matches
from collector.utils import strip_html as _strip_html


# ── _location_matches ──────────────────────────────────────────────────────────


class TestLocationMatches:
    def test_remote_matches_all(self):
        assert _location_matches("Poland Only", "Remote")

    def test_worldwide_matches_any_country(self):
        assert _location_matches("Worldwide", "Germany")

    def test_anywhere_matches_any_country(self):
        assert _location_matches("Anywhere", "France")

    def test_global_matches_any_country(self):
        assert _location_matches("Global", "Canada")

    def test_europe_matches_eu_country(self):
        assert _location_matches("Europe", "Poland")

    def test_europe_matches_norway(self):
        assert _location_matches("Europe", "Norway")

    def test_europe_does_not_match_us(self):
        assert not _location_matches("Europe", "United States")

    def test_emea_matches_eu_country(self):
        assert _location_matches("EMEA", "Germany")

    def test_north_america_matches_us(self):
        assert _location_matches("North America", "United States")

    def test_north_america_matches_canada(self):
        assert _location_matches("USA/Canada", "Canada")

    def test_north_america_does_not_match_poland(self):
        assert not _location_matches("North America", "Poland")

    def test_direct_country_match(self):
        assert _location_matches("Poland, Ukraine", "Poland")

    def test_alias_uk(self):
        assert _location_matches("United Kingdom, Ireland", "UK")

    def test_alias_usa(self):
        assert _location_matches("USA, Canada", "United States")

    def test_no_match_different_region(self):
        assert not _location_matches("USA Only", "Germany")

    def test_case_insensitive(self):
        assert _location_matches("POLAND", "poland")


# ── _strip_html ────────────────────────────────────────────────────────────────


class TestStripHtml:
    def test_strips_tags(self):
        assert _strip_html("<p>Hello</p>") == "Hello"

    def test_none_on_empty(self):
        assert _strip_html("") is None

    def test_none_on_whitespace_only(self):
        assert _strip_html("<p>  </p>") is None

    def test_br_to_newline(self):
        text = _strip_html("Line1<br>Line2")
        assert "Line1" in text and "Line2" in text


# ── RemotiveSource.search() ────────────────────────────────────────────────────


def _make_job(
    job_id: int = 1,
    title: str = "Senior PHP Developer",
    company: str = "Acme",
    candidate_location: str = "Worldwide",
    days_ago: int = 0,
    description: str = "<p>Great role</p>",
) -> dict:
    pub = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S")
    return {
        "id": job_id,
        "url": f"https://remotive.com/remote-jobs/software-dev/job-{job_id}",
        "title": title,
        "company_name": company,
        "candidate_required_location": candidate_location,
        "publication_date": pub,
        "description": description,
    }


def _build_source(jobs: list[dict], days_back: int = 7) -> RemotiveSource:
    src = RemotiveSource(days_back=days_back)
    src._fetch_jobs = MagicMock(return_value=jobs)
    return src


class TestRemotiveSourceSearch:
    def test_returns_matching_job(self):
        src = _build_source([_make_job()])
        results = src.search("PHP Developer", "Remote")
        assert len(results) == 1
        assert results[0].title == "Senior PHP Developer"
        assert results[0].source == "remotive"

    def test_source_id_is_job_id(self):
        src = _build_source([_make_job(job_id=42)])
        results = src.search("PHP Developer", "Remote")
        assert results[0].source_id == "42"

    def test_url_preserved(self):
        src = _build_source([_make_job(job_id=7)])
        results = src.search("PHP Developer", "Remote")
        assert results[0].url == "https://remotive.com/remote-jobs/software-dev/job-7"

    def test_filters_by_date(self):
        fresh = _make_job(job_id=1, days_ago=1)
        old   = _make_job(job_id=2, days_ago=10)
        src = _build_source([fresh, old], days_back=7)
        results = src.search("PHP Developer", "Remote")
        assert len(results) == 1
        assert results[0].source_id == "1"

    def test_filters_by_location_europe(self):
        eu  = _make_job(job_id=1, candidate_location="Europe")
        us  = _make_job(job_id=2, candidate_location="USA Only")
        src = _build_source([eu, us])
        results = src.search("PHP Developer", "Poland")
        assert len(results) == 1
        assert results[0].source_id == "1"

    def test_worldwide_matches_any_location(self):
        job = _make_job(candidate_location="Worldwide")
        src = _build_source([job])
        results = src.search("PHP Developer", "Austria")
        assert len(results) == 1

    def test_skips_known_urls(self):
        job = _make_job(job_id=5)
        src = _build_source([job])
        known = {"https://remotive.com/remote-jobs/software-dev/job-5"}
        results = src.search("PHP Developer", "Remote", known_urls=known)
        assert results == []

    def test_respects_max_results(self):
        jobs = [_make_job(job_id=i) for i in range(10)]
        src = _build_source(jobs)
        results = src.search("PHP Developer", "Remote", max_results=3)
        assert len(results) == 3

    def test_description_stripped(self):
        src = _build_source([_make_job(description="<p>Good <b>role</b></p>")])
        results = src.search("PHP Developer", "Remote")
        assert results[0].description == "Good role"

    def test_empty_api_response(self):
        src = _build_source([])
        results = src.search("PHP Developer", "Remote")
        assert results == []

    def test_skips_job_without_url(self):
        job = _make_job()
        job["url"] = ""
        src = _build_source([job])
        results = src.search("PHP Developer", "Remote")
        assert results == []

    def test_location_fallback_to_remote(self):
        job = _make_job(candidate_location="")
        src = _build_source([job])
        results = src.search("PHP Developer", "Remote")
        assert results[0].location == "Remote"

    def test_company_extracted(self):
        src = _build_source([_make_job(company="GlobalTech")])
        results = src.search("PHP Developer", "Remote")
        assert results[0].company == "GlobalTech"

    def test_fetch_jobs_uses_shared_feed(self):
        src = _build_source([])
        src.search("React Developer", "Remote")
        src._fetch_jobs.assert_called_once_with()

    def test_broad_query_matches_specialized_title(self):
        src = _build_source([_make_job(title="Senior Backend Engineer")])
        assert len(src.search("Software Engineer", "Remote")) == 1

    def test_native_remote_facts_are_preserved(self):
        src = _build_source([_make_job(candidate_location="Europe")])
        result = src.search("PHP Developer", "Poland")[0]
        assert result.source_structured_data == {
            "remote": True,
            "remote_available": True,
            "remote_regions": ["Europe"],
        }

    def test_noisy_tags_do_not_create_a_technology_match(self):
        job = _make_job(title="Senior Data Scientist", description="Build forecasting models")
        job["tags"] = ["PHP", "Java", "React"]
        src = _build_source([job])
        assert src.search("PHP Developer", "Remote") == []

    def test_records_search_funnel(self):
        src = _build_source([
            _make_job(job_id=1),
            _make_job(job_id=2, days_ago=20),
            _make_job(job_id=3, candidate_location="USA Only"),
        ])
        src.search("PHP Developer", "Poland")
        assert src.last_search_diagnostics == {
            "upstream_found": 3,
            "query_matched": 3,
            "date_matched": 2,
            "geo_matched": 1,
        }

    def test_posted_at_captures_the_publication_date(self):
        # publication_date was already parsed for the days_back cutoff, then
        # discarded, RawJob.posted_at carries it through instead.
        src = _build_source([_make_job(job_id=1, days_ago=2)])
        results = src.search("PHP Developer", "Remote")
        assert results[0].posted_at is not None
        posted = datetime.fromisoformat(results[0].posted_at)
        assert (datetime.now(timezone.utc) - posted).days == 2


class TestRemotiveFetch:
    def test_feed_is_downloaded_once_for_multiple_queries(self):
        response = MagicMock()
        response.json.return_value = {"jobs": []}
        response.raise_for_status.return_value = None
        source = RemotiveSource()
        source._client = MagicMock()
        source._client.get.return_value = response

        source.search("Python Developer", "Poland")
        source.search("Java Developer", "Poland")

        source._client.get.assert_called_once_with("https://remotive.com/api/remote-jobs")

    def test_http_errors_are_visible_to_the_runner(self):
        source = RemotiveSource()
        source._client = MagicMock()
        response = MagicMock()
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "rate limited", request=MagicMock(), response=MagicMock(),
        )
        source._client.get.return_value = response

        with pytest.raises(httpx.HTTPStatusError):
            source.search("Python Developer", "Poland")
