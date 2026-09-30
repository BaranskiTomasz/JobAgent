"""Unit tests for the theprotocol.it scraper, no real browser calls made."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from collector.sources.theprotocol import TheProtocolSource, _read_next_data, _section_text, _salary_text


def _next_data_page(payload: dict) -> MagicMock:
    page = MagicMock()
    page.eval_on_selector.return_value = json.dumps(payload)
    return page


def _search_payload(offers: list[dict]) -> dict:
    return {"props": {"pageProps": {"offersResponse": {"offers": offers, "page": {"number": 1, "size": 50, "count": 1}}}}}


def _detail_payload(offer: dict | None) -> dict:
    return {"props": {"pageProps": {"offer": offer}}}


def _offer(
    offer_url_name="acme-php-developer,oferta,abc",
    title="PHP Developer",
    employer="Acme",
    city="Warszawa",
    offer_id="abc-123",
    days_ago=0,
    work_modes=None,
    technologies=None,
):
    published = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.%f")
    return {
        "id": offer_id,
        "title": title,
        "employer": employer,
        "offerUrlName": offer_url_name,
        "workplace": [{"city": city}],
        "workModes": work_modes if work_modes is not None else ["remote"],
        "publicationDateUtc": published,
        "technologies": technologies if technologies is not None else [],
    }


class TestReadNextData:
    def test_parses_valid_json(self):
        page = _next_data_page({"props": {"pageProps": {}}})
        data = _read_next_data(page)
        assert data == {"props": {"pageProps": {}}}

    def test_returns_none_on_missing_selector(self):
        page = MagicMock()
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        page.wait_for_selector.side_effect = PlaywrightTimeout("timeout")
        assert _read_next_data(page) is None

    def test_returns_none_on_invalid_json(self):
        page = MagicMock()
        page.eval_on_selector.return_value = "not json"
        assert _read_next_data(page) is None


class TestSectionText:
    def test_joins_plain_text_sections(self):
        offer = {"textSections": [{"plainText": "Requirements: PHP"}, {"plainText": "Benefits: medical"}]}
        text = _section_text(offer)
        assert "Requirements: PHP" in text
        assert "Benefits: medical" in text

    def test_skips_sections_without_plain_text(self):
        offer = {"textSections": [{"plainText": "Has text"}, {"type": "no-text-key"}]}
        text = _section_text(offer)
        assert text == "Has text"

    def test_empty_sections_returns_empty_string(self):
        assert _section_text({"textSections": []}) == ""

    def test_missing_key_returns_empty_string(self):
        assert _section_text({}) == ""


def _contract(name="kontrakt B2B", from_=23000, to=32000, currency="zł", period_short="mies.", kind="netto (+ VAT)"):
    return {
        "name": name,
        "salary": {
            "from": from_, "to": to, "currencyCode": currency,
            "timeUnit": {"shortForm": period_short}, "kindCode": kind,
        },
    }


class TestSalaryText:
    def test_single_contract_formatted(self):
        offer = {"attributes": {"employment": {"typesOfContracts": [_contract()]}}}
        text = _salary_text(offer)
        assert "23000-32000 PLN" in text
        assert "kontrakt B2B" in text
        assert "netto (+ VAT)" in text

    def test_currency_symbol_normalized_to_code(self):
        offer = {"attributes": {"employment": {"typesOfContracts": [_contract(currency="zł")]}}}
        assert "PLN" in _salary_text(offer)
        assert "zł" not in _salary_text(offer)

    def test_multiple_contract_types_both_included(self):
        offer = {"attributes": {"employment": {"typesOfContracts": [
            _contract(name="umowa o pracę", from_=10000, to=21500, kind="brutto"),
            _contract(name="kontrakt B2B", from_=10000, to=27000),
        ]}}}
        text = _salary_text(offer)
        assert "umowa o pracę" in text
        assert "10000-21500" in text
        assert "kontrakt B2B" in text
        assert "10000-27000" in text

    def test_no_contracts_returns_empty_string(self):
        assert _salary_text({"attributes": {"employment": {"typesOfContracts": []}}}) == ""

    def test_missing_attributes_returns_empty_string(self):
        assert _salary_text({}) == ""

    def test_contract_without_salary_skipped(self):
        offer = {"attributes": {"employment": {"typesOfContracts": [{"name": "B2B"}]}}}
        assert _salary_text(offer) == ""

    def test_partial_salary_range_skipped(self):
        # e.g. only "from" disclosed, no upper bound, don't fabricate a range
        offer = {"attributes": {"employment": {"typesOfContracts": [
            {"name": "B2B", "salary": {"from": 23000, "to": None, "currencyCode": "zł"}}
        ]}}}
        assert _salary_text(offer) == ""


def _make_source() -> TheProtocolSource:
    src = TheProtocolSource()
    src._page = MagicMock()
    src.fetch_description = MagicMock(return_value="Full description")
    return src


class TestTheProtocolSourceSearch:
    def test_non_poland_location_returns_empty_without_navigation(self):
        src = _make_source()
        results = src.search("PHP", "Germany")
        assert results == []
        src._page.goto.assert_not_called()

    def test_poland_search_returns_mapped_job(self):
        src = _make_source()
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([_offer()]))
        results = src.search("PHP", "Poland")
        assert len(results) == 1
        assert results[0].company == "Acme"
        assert results[0].source == "theprotocol"

    def test_tag_uses_meaningful_query_token(self):
        src = _make_source()
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([_offer()]))
        src.search("Symfony Developer", "Poland")
        called_url = src._page.goto.call_args[0][0]
        assert "symfony" in called_url
        assert "developer" not in called_url

        src = _make_source()
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([]))
        src.search("Senior Backend Engineer", "Poland")
        assert "backend" in src._page.goto.call_args[0][0]

    def test_search_url_does_not_restrict_to_remote_workmode(self):
        # Regression: theprotocol.it is routed for hybrid/onsite Polish-city
        # candidates too (collector/runner.py's _POLAND_ONLY_SOURCES), so a
        # hardcoded ";t/zdalna;rw" (remote-only) URL segment silently
        # returned nothing relevant for them.
        src = _make_source()
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([_offer()]))
        src.search("PHP", "Poland")
        called_url = src._page.goto.call_args[0][0]
        assert "zdalna" not in called_url

    def test_remote_workmode_labeled_remote(self):
        src = _make_source()
        offer = _offer(city="Warszawa", work_modes=["remote"])
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([offer]))
        results = src.search("PHP", "Poland")
        assert results[0].location == "Warszawa, Poland (Remote)"

    def test_hybrid_workmode_labeled_hybrid(self):
        src = _make_source()
        offer = _offer(city="Krakow", work_modes=["hybrid"])
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([offer]))
        results = src.search("PHP", "Poland")
        assert results[0].location == "Krakow, Poland (Hybrid)"

    def test_onsite_workmode_has_no_suffix(self):
        src = _make_source()
        offer = _offer(city="Gdansk", work_modes=["full office"])
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([offer]))
        results = src.search("PHP", "Poland")
        assert results[0].location == "Gdansk, Poland"

    def test_polish_workmode_tokens_recognized(self):
        src = _make_source()
        offer = _offer(city="Poznan", work_modes=["hybrydowa", "stacjonarna"])
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([offer]))
        results = src.search("PHP", "Poland")
        # remote not present, hybrid is -> hybrid wins over onsite
        assert results[0].location == "Poznan, Poland (Hybrid)"

    def test_detail_url_built_from_offer_url_name(self):
        src = _make_source()
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([_offer(offer_url_name="foo,oferta,xyz")]))
        results = src.search("PHP", "Poland")
        assert results[0].url == "https://theprotocol.it/praca/foo,oferta,xyz"

    def test_description_fetched_inline(self):
        src = _make_source()
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([_offer()]))
        results = src.search("PHP", "Poland")
        assert results[0].description == "Full description"
        src.fetch_description.assert_called_once()

    def test_skips_known_urls(self):
        src = _make_source()
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([_offer(offer_url_name="dup,oferta,1")]))
        known = {"https://theprotocol.it/praca/dup,oferta,1"}
        results = src.search("PHP", "Poland", known_urls=known)
        assert results == []
        src.fetch_description.assert_not_called()

    def test_filters_by_date(self):
        src = _make_source()
        fresh = _offer(offer_url_name="fresh,oferta,1", days_ago=1)
        old = _offer(offer_url_name="old,oferta,2", days_ago=30)
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([fresh, old]))
        results = src.search("PHP", "Poland", days_back=7)
        assert len(results) == 1
        assert "fresh" in results[0].url

    def test_respects_max_results(self):
        src = _make_source()
        offers = [_offer(offer_url_name=f"job-{i},oferta,{i}") for i in range(5)]
        src._page.eval_on_selector.return_value = json.dumps(_search_payload(offers))
        results = src.search("PHP", "Poland", max_results=2)
        assert len(results) == 2

    def test_missing_next_data_returns_empty(self):
        src = _make_source()
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        src._page.wait_for_selector.side_effect = PlaywrightTimeout("timeout")
        results = src.search("PHP", "Poland")
        assert results == []

    def test_goto_timeout_returns_empty(self):
        src = _make_source()
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        src._page.goto.side_effect = PlaywrightTimeout("timeout")
        results = src.search("PHP", "Poland")
        assert results == []

    def test_empty_title_returns_empty(self):
        src = _make_source()
        results = src.search("   ", "Poland")
        assert results == []
        src._page.goto.assert_not_called()

    def test_offer_not_matching_tag_is_excluded(self):
        # Regression: theprotocol.it's /filtry/{tag} URL stopped applying any
        # server-side filter (verified live 2026-08-30: unrelated and even
        # nonsense tags returned the identical unfiltered top-50 listing), so
        # search() has to reject offers itself instead of trusting the site.
        src = _make_source()
        unrelated = _offer(title="Scrum Master", technologies=["Azure DevOps", ".NET"])
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([unrelated]))
        results = src.search("PHP", "Poland")
        assert results == []

    def test_offer_matching_via_technologies_list_is_included(self):
        # The tag can match a listed technology even when it isn't in the title
        # itself (e.g. a non-PHP role that also lists PHP as a secondary skill).
        src = _make_source()
        offer = _offer(title="Scrum Master", technologies=["Azure DevOps", "PHP"])
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([offer]))
        results = src.search("PHP", "Poland")
        assert len(results) == 1

    def test_posted_at_captures_the_publication_date(self):
        # publicationDateUtc was already parsed for the days_back cutoff, then
        # discarded, RawJob.posted_at carries it through instead.
        src = _make_source()
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([_offer(days_ago=2)]))
        results = src.search("PHP", "Poland")
        assert results[0].posted_at is not None
        posted = datetime.fromisoformat(results[0].posted_at)
        assert (datetime.now(timezone.utc) - posted).days == 2


class TestFetchDescription:
    def test_returns_joined_sections(self):
        src = TheProtocolSource()
        src._page = _next_data_page(_detail_payload({"textSections": [{"plainText": "Requires PHP"}]}))
        desc = src.fetch_description("https://theprotocol.it/praca/foo")
        assert desc == "Requires PHP"

    def test_salary_prepended_when_present(self):
        # Regression: theprotocol.it discloses salary as a structured field separate
        # from textSections, a scraper reading only textSections silently drops it,
        # and the evaluator then dings the job for "no salary disclosed" on a listing
        # that plainly had one on the site.
        offer = {
            "textSections": [{"plainText": "Requires PHP"}],
            "attributes": {"employment": {"typesOfContracts": [_contract()]}},
        }
        src = TheProtocolSource()
        src._page = _next_data_page(_detail_payload(offer))
        desc = src.fetch_description("https://theprotocol.it/praca/foo")
        assert "Requires PHP" in desc
        assert "23000-32000 PLN" in desc

    def test_no_salary_data_returns_sections_only(self):
        src = TheProtocolSource()
        src._page = _next_data_page(_detail_payload({"textSections": [{"plainText": "Requires PHP"}]}))
        desc = src.fetch_description("https://theprotocol.it/praca/foo")
        assert desc == "Requires PHP"

    def test_returns_none_when_offer_missing(self):
        src = TheProtocolSource()
        src._page = _next_data_page(_detail_payload(None))
        assert src.fetch_description("https://theprotocol.it/praca/foo") is None

    def test_returns_none_on_goto_timeout(self):
        src = TheProtocolSource()
        src._page = MagicMock()
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        src._page.goto.side_effect = PlaywrightTimeout("timeout")
        assert src.fetch_description("https://theprotocol.it/praca/foo") is None
