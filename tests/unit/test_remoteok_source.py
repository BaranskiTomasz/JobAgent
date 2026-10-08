"""Unit tests for Remote OK source, no HTTP calls made."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import httpx
import pytest

from collector.sources.remoteok import RemoteOKSource, _location_is_eligible, _tag_for_query
from collector.location import location_matches as _location_matches
from collector.utils import strip_html as _strip_html


# ── _location_matches ──────────────────────────────────────────────────────────


class TestLocationMatches:
    def test_remote_matches_all(self):
        assert _location_matches("USA Only", "Remote")

    def test_empty_location_matches_all(self):
        assert _location_matches("", "Poland")

    def test_worldwide_matches_any_country(self):
        assert _location_matches("Worldwide", "Germany")

    def test_anywhere_matches_any_country(self):
        assert _location_matches("Anywhere", "France")

    def test_europe_matches_eu_country(self):
        assert _location_matches("Europe", "Poland")

    def test_emea_matches_eu_country(self):
        assert _location_matches("EMEA", "Germany")

    def test_europe_does_not_match_us(self):
        assert not _location_matches("Europe Only", "United States")

    def test_usa_only_does_not_match_poland(self):
        assert not _location_matches("USA Only", "Poland")

    def test_direct_country_match(self):
        assert _location_matches("Poland, Ukraine", "Poland")

    def test_alias_uk(self):
        assert _location_matches("United Kingdom Only", "UK")

    def test_case_insensitive(self):
        assert _location_matches("POLAND", "poland")


# ── _strip_html ────────────────────────────────────────────────────────────────


class TestStripHtml:
    def test_strips_tags(self):
        assert _strip_html("<p>Hello</p>") == "Hello"

    def test_none_on_empty(self):
        assert _strip_html("") is None

    def test_br_to_newline(self):
        text = _strip_html("Line1<br>Line2")
        assert "Line1" in text and "Line2" in text


# ── RemoteOKSource.search() ───────────────────────────────────────────────────


def _make_job(
    slug: str = "php-dev-1",
    position: str = "Senior PHP Developer",
    company: str = "Acme",
    location: str = "Worldwide",
    days_ago: int = 0,
    tags: list[str] | None = None,
    description: str = "<p>Great role</p>",
) -> dict:
    pub = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    return {
        "slug": slug,
        "url": f"https://remoteok.io/remote-jobs/{slug}",
        "position": position,
        "company": company,
        "location": location,
        "date": pub,
        "tags": tags if tags is not None else ["php", "backend"],
        "description": description,
    }


def _build_source(jobs: list[dict], days_back: int = 7) -> RemoteOKSource:
    src = RemoteOKSource(days_back=days_back)
    src._fetch_jobs = MagicMock(return_value=jobs)
    return src


class TestRemoteOKSourceSearch:
    def test_returns_matching_job(self):
        src = _build_source([_make_job()])
        results = src.search("PHP", "Remote")
        assert len(results) == 1
        assert results[0].title == "Senior PHP Developer"
        assert results[0].source == "remoteok"

    def test_source_id_is_slug(self):
        src = _build_source([_make_job(slug="php-dev-42")])
        results = src.search("PHP", "Remote")
        assert results[0].source_id == "php-dev-42"

    def test_url_preserved(self):
        src = _build_source([_make_job(slug="abc")])
        results = src.search("PHP", "Remote")
        assert results[0].url == "https://remoteok.io/remote-jobs/abc"

    def test_filters_by_date(self):
        fresh = _make_job(slug="new", days_ago=1)
        old   = _make_job(slug="old", days_ago=10)
        src = _build_source([fresh, old], days_back=7)
        results = src.search("PHP", "Remote")
        assert len(results) == 1
        assert results[0].source_id == "new"

    def test_filters_by_keyword_in_position(self):
        php = _make_job(slug="1", position="PHP Developer", tags=[])
        py  = _make_job(slug="2", position="Python Developer", tags=[])
        src = _build_source([php, py])
        results = src.search("PHP", "Remote")
        assert len(results) == 1
        assert results[0].source_id == "1"

    def test_broad_query_matches_specialized_title(self):
        job = _make_job(position="Senior Backend Engineer")
        src = _build_source([job])
        assert len(src.search("Software Engineer", "Remote")) == 1

    def test_does_not_match_keyword_only_in_tags(self):
        # RemoteOK tags are too noisy (generic categories applied to all jobs),
        # so only title is used for keyword filtering.
        job = _make_job(slug="1", position="Backend Developer", tags=["php", "laravel"])
        src = _build_source([job])
        results = src.search("PHP", "Remote")
        assert results == []

    def test_excludes_job_with_no_keyword_match(self):
        job = _make_job(slug="1", position="Python Developer", tags=["python", "django"])
        src = _build_source([job])
        results = src.search("PHP", "Remote")
        assert results == []

    def test_filters_by_location(self):
        eu  = _make_job(slug="1", location="Europe")
        us  = _make_job(slug="2", location="USA Only")
        src = _build_source([eu, us])
        results = src.search("PHP", "Poland")
        assert len(results) == 1
        assert results[0].source_id == "1"

    def test_empty_location_matches_all(self):
        job = _make_job(location="")
        src = _build_source([job])
        results = src.search("PHP", "Poland")
        assert len(results) == 1

    def test_skips_known_urls(self):
        job = _make_job(slug="php-1")
        src = _build_source([job])
        known = {"https://remoteok.io/remote-jobs/php-1"}
        results = src.search("PHP", "Remote", known_urls=known)
        assert results == []

    def test_respects_max_results(self):
        jobs = [_make_job(slug=f"job-{i}") for i in range(10)]
        src = _build_source(jobs)
        results = src.search("PHP", "Remote", max_results=3)
        assert len(results) == 3

    def test_description_stripped(self):
        src = _build_source([_make_job(description="<p>Good <b>role</b></p>")])
        results = src.search("PHP", "Remote")
        assert results[0].description == "Good role"

    def test_empty_response_returns_empty(self):
        src = _build_source([])
        results = src.search("PHP", "Remote")
        assert results == []

    def test_skips_job_without_url(self):
        job = _make_job()
        job["url"] = ""
        src = _build_source([job])
        results = src.search("PHP", "Remote")
        assert results == []

    def test_location_fallback_to_remote(self):
        job = _make_job(location="")
        src = _build_source([job])
        results = src.search("PHP", "Remote")
        assert results[0].location == "Remote"

    def test_company_extracted(self):
        src = _build_source([_make_job(company="GlobalTech")])
        results = src.search("PHP", "Remote")
        assert results[0].company == "GlobalTech"

    def test_native_remote_facts_are_preserved(self):
        src = _build_source([_make_job(location="Europe")])
        result = src.search("PHP", "Poland")[0]
        assert result.source_structured_data == {
            "remote": True,
            "remote_available": True,
            "remote_regions": ["Europe"],
            "_salary_disclosed": False,
        }

    def test_native_salary_is_normalized(self):
        job = _make_job()
        job.update(salary_min=80_000, salary_max=120_000)
        result = _build_source([job]).search("PHP", "Remote")[0]
        assert result.source_structured_data["salary_min"] == 80_000
        assert result.source_structured_data["salary_max"] == 120_000
        assert result.source_structured_data["salary_currency"] == "USD"
        assert result.source_structured_data["salary_period"] == "yearly"

    def test_records_search_funnel(self):
        src = _build_source([
            _make_job(slug="fresh"),
            _make_job(slug="old", days_ago=20),
            _make_job(slug="us", location="USA Only"),
        ])
        src.search("PHP", "Poland")
        assert src.last_search_diagnostics == {
            "upstream_found": 3,
            "query_matched": 3,
            "date_matched": 2,
            "geo_matched": 1,
            "known_url_filtered": 0,
        }

    def test_epoch_is_used_when_date_is_invalid(self):
        job = _make_job()
        job["date"] = "invalid"
        job["epoch"] = int(datetime.now(timezone.utc).timestamp())
        assert len(_build_source([job]).search("PHP", "Remote")) == 1

    def test_max_results_does_not_truncate_funnel_diagnostics(self):
        jobs = [_make_job(slug=f"job-{index}") for index in range(4)]
        source = _build_source(jobs)
        assert len(source.search("PHP", "Remote", max_results=1)) == 1
        assert source.last_search_diagnostics["geo_matched"] == 4

    def test_known_url_has_its_own_diagnostic(self):
        job = _make_job(slug="known")
        source = _build_source([job])
        source.search("PHP", "Remote", known_urls={job["url"]})
        assert source.last_search_diagnostics["known_url_filtered"] == 1

    def test_posted_at_captures_the_publication_date(self):
        # "date" was already parsed for the days_back cutoff, then discarded,
        # RawJob.posted_at carries it through instead.
        src = _build_source([_make_job(days_ago=5)])
        results = src.search("PHP", "Remote")
        assert results[0].posted_at is not None
        posted = datetime.fromisoformat(results[0].posted_at)
        assert (datetime.now(timezone.utc) - posted).days == 5


# ── _fetch_jobs: merges the generic feed with a per-tag fetch ─────────────────


class TestFetchJobsMergesGenericAndTag:
    # Regression: the unauthenticated API only ever exposes its newest ~100
    # postings site-wide (verified live: increasingly dominated by non-tech
    # postings on a given day), so relevant jobs outside that narrow window were
    # invisible no matter the query. ?tags={tag} narrows the same endpoint to
    # postings carrying that tag - fetched and merged with the generic feed
    # instead of replacing it, since a real match sometimes lacks the tag too
    # (also verified live).

    def _source_with_urls(self) -> RemoteOKSource:
        src = RemoteOKSource()
        src._client = MagicMock()
        return src

    def test_tag_fetch_is_merged_with_generic_feed(self):
        generic_job = _make_job(slug="generic-hit", position="Senior PHP Developer")
        tag_only_job = _make_job(slug="tag-only-hit", position="PHP Backend Engineer")
        src = self._source_with_urls()
        src._fetch_url = MagicMock(side_effect=lambda url: (
            [generic_job] if url == "https://remoteok.com/api" else [tag_only_job]
        ))

        jobs = src._fetch_jobs("php")

        urls = {j["url"] for j in jobs}
        assert generic_job["url"] in urls
        assert tag_only_job["url"] in urls

    def test_duplicate_url_in_both_feeds_kept_once(self):
        job = _make_job(slug="shared")
        src = self._source_with_urls()
        src._fetch_url = MagicMock(return_value=[job])

        jobs = src._fetch_jobs("php")

        assert len(jobs) == 1

    def test_no_tag_only_fetches_generic_feed(self):
        src = self._source_with_urls()
        src._fetch_url = MagicMock(return_value=[_make_job()])

        src._fetch_jobs("")

        src._fetch_url.assert_called_once_with("https://remoteok.com/api")

    def test_generic_feed_fetched_once_across_multiple_tags(self):
        src = self._source_with_urls()
        src._fetch_url = MagicMock(return_value=[])

        src._fetch_jobs("php")
        src._fetch_jobs("python")

        generic_calls = [c for c in src._fetch_url.call_args_list if c.args[0] == "https://remoteok.com/api"]
        assert len(generic_calls) == 1

    def test_same_tag_fetched_once_across_repeated_calls(self):
        src = self._source_with_urls()
        src._fetch_url = MagicMock(return_value=[])

        src._fetch_jobs("php")
        src._fetch_jobs("php")

        tag_calls = [c for c in src._fetch_url.call_args_list if "tags=" in c.args[0]]
        assert len(tag_calls) == 1

    def test_tag_url_includes_tags_query_param(self):
        src = self._source_with_urls()
        src._fetch_url = MagicMock(return_value=[])

        src._fetch_jobs("python")

        called_urls = [c.args[0] for c in src._fetch_url.call_args_list]
        assert "https://remoteok.com/api?tags=python" in called_urls


class TestSearchDerivesTagFromFirstWord:
    def test_multi_word_title_uses_first_word_as_tag(self):
        src = RemoteOKSource()
        src._fetch_jobs = MagicMock(return_value=[])

        src.search("Symfony Developer", "Remote")

        src._fetch_jobs.assert_called_once_with("symfony")


class TestRemoteOKQueryTags:
    @pytest.mark.parametrize(("query", "tag"), [
        ("Node.js Developer", "node"),
        (".NET Developer", ".net"),
        ("Go Developer", "golang"),
        ("Software Engineer", "software"),
        ("Full Stack Developer", "full-stack"),
        ("Machine Learning Engineer", "machine-learning"),
    ])
    def test_maps_catalog_queries_to_remoteok_tags(self, query, tag):
        assert _tag_for_query(query) == tag


class TestRemoteOKDescriptionLocation:
    def test_empty_location_with_country_restriction_is_not_worldwide(self):
        description = "Mostly remote within Germany with a monthly Berlin office day."
        assert not _location_is_eligible("", description, "Poland")

    def test_empty_location_with_matching_country_is_eligible(self):
        assert _location_is_eligible("", "We hire remote candidates in Poland.", "Poland")

    def test_empty_location_without_restriction_remains_unknown(self):
        assert _location_is_eligible("", "Join our distributed engineering team.", "Poland")


class TestRemoteOKFetchFailures:
    def test_generic_feed_error_is_visible_to_runner(self):
        source = RemoteOKSource()
        source._client = MagicMock()
        response = MagicMock()
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "unavailable", request=MagicMock(), response=MagicMock(),
        )
        source._client.get.return_value = response

        with pytest.raises(httpx.HTTPStatusError):
            source.search("Python Developer", "Poland")

    def test_tag_feed_error_falls_back_to_generic_and_marks_partial(self):
        source = RemoteOKSource()
        source._generic_cache = [_make_job(position="Python Developer")]
        source._fetch_url = MagicMock(side_effect=httpx.HTTPError("tag unavailable"))

        results = source.search("Python Developer", "Poland")

        assert len(results) == 1
        assert source.last_search_diagnostics["source_status"] == "partial"
        assert "tag unavailable" in source.last_search_diagnostics["source_error"]
