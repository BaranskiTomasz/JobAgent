"""Unit tests for the justjoin.it scraper, no real HTTP/browser calls made."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from collector.sources.justjoin import JustJoinSource, _parse_rsc_offers, _extract_source_structured_data


def _push_script(payload: str) -> str:
    """Wrap a raw RSC stream fragment in a <script>self.__next_f.push(...)</script> tag,
    the same way Next.js embeds it in server-rendered HTML."""
    return f"<script>self.__next_f.push([1,{json.dumps(payload)}])</script>"


def _offers_html(offers: list[dict]) -> str:
    """Build minimal HTML embedding one OFFERS query chunk, matching the real
    react-query dehydrated-state shape justjoin.it streams via RSC."""
    state = {
        "state": {
            "mutations": [],
            "queries": [{
                "queryKey": ["OFFERS", {}],
                "state": {"data": {"pages": [{"meta": {}, "data": offers}], "pageParams": [None]}},
            }],
        },
        "children": [],
    }
    payload = json.dumps(["$", "$L27", None, state])
    return _push_script(f"1d:{payload}")


def _offer(
    slug="acme-php-developer",
    title="PHP Developer",
    company="Acme",
    city="Warszawa",
    guid="abc-123",
    days_ago=0,
    workplace_type="remote",
):
    published = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "slug": slug,
        "title": title,
        "companyName": company,
        "city": city,
        "guid": guid,
        "publishedAt": published,
        "employmentTypes": [{"currency": "PLN"}],
        "workplaceType": workplace_type,
    }


class TestParseRscOffers:
    def test_extracts_offers_from_single_chunk(self):
        html = _offers_html([_offer()])
        offers = _parse_rsc_offers(html)
        assert len(offers) == 1
        assert offers[0]["title"] == "PHP Developer"
        assert offers[0]["companyName"] == "Acme"

    def test_extracts_multiple_offers(self):
        html = _offers_html([_offer(slug="a"), _offer(slug="b", title="Python Developer")])
        offers = _parse_rsc_offers(html)
        assert len(offers) == 2

    def test_no_offers_query_returns_empty(self):
        html = _push_script('99:["$","$Lxx",null,{"state":{"queries":[]}}]')
        assert _parse_rsc_offers(html) == []

    def test_garbage_html_returns_empty_not_raises(self):
        assert _parse_rsc_offers("<html>not next.js at all</html>") == []

    def test_ignores_unrelated_push_calls(self):
        # Other RSC rows (component references, translation strings, etc.) share the
        # same push() wrapper but must not be mistaken for the OFFERS chunk.
        unrelated = _push_script('12:["$","$L5",null,{"messages":{"header":"Filters"}}]')
        html = unrelated + _offers_html([_offer()])
        offers = _parse_rsc_offers(html)
        assert len(offers) == 1


def _make_source() -> JustJoinSource:
    src = JustJoinSource()
    src._client = MagicMock()
    src.fetch_description = MagicMock(return_value="Full description text")
    return src


class TestJustJoinSourceSearch:
    def test_non_poland_location_returns_empty_without_request(self):
        src = _make_source()
        results = src.search("PHP Developer", "Germany")
        assert results == []
        src._client.get.assert_not_called()

    def test_poland_location_triggers_search(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, text=_offers_html([_offer()]))
        results = src.search("PHP Developer", "Poland")
        assert len(results) == 1
        assert results[0].company == "Acme"
        assert results[0].source == "justjoin"

    def test_polish_spelling_also_matches(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, text=_offers_html([_offer()]))
        results = src.search("PHP Developer", "Polska")
        assert len(results) == 1

    def test_url_built_from_slug(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, text=_offers_html([_offer(slug="acme-php-dev")]))
        results = src.search("PHP Developer", "Poland")
        assert results[0].url == "https://justjoin.it/job-offer/acme-php-dev"

    def test_description_fetched_inline(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, text=_offers_html([_offer()]))
        results = src.search("PHP Developer", "Poland")
        assert results[0].description == "Full description text"
        src.fetch_description.assert_called_once()

    def test_skips_known_urls(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, text=_offers_html([_offer(slug="dup")]))
        known = {"https://justjoin.it/job-offer/dup"}
        results = src.search("PHP Developer", "Poland", known_urls=known)
        assert results == []
        src.fetch_description.assert_not_called()

    def test_filters_by_date(self):
        src = _make_source()
        fresh = _offer(slug="fresh", days_ago=1)
        old = _offer(slug="old", days_ago=30)
        src._client.get.return_value = MagicMock(status_code=200, text=_offers_html([fresh, old]))
        results = src.search("PHP Developer", "Poland", days_back=7)
        assert len(results) == 1
        assert "fresh" in results[0].url

    def test_respects_max_results(self):
        src = _make_source()
        offers = [_offer(slug=f"job-{i}") for i in range(5)]
        src._client.get.return_value = MagicMock(status_code=200, text=_offers_html(offers))
        results = src.search("PHP Developer", "Poland", max_results=2)
        assert len(results) == 2

    def test_search_does_not_restrict_to_remote_workplace(self):
        # Regression: justjoin.it is routed for hybrid/onsite Polish-city candidates
        # too (collector/runner.py's _POLAND_ONLY_SOURCES), so a hardcoded
        # workplace=remote param silently returned nothing relevant for them.
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, text=_offers_html([_offer()]))
        src.search("PHP Developer", "Poland")
        _, kwargs = src._client.get.call_args
        assert "workplace" not in kwargs.get("params", {})

    def test_remote_offer_location_labeled_remote(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(
            status_code=200, text=_offers_html([_offer(city="Warszawa", workplace_type="remote")])
        )
        results = src.search("PHP Developer", "Poland")
        assert results[0].location == "Warszawa, Poland (Remote)"

    def test_hybrid_offer_location_labeled_hybrid(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(
            status_code=200, text=_offers_html([_offer(city="Krakow", workplace_type="hybrid")])
        )
        results = src.search("PHP Developer", "Poland")
        assert results[0].location == "Krakow, Poland (Hybrid)"

    def test_office_offer_location_has_no_suffix(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(
            status_code=200, text=_offers_html([_offer(city="Gdansk", workplace_type="office")])
        )
        results = src.search("PHP Developer", "Poland")
        assert results[0].location == "Gdansk, Poland"

    def test_non_200_response_returns_empty(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=500, text="")
        results = src.search("PHP Developer", "Poland")
        assert results == []

    def test_request_exception_returns_empty(self):
        src = _make_source()
        src._client.get.side_effect = Exception("network error")
        results = src.search("PHP Developer", "Poland")
        assert results == []

    def test_empty_offers_returns_empty(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, text="<html></html>")
        results = src.search("PHP Developer", "Poland")
        assert results == []

    def test_source_structured_data_captures_salary_and_skills(self):
        # Regression: justjoin.it discloses salary/skills as structured API
        # fields (verified live against the real site), Haiku shouldn't have
        # to re-guess them from the description text later.
        offer = _offer()
        offer["experienceLevel"] = "senior"
        offer["employmentTypes"] = [
            {"from": 15000, "to": 25000, "currency": "PLN", "currencySource": "original", "unit": "month"},
            {"from": 3500, "to": 5800, "currency": "EUR", "currencySource": "conversion", "unit": "month"},
        ]
        offer["requiredSkills"] = ["PHP", "Symfony"]
        offer["niceToHaveSkills"] = ["Docker"]
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, text=_offers_html([offer]))
        results = src.search("PHP Developer", "Poland")
        ssd = results[0].source_structured_data
        assert ssd["seniority"] == "senior"
        assert ssd["salary_min"] == 15000
        assert ssd["salary_max"] == 25000
        assert ssd["salary_currency"] == "PLN"
        assert ssd["salary_period"] == "monthly"
        assert ssd["stack_required"] == ["PHP", "Symfony"]
        assert ssd["stack_preferred"] == ["Docker"]

    def test_posted_at_captures_the_publication_date(self):
        # publishedAt was already parsed for the days_back cutoff, then
        # discarded, RawJob.posted_at carries it through instead.
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, text=_offers_html([_offer(days_ago=3)]))
        results = src.search("PHP Developer", "Poland")
        assert results[0].posted_at is not None
        posted = datetime.fromisoformat(results[0].posted_at)
        assert (datetime.now(timezone.utc) - posted).days == 3


class TestExtractSourceStructuredData:
    def test_extracts_salary_from_original_currency_entry(self):
        offer = {
            "experienceLevel": "senior",
            "employmentTypes": [
                {"from": 15000, "to": 25000, "currency": "PLN", "currencySource": "original", "unit": "month"},
                {"from": 3500, "to": 5800, "currency": "EUR", "currencySource": "conversion", "unit": "month"},
            ],
        }
        data = _extract_source_structured_data(offer)
        assert data["salary_min"] == 15000
        assert data["salary_currency"] == "PLN"

    def test_unknown_seniority_value_skipped(self):
        # justjoin.it's experienceLevel vocabulary doesn't necessarily line up
        # 1:1 with extractor/runner.py's enum, never write a value outside it.
        data = _extract_source_structured_data({"experienceLevel": "c-level"})
        assert "seniority" not in data

    def test_unsupported_currency_skips_salary_entirely(self):
        offer = {"employmentTypes": [
            {"from": 3200, "to": 5400, "currency": "CHF", "currencySource": "original", "unit": "month"},
        ]}
        assert "salary_min" not in _extract_source_structured_data(offer)

    def test_unknown_unit_skips_salary_entirely(self):
        offer = {"employmentTypes": [
            {"from": 15000, "to": 25000, "currency": "PLN", "currencySource": "original", "unit": "sprint"},
        ]}
        assert "salary_min" not in _extract_source_structured_data(offer)

    def test_no_employment_types_returns_no_salary(self):
        assert _extract_source_structured_data({}) == {"_salary_disclosed": False}

    def test_empty_skills_omitted_not_written_as_empty_list(self):
        offer = {"requiredSkills": [], "niceToHaveSkills": []}
        data = _extract_source_structured_data(offer)
        assert "stack_required" not in data
        assert "stack_preferred" not in data


class TestFetchDescription:
    def test_uses_editor_paragraph_when_present(self):
        src = JustJoinSource()
        src._page = MagicMock()
        src._page.eval_on_selector_all.return_value = ["Para one", "Para two"]
        desc = src.fetch_description("https://justjoin.it/job-offer/foo")
        assert desc == "Para one\nPara two"

    def test_falls_back_to_job_description_heading_when_editor_paragraph_empty(self):
        # A real posting was found live with no .editor-paragraph markup at all,
        # description rendered as plain text under a "Job description" heading instead.
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        src = JustJoinSource()
        src._page = MagicMock()
        src._page.wait_for_selector.side_effect = PlaywrightTimeout("not found")
        src._page.evaluate.return_value = "Full plain-text description"
        desc = src.fetch_description("https://justjoin.it/job-offer/foo")
        assert desc == "Full plain-text description"

    def test_returns_none_when_both_strategies_find_nothing(self):
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        src = JustJoinSource()
        src._page = MagicMock()
        src._page.wait_for_selector.side_effect = PlaywrightTimeout("not found")
        src._page.evaluate.return_value = ""
        assert src.fetch_description("https://justjoin.it/job-offer/foo") is None

    def test_returns_none_on_goto_timeout(self):
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        src = JustJoinSource()
        src._page = MagicMock()
        src._page.goto.side_effect = PlaywrightTimeout("timeout")
        assert src.fetch_description("https://justjoin.it/job-offer/foo") is None
