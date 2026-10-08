"""Unit tests for the solid.jobs scraper, no real HTTP calls made."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from collector.sources.solidjobs import SolidJobsSource


def _offer(
    offer_id=1,
    job_title="PHP Developer",
    company="Acme",
    city="Warszawa",
    slug="acme-php-developer",
    skills=None,
    remote="W całości",
    days_ago=0,
):
    valid_from = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    return {
        "id": offer_id,
        "jobTitle": job_title,
        "companyName": company,
        "companyCity": city,
        "jobOfferUrl": slug,
        "requiredSkills": [{"name": s} for s in (skills or ["PHP"])],
        "remotePossible": remote,
        "validFrom": valid_from,
    }


def _make_source() -> SolidJobsSource:
    src = SolidJobsSource()
    src._client = MagicMock()
    src.fetch_description = MagicMock(return_value="Full description")
    return src


class TestSolidJobsSearch:
    def test_non_poland_location_returns_empty_without_request(self):
        src = _make_source()
        results = src.search("PHP", "Germany")
        assert results == []
        src._client.get.assert_not_called()

    def test_poland_search_matches_by_title(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, json=lambda: [_offer(job_title="Senior PHP Developer")])
        results = src.search("PHP", "Poland")
        assert len(results) == 1
        assert results[0].company == "Acme"
        assert results[0].source == "solidjobs"

    def test_matches_by_required_skill_not_just_title(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(
            status_code=200, json=lambda: [_offer(job_title="Backend Developer", skills=["PHP", "Symfony"])]
        )
        results = src.search("PHP", "Poland")
        assert len(results) == 1

    def test_non_matching_keyword_excluded(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(
            status_code=200, json=lambda: [_offer(job_title="Java Developer", skills=["Java", "Spring"])]
        )
        results = src.search("PHP", "Poland")
        assert results == []

    def test_hybrid_included_and_labeled_hybrid(self):
        # Regression: solid.jobs is routed for hybrid/onsite Polish-city
        # candidates too (collector/runner.py's _POLAND_ONLY_SOURCES), these
        # used to be silently dropped entirely by a remote-only allowlist.
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, json=lambda: [_offer(city="Krakow", remote="Hybrydowo")])
        results = src.search("PHP", "Poland")
        assert len(results) == 1
        assert results[0].location == "Krakow, Poland (Hybrid)"

    def test_partial_remote_included_and_labeled_hybrid(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(
            status_code=200, json=lambda: [_offer(city="Warszawa", remote="Możliwa częściowo")]
        )
        results = src.search("PHP", "Poland")
        assert len(results) == 1
        assert results[0].location == "Warszawa, Poland (Hybrid)"

    def test_no_remote_option_included_with_no_suffix(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, json=lambda: [_offer(city="Gdansk", remote="Brak")])
        results = src.search("PHP", "Poland")
        assert len(results) == 1
        assert results[0].location == "Gdansk, Poland"

    def test_stationary_or_remote_included_and_labeled_remote(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(
            status_code=200, json=lambda: [_offer(city="Poznan", remote="Stacjonarnie lub zdalnie")]
        )
        results = src.search("PHP", "Poland")
        assert len(results) == 1
        assert results[0].location == "Poznan, Poland (Remote)"

    def test_fully_remote_labeled_remote(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, json=lambda: [_offer(city="Warszawa", remote="W całości")])
        results = src.search("PHP", "Poland")
        assert results[0].location == "Warszawa, Poland (Remote)"

    def test_url_built_from_id_and_slug(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, json=lambda: [_offer(offer_id=42, slug="foo-bar")])
        results = src.search("PHP", "Poland")
        assert results[0].url == "https://solid.jobs/offer/42/foo-bar"

    def test_description_fetched_inline(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, json=lambda: [_offer()])
        results = src.search("PHP", "Poland")
        assert results[0].description == "Full description"
        src.fetch_description.assert_called_once()

    def test_skips_known_urls(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, json=lambda: [_offer(offer_id=5, slug="dup")])
        known = {"https://solid.jobs/offer/5/dup"}
        results = src.search("PHP", "Poland", known_urls=known)
        assert results == []
        src.fetch_description.assert_not_called()

    def test_known_url_is_canonical_and_remote_fact_is_retained(self):
        src = _make_source()
        offer = _offer(offer_id=5, slug="remote-job", remote="W całości")
        src._client.get.return_value = MagicMock(status_code=200, json=lambda: [offer])
        result = src.search("PHP", "Poland")[0]
        assert result.source_structured_data == {
            "remote": True,
            "remote_available": True,
            "remote_regions": ["Poland"],
        }
        assert src.search("PHP", "Poland", known_urls={result.url + "/?utm=career"}) == []

    def test_native_salary_facts_are_retained(self):
        src = _make_source()
        offer = _offer()
        offer["salary"] = {"from": 15000, "to": 22000, "currency": "PLN"}
        src._client.get.return_value = MagicMock(status_code=200, json=lambda: [offer])
        facts = src.search("PHP", "Poland")[0].source_structured_data
        assert facts["salary_min"] == 15000
        assert facts["salary_max"] == 22000
        assert facts["salary_currency"] == "PLN"

    def test_filters_by_date(self):
        src = _make_source()
        fresh = _offer(offer_id=1, slug="fresh", days_ago=1)
        old = _offer(offer_id=2, slug="old", days_ago=30)
        src._client.get.return_value = MagicMock(status_code=200, json=lambda: [fresh, old])
        results = src.search("PHP", "Poland", days_back=7)
        assert len(results) == 1
        assert "fresh" in results[0].url

    def test_respects_max_results(self):
        src = _make_source()
        offers = [_offer(offer_id=i, slug=f"job-{i}") for i in range(5)]
        src._client.get.return_value = MagicMock(status_code=200, json=lambda: offers)
        results = src.search("PHP", "Poland", max_results=2)
        assert len(results) == 2

    def test_offers_list_cached_across_searches(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, json=lambda: [_offer(job_title="PHP Developer"), _offer(offer_id=2, job_title="Python Developer", skills=["Python"])])
        src.search("PHP", "Poland")
        src.search("Python", "Poland")
        assert src._client.get.call_count == 1

    def test_empty_title_returns_empty(self):
        src = _make_source()
        results = src.search("   ", "Poland")
        assert results == []
        src._client.get.assert_not_called()

    def test_non_200_response_returns_empty(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=500)
        results = src.search("PHP", "Poland")
        assert results == []

    def test_request_exception_returns_empty(self):
        src = _make_source()
        src._client.get.side_effect = Exception("network error")
        results = src.search("PHP", "Poland")
        assert results == []
        assert src.last_search_diagnostics["source_status"] == "error"

    def test_posted_at_captures_valid_from(self):
        # validFrom was already parsed for the days_back cutoff, then discarded,
        # RawJob.posted_at carries it through instead.
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, json=lambda: [_offer(days_ago=3)])
        results = src.search("PHP", "Poland")
        assert results[0].posted_at is not None
        posted = datetime.fromisoformat(results[0].posted_at)
        assert (datetime.now(timezone.utc) - posted).days == 3


class TestSolidJobsFetchDescription:
    def test_joins_description_and_candidate_profile(self):
        src = SolidJobsSource()
        src._client = MagicMock()
        src._client.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"jobOfferDetails": {"jobDescription": "Requires PHP", "candidateProfile": "3+ years"}},
        )
        desc = src.fetch_description("https://solid.jobs/offer/42/foo-bar")
        assert "Requires PHP" in desc and "3+ years" in desc

    def test_returns_none_on_non_200(self):
        src = SolidJobsSource()
        src._client = MagicMock()
        src._client.get.return_value = MagicMock(status_code=404)
        assert src.fetch_description("https://solid.jobs/offer/42/foo-bar") is None

    def test_returns_none_on_request_exception(self):
        src = SolidJobsSource()
        src._client = MagicMock()
        src._client.get.side_effect = Exception("boom")
        assert src.fetch_description("https://solid.jobs/offer/42/foo-bar") is None
