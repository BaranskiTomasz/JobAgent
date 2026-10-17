"""Unit tests for the theprotocol.it scraper, no real browser calls made."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from collector.sources.theprotocol import (
    TheProtocolSource,
    _read_next_data,
    _section_text,
    _salary_text,
    _extract_source_structured_data,
    _extract_detail_structured_data,
    _SALARY_UNIT_TO_PERIOD,
    _KNOWN_SENIORITY_LEVELS,
)


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
    # Mock goto, wait_for_selector to do nothing (they're called but we don't care about their return)
    src._page.goto.return_value = None
    src._page.wait_for_selector.return_value = None
    # Default behavior for fetch_description - keep for backward compatibility
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
        offer = _offer()
        src._page.eval_on_selector.side_effect = [
            json.dumps(_search_payload([offer])),
            json.dumps(_detail_payload({
                "textSections": [{"plainText": "Full description"}],
                "attributes": {"employment": {"typesOfContracts": []}},
                "technologies": {"expected": [], "optional": []},
            })),
        ]
        results = src.search("PHP", "Poland")
        assert results[0].description == "Full description"

    def test_detail_structured_data_kept_even_when_description_text_is_empty(self):
        # Regression: _fetch_description_and_detail_data() collapses
        # description to None whenever _salary_text()/_section_text() both
        # come back empty (via the "or None" idiom), but detail_structured
        # is populated independently from the SAME successful page load -
        # gating the merge on `description` being truthy silently dropped
        # real stack_required/stack_preferred data on exactly this listing.
        src = _make_source()
        offer = _offer()
        src._page.eval_on_selector.side_effect = [
            json.dumps(_search_payload([offer])),
            json.dumps(_detail_payload({
                "textSections": [],
                "attributes": {"employment": {"typesOfContracts": []}},
                "technologies": {"expected": [{"name": "Python"}], "optional": []},
            })),
        ]
        results = src.search("PHP", "Poland")
        assert results[0].description is None
        assert results[0].source_structured_data["stack_required"] == ["Python"]

    def test_skips_known_urls(self):
        src = _make_source()
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([_offer(offer_url_name="dup,oferta,1")]))
        known = {"https://theprotocol.it/praca/dup,oferta,1"}
        results = src.search("PHP", "Poland", known_urls=known)
        assert results == []

    def test_known_url_is_canonical_and_remote_fact_is_retained(self):
        src = _make_source()
        offer = _offer(offer_url_name="remote,oferta,1", work_modes=["remote"])
        offer.pop("id")
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([offer]))
        result = src.search("PHP", "Poland")[0]
        assert result.source_id == "https://theprotocol.it/praca/remote,oferta,1"
        assert result.source_structured_data == {
            "remote": True,
            "remote_available": True,
        }
        assert src.search("PHP", "Poland", known_urls={result.url + "/?utm=career"}) == []

    def test_follows_numbered_listing_pages(self):
        src = _make_source()
        first = _offer(offer_url_name="first,oferta,1")
        second = _offer(offer_url_name="second,oferta,2")
        first_payload = _search_payload([first])
        first_payload["props"]["pageProps"]["offersResponse"]["page"] = {"number": 1, "size": 1, "count": 2}
        second_payload = _search_payload([second])
        # All listing/pagination pages are fetched first (the pagination
        # loop runs to completion before the per-offer results loop even
        # starts), so the real eval_on_selector call order is
        # [search page 1, search page 2, detail 1, detail 2] - not
        # interleaved per offer.
        detail_payload_1 = _detail_payload({
            "textSections": [{"plainText": "Desc 1"}],
            "attributes": {"employment": {"typesOfContracts": []}},
            "technologies": {"expected": [], "optional": []},
        })
        detail_payload_2 = _detail_payload({
            "textSections": [{"plainText": "Desc 2"}],
            "attributes": {"employment": {"typesOfContracts": []}},
            "technologies": {"expected": [], "optional": []},
        })
        src._page.eval_on_selector.side_effect = [
            json.dumps(first_payload),
            json.dumps(second_payload),
            json.dumps(detail_payload_1),
            json.dumps(detail_payload_2),
        ]
        results = src.search("PHP", "Poland")
        assert [job.url for job in results] == [
            "https://theprotocol.it/praca/first,oferta,1",
            "https://theprotocol.it/praca/second,oferta,2",
        ]
        assert src.last_search_diagnostics["pages_fetched"] == 2

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


class TestExtractSourceStructuredData:
    def test_seniority_extracted_for_known_level(self):
        offer = {"positionLevels": [{"value": "senior"}]}
        data = _extract_source_structured_data(offer)
        assert data["seniority"] == "senior"

    def test_seniority_extracted_for_junior(self):
        offer = {"positionLevels": [{"value": "junior"}]}
        data = _extract_source_structured_data(offer)
        assert data["seniority"] == "junior"

    def test_seniority_extracted_for_mid(self):
        offer = {"positionLevels": [{"value": "mid"}]}
        data = _extract_source_structured_data(offer)
        assert data["seniority"] == "mid"

    def test_seniority_extracted_for_lead(self):
        offer = {"positionLevels": [{"value": "lead"}]}
        data = _extract_source_structured_data(offer)
        assert data["seniority"] == "lead"

    def test_seniority_extracted_for_director(self):
        offer = {"positionLevels": [{"value": "director"}]}
        data = _extract_source_structured_data(offer)
        assert data["seniority"] == "director"

    def test_seniority_skipped_for_unknown_manager(self):
        offer = {"positionLevels": [{"value": "manager"}]}
        data = _extract_source_structured_data(offer)
        assert "seniority" not in data

    def test_seniority_skipped_for_unknown_trainee(self):
        offer = {"positionLevels": [{"value": "trainee"}]}
        data = _extract_source_structured_data(offer)
        assert "seniority" not in data

    def test_seniority_skipped_when_empty(self):
        offer = {"positionLevels": []}
        data = _extract_source_structured_data(offer)
        assert "seniority" not in data

    def test_seniority_skipped_when_missing(self):
        offer = {}
        data = _extract_source_structured_data(offer)
        assert "seniority" not in data

    def test_salary_extracted_with_all_fields(self):
        offer = {
            "typesOfContracts": [{
                "salary": {
                    "from": 22000,
                    "to": 26000,
                    "currencySymbol": "zł",
                    "timeUnit": {"shortForm": "mth."},
                }
            }]
        }
        data = _extract_source_structured_data(offer)
        assert data["salary_min"] == 22000
        assert data["salary_max"] == 26000
        assert data["salary_currency"] == "PLN"
        assert data["salary_period"] == "monthly"
        assert data["_salary_disclosed"] is True

    def test_salary_extracted_with_polish_period(self):
        offer = {
            "typesOfContracts": [{
                "salary": {
                    "from": 22000,
                    "to": 26000,
                    "currencySymbol": "zł",
                    "timeUnit": {"shortForm": "mies."},
                }
            }]
        }
        data = _extract_source_structured_data(offer)
        assert data["salary_period"] == "monthly"

    def test_salary_extracted_with_hourly_period(self):
        offer = {
            "typesOfContracts": [{
                "salary": {
                    "from": 100,
                    "to": 150,
                    "currencySymbol": "zł",
                    "timeUnit": {"shortForm": "godz."},
                }
            }]
        }
        data = _extract_source_structured_data(offer)
        assert data["salary_period"] == "hourly"

    def test_salary_skipped_when_from_missing(self):
        offer = {
            "typesOfContracts": [{
                "salary": {
                    "from": None,
                    "to": 26000,
                    "currencySymbol": "zł",
                    "timeUnit": {"shortForm": "mth."},
                }
            }]
        }
        data = _extract_source_structured_data(offer)
        assert "salary_min" not in data

    def test_salary_skipped_when_to_missing(self):
        offer = {
            "typesOfContracts": [{
                "salary": {
                    "from": 22000,
                    "to": None,
                    "currencySymbol": "zł",
                    "timeUnit": {"shortForm": "mth."},
                }
            }]
        }
        data = _extract_source_structured_data(offer)
        assert "salary_min" not in data

    def test_salary_skipped_when_currency_unknown(self):
        offer = {
            "typesOfContracts": [{
                "salary": {
                    "from": 22000,
                    "to": 26000,
                    "currencySymbol": "XYZ",
                    "timeUnit": {"shortForm": "mth."},
                }
            }]
        }
        data = _extract_source_structured_data(offer)
        assert "salary_min" not in data

    def test_salary_skipped_when_period_unknown(self):
        offer = {
            "typesOfContracts": [{
                "salary": {
                    "from": 22000,
                    "to": 26000,
                    "currencySymbol": "zł",
                    "timeUnit": {"shortForm": "unknown"},
                }
            }]
        }
        data = _extract_source_structured_data(offer)
        assert "salary_min" not in data

    def test_salary_skipped_when_timeunit_missing(self):
        offer = {
            "typesOfContracts": [{
                "salary": {
                    "from": 22000,
                    "to": 26000,
                    "currencySymbol": "zł",
                }
            }]
        }
        data = _extract_source_structured_data(offer)
        assert "salary_min" not in data

    def test_salary_skipped_when_no_contracts(self):
        offer = {"typesOfContracts": []}
        data = _extract_source_structured_data(offer)
        assert "salary_min" not in data

    def test_salary_with_euro_currency(self):
        offer = {
            "typesOfContracts": [{
                "salary": {
                    "from": 50000,
                    "to": 70000,
                    "currencySymbol": "€",
                    "timeUnit": {"shortForm": "mth."},
                }
            }]
        }
        data = _extract_source_structured_data(offer)
        assert data["salary_currency"] == "EUR"

    def test_salary_rounding(self):
        # 22500.6/26500.7 (not a plain .5) to sidestep Python's round-half-
        # to-even banker's rounding, which would otherwise round 22500.5
        # down to 22500 rather than up - not a bug, just the wrong value to
        # assert an unambiguous round-up with.
        offer = {
            "typesOfContracts": [{
                "salary": {
                    "from": 22500.6,
                    "to": 26500.7,
                    "currencySymbol": "zł",
                    "timeUnit": {"shortForm": "mth."},
                }
            }]
        }
        data = _extract_source_structured_data(offer)
        assert data["salary_min"] == 22501
        assert data["salary_max"] == 26501


class TestExtractDetailStructuredData:
    def test_stack_required_extracted_from_expected(self):
        offer = {
            "technologies": {
                "expected": [{"name": "Python"}, {"name": "Django"}],
                "optional": [],
            }
        }
        data = _extract_detail_structured_data(offer)
        assert "stack_required" in data
        assert data["stack_required"] == ["Python", "Django"]

    def test_stack_preferred_extracted_from_optional(self):
        offer = {
            "technologies": {
                "expected": [],
                "optional": [{"name": "React"}, {"name": "TypeScript"}],
            }
        }
        data = _extract_detail_structured_data(offer)
        assert "stack_preferred" in data
        assert data["stack_preferred"] == ["React", "TypeScript"]

    def test_both_stack_required_and_preferred(self):
        offer = {
            "technologies": {
                "expected": [{"name": "Python"}],
                "optional": [{"name": "Django"}],
            }
        }
        data = _extract_detail_structured_data(offer)
        assert data["stack_required"] == ["Python"]
        assert data["stack_preferred"] == ["Django"]

    def test_empty_optional_not_written(self):
        offer = {
            "technologies": {
                "expected": [{"name": "Python"}],
                "optional": [],
            }
        }
        data = _extract_detail_structured_data(offer)
        assert "stack_required" in data
        assert "stack_preferred" not in data

    def test_empty_expected_not_written(self):
        offer = {
            "technologies": {
                "expected": [],
                "optional": [{"name": "Python"}],
            }
        }
        data = _extract_detail_structured_data(offer)
        assert "stack_required" not in data
        assert "stack_preferred" in data

    def test_missing_technologies_key(self):
        offer = {}
        data = _extract_detail_structured_data(offer)
        assert "stack_required" not in data
        assert "stack_preferred" not in data

    def test_technologies_with_os_key_ignored(self):
        offer = {
            "technologies": {
                "expected": [{"name": "Python"}],
                "optional": [],
                "os": [{"name": "Linux"}],
            }
        }
        data = _extract_detail_structured_data(offer)
        assert data["stack_required"] == ["Python"]
        assert "stack_preferred" not in data


class TestSearchStructuredData:
    def test_seniority_in_structured_data(self):
        src = _make_source()
        offer = _offer()
        offer["positionLevels"] = [{"value": "senior"}]
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([offer]))
        results = src.search("PHP", "Poland")
        assert len(results) == 1
        assert results[0].source_structured_data["seniority"] == "senior"

    def test_salary_in_structured_data(self):
        src = _make_source()
        offer = _offer()
        offer["typesOfContracts"] = [{
            "salary": {
                "from": 22000,
                "to": 26000,
                "currencySymbol": "zł",
                "timeUnit": {"shortForm": "mth."},
            }
        }]
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([offer]))
        results = src.search("PHP", "Poland")
        assert len(results) == 1
        assert results[0].source_structured_data["salary_min"] == 22000
        assert results[0].source_structured_data["salary_max"] == 26000
        assert results[0].source_structured_data["salary_currency"] == "PLN"
        assert results[0].source_structured_data["salary_period"] == "monthly"
        assert results[0].source_structured_data["_salary_disclosed"] is True

    def test_stack_required_in_structured_data(self):
        src = _make_source()
        offer = _offer()
        offer["positionLevels"] = [{"value": "senior"}]
        src._page.eval_on_selector.side_effect = [
            json.dumps(_search_payload([offer])),
            json.dumps(_detail_payload({
                "textSections": [{"plainText": "Desc"}],
                "attributes": {"employment": {"typesOfContracts": []}},
                "technologies": {
                    "expected": [{"name": "Python"}, {"name": "Django"}],
                    "optional": [],
                },
            })),
        ]
        results = src.search("PHP", "Poland")
        assert len(results) == 1
        assert results[0].source_structured_data["stack_required"] == ["Python", "Django"]

    def test_stack_preferred_in_structured_data(self):
        src = _make_source()
        offer = _offer()
        src._page.eval_on_selector.side_effect = [
            json.dumps(_search_payload([offer])),
            json.dumps(_detail_payload({
                "textSections": [{"plainText": "Desc"}],
                "attributes": {"employment": {"typesOfContracts": []}},
                "technologies": {
                    "expected": [{"name": "Python"}],
                    "optional": [{"name": "React"}, {"name": "TypeScript"}],
                },
            })),
        ]
        results = src.search("PHP", "Poland")
        assert len(results) == 1
        assert results[0].source_structured_data["stack_required"] == ["Python"]
        assert results[0].source_structured_data["stack_preferred"] == ["React", "TypeScript"]

    def test_manager_seniority_not_in_structured_data(self):
        src = _make_source()
        offer = _offer()
        offer["positionLevels"] = [{"value": "manager"}]
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([offer]))
        results = src.search("PHP", "Poland")
        assert len(results) == 1
        assert "seniority" not in results[0].source_structured_data

    def test_remote_fields_preserved_with_structured_data(self):
        src = _make_source()
        offer = _offer(work_modes=["remote"])
        offer["positionLevels"] = [{"value": "senior"}]
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([offer]))
        results = src.search("PHP", "Poland")
        assert len(results) == 1
        assert results[0].source_structured_data["remote"] is True
        assert results[0].source_structured_data["remote_available"] is True
        assert "remote_regions" not in results[0].source_structured_data
        assert results[0].source_structured_data["seniority"] == "senior"

    def test_all_structured_data_fields_combined(self):
        src = _make_source()
        offer = _offer(work_modes=["remote"])
        offer["positionLevels"] = [{"value": "senior"}]
        offer["typesOfContracts"] = [{
            "salary": {
                "from": 22000,
                "to": 26000,
                "currencySymbol": "zł",
                "timeUnit": {"shortForm": "mth."},
            }
        }]
        src._page.eval_on_selector.side_effect = [
            json.dumps(_search_payload([offer])),
            json.dumps(_detail_payload({
                "textSections": [{"plainText": "Desc"}],
                "attributes": {"employment": {"typesOfContracts": []}},
                "technologies": {
                    "expected": [{"name": "Python"}],
                    "optional": [{"name": "Django"}],
                },
            })),
        ]
        results = src.search("PHP", "Poland")
        assert len(results) == 1
        data = results[0].source_structured_data
        assert data["remote"] is True
        assert data["remote_available"] is True
        assert "remote_regions" not in data
        assert data["seniority"] == "senior"
        assert data["salary_min"] == 22000
        assert data["salary_max"] == 26000
        assert data["salary_currency"] == "PLN"
        assert data["salary_period"] == "monthly"
        assert data["_salary_disclosed"] is True
        assert data["stack_required"] == ["Python"]
        assert data["stack_preferred"] == ["Django"]
