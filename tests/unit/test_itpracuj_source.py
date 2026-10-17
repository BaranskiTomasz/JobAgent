"""Unit tests for the it.pracuj.pl scraper, no real browser calls made."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from collector.sources.itpracuj import (
    ItPracujSource,
    _read_next_data,
    _find_query,
    _parse_salary,
    _parse_seniority,
    _extract_source_structured_data,
    _extract_detail_structured_data,
    _detail_text,
)


def _next_data_page(payload: dict) -> MagicMock:
    page = MagicMock()
    page.eval_on_selector.return_value = json.dumps(payload)
    return page


def _search_payload(groups: list[dict]) -> dict:
    return {
        "props": {"pageProps": {"dehydratedState": {"queries": [
            {"queryKey": ["jobOffers", {}], "state": {"data": {"groupedOffers": groups, "offersTotalCount": len(groups)}}},
        ]}}},
    }


def _group(
    offer_url="https://www.pracuj.pl/praca/php-developer,oferta,1",
    job_title="PHP Developer",
    company="Acme",
    city="Warszawa",
    partition_id=1,
    days_ago=0,
    description="Krótki opis stanowiska...",
    work_modes=None,
    salary_display="",
    position_levels=None,
    technologies=None,
):
    published = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "jobTitle": job_title,
        "companyName": company,
        "lastPublicated": published,
        "groupId": f"group-{partition_id}",
        "jobDescription": description,
        "workModes": work_modes if work_modes is not None else ["Praca zdalna"],
        "salaryDisplayText": salary_display,
        "positionLevels": position_levels if position_levels is not None else [],
        "technologies": technologies if technologies is not None else [],
        "offers": [{"offerAbsoluteUri": offer_url, "displayWorkplace": city, "partitionId": partition_id}],
    }


class TestReadNextData:
    def test_parses_valid_json(self):
        page = _next_data_page({"props": {"pageProps": {}}})
        assert _read_next_data(page) == {"props": {"pageProps": {}}}

    def test_returns_none_on_missing_selector(self):
        page = MagicMock()
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        page.wait_for_selector.side_effect = PlaywrightTimeout("timeout")
        assert _read_next_data(page) is None

    def test_returns_none_on_invalid_json(self):
        page = MagicMock()
        page.eval_on_selector.return_value = "not json"
        assert _read_next_data(page) is None


class TestFindQuery:
    def test_finds_matching_query_by_name(self):
        data = {"props": {"pageProps": {"dehydratedState": {"queries": [
            {"queryKey": ["jobOffers", {}], "state": {"data": {"x": 1}}},
        ]}}}}
        assert _find_query(data, "jobOffers") == {"x": 1}

    def test_returns_none_when_not_found(self):
        data = {"props": {"pageProps": {"dehydratedState": {"queries": []}}}}
        assert _find_query(data, "jobOffers") is None


class TestParsingHelpers:
    def test_parse_salary_hourly(self):
        res = _parse_salary("130–170\xa0zł netto (+\xa0VAT)\xa0/ godz.")
        assert res == {
            "salary_min": 130,
            "salary_max": 170,
            "_salary_disclosed": True,
            "salary_currency": "PLN",
            "salary_period": "hourly",
        }

    def test_parse_salary_monthly_single_val(self):
        res = _parse_salary("15 000 zł / mies.")
        assert res == {
            "salary_min": 15000,
            "salary_max": 15000,
            "_salary_disclosed": True,
            "salary_currency": "PLN",
            "salary_period": "monthly",
        }

    def test_parse_salary_empty_or_invalid(self):
        assert _parse_salary("") == {}
        assert _parse_salary("Wynagrodzenie do uzgodnienia") == {}
        assert _parse_salary("1000 - 2000 JPY / mth") == {}

    def test_parse_seniority_levels(self):
        assert _parse_seniority(["Starszy specjalista / Starsza specjalistka (senior)"]) == "senior"
        assert _parse_seniority(["Młodszy specjalista / Młodsza specjalistka (junior)"]) == "junior"
        assert _parse_seniority(["Specjalista / Specjalistka (mid / Regular)"]) == "mid"
        assert _parse_seniority(["Kierownik / Kierowniczka - koordynator / Koordynatorka"]) == "lead"
        assert _parse_seniority(["Dyrektor / Dyrektorka"]) == "director"
        assert _parse_seniority(["Praktykant / Stażysta"]) is None
        assert _parse_seniority([]) is None

    def test_extract_source_structured_data_technologies_as_strings(self):
        group = {
            "positionLevels": ["senior"],
            "salaryDisplayText": "20 000–25 000 zł / mies.",
            "technologies": ["Python", {"name": "Django"}, {"unknown": "x"}, "PostgreSQL"],
        }
        res = _extract_source_structured_data(group)
        assert res["seniority"] == "senior"
        assert res["salary_min"] == 20000
        assert res["salary_max"] == 25000
        assert res["salary_currency"] == "PLN"
        assert res["salary_period"] == "monthly"
        assert res["_salary_disclosed"] is True
        # Dicts and strings are all reduced to plain hashable strings
        assert res["stack_required"] == ["Python", "Django", "PostgreSQL"]

    def test_extract_detail_structured_data(self):
        offer = {
            "sections": [
                {
                    "sectionType": "technologies",
                    "subSections": [
                        {
                            "sectionType": "technologies-expected",
                            "model": {"customItems": [{"name": "Python"}, {"name": "Docker"}]},
                        },
                        {
                            "sectionType": "technologies-optional",
                            "model": {"customItems": [{"name": "Kubernetes"}]},
                        },
                    ],
                }
            ]
        }
        res = _extract_detail_structured_data(offer)
        assert res == {
            "stack_required": ["Python", "Docker"],
            "stack_preferred": ["Kubernetes"],
        }

    def test_detail_text_joins_text_sections_ignoring_gallery(self):
        offer = {
            "textSections": [
                {"sectionType": "responsibilities", "plainText": "Responsibilities: code"},
                {"sectionType": "about-us-gallery", "plainText": "Should be ignored"},
                {"sectionType": "requirements", "plainText": "Requirements: Python"},
            ]
        }
        text = _detail_text(offer)
        assert text == "Responsibilities: code\n\nRequirements: Python"


def _make_source() -> ItPracujSource:
    src = ItPracujSource()
    src._page = MagicMock()
    return src


class TestItPracujSourceSearch:
    def test_non_poland_location_returns_empty_without_navigation(self):
        src = _make_source()
        results = src.search("PHP Developer", "Germany")
        assert results == []
        src._page.goto.assert_not_called()

    def test_poland_search_returns_mapped_job(self):
        src = _make_source()
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([_group()]))
        results = src.search("PHP Developer", "Poland")
        assert len(results) == 1
        assert results[0].company == "Acme"
        assert results[0].source == "itpracuj"

    def test_full_multiword_title_passed_through_url(self):
        src = _make_source()
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([_group()]))
        src.search("Symfony Developer", "Poland")
        called_url = src._page.goto.call_args[0][0]
        assert "Symfony%20Developer" in called_url

    def test_search_url_does_not_restrict_to_remote_workmode(self):
        src = _make_source()
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([_group()]))
        src.search("PHP", "Poland")
        called_url = src._page.goto.call_args[0][0]
        assert "zdalna" not in called_url
        assert "home-office" not in called_url

    def test_remote_workmode_labeled_remote(self):
        src = _make_source()
        group = _group(city="Warszawa", work_modes=["Praca zdalna"])
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([group]))
        results = src.search("PHP", "Poland")
        assert results[0].location == "Warszawa, Poland (Remote)"

    def test_hybrid_workmode_labeled_hybrid(self):
        src = _make_source()
        group = _group(city="Krakow", work_modes=["Praca hybrydowa"])
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([group]))
        results = src.search("PHP", "Poland")
        assert results[0].location == "Krakow, Poland (Hybrid)"

    def test_onsite_workmode_has_no_suffix(self):
        src = _make_source()
        group = _group(city="Gdansk", work_modes=["Praca stacjonarna"])
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([group]))
        results = src.search("PHP", "Poland")
        assert results[0].location == "Gdansk, Poland"

    def test_description_taken_from_search_payload(self):
        src = _make_source()
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([_group(description="Opis stanowiska...")]))
        results = src.search("PHP", "Poland")
        assert results[0].description == "Opis stanowiska..."

    def test_missing_description_is_none(self):
        src = _make_source()
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([_group(description="")]))
        results = src.search("PHP", "Poland")
        assert results[0].description is None

    def test_skips_known_urls(self):
        src = _make_source()
        src._page.eval_on_selector.return_value = json.dumps(
            _search_payload([_group(offer_url="https://www.pracuj.pl/praca/dup,oferta,1")])
        )
        known = {"https://www.pracuj.pl/praca/dup,oferta,1"}
        results = src.search("PHP", "Poland", known_urls=known)
        assert results == []

    def test_known_url_is_canonical_and_remote_fact_is_retained(self):
        src = _make_source()
        group = _group(offer_url="https://www.pracuj.pl/praca/remote,oferta,1?utm=career")
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([group]))
        result = src.search("PHP", "Poland")[0]
        assert result.url == "https://www.pracuj.pl/praca/remote,oferta,1"
        assert result.source_structured_data == {
            "remote": True,
            "remote_available": True,
        }
        assert src.search("PHP", "Poland", known_urls={"https://www.pracuj.pl/praca/remote,oferta,1/"}) == []

    def test_structured_data_extracted_in_search(self):
        src = _make_source()
        group = _group(
            job_title="Python Developer",
            salary_display="120–150 zł netto (+ VAT) / godz.",
            position_levels=["Starszy specjalista / Starsza specjalistka (senior)"],
            technologies=["Python", "PostgreSQL"],
            work_modes=["Praca zdalna"],
        )
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([group]))
        results = src.search("Python", "Poland")
        assert len(results) == 1
        data = results[0].source_structured_data
        assert data["remote"] is True
        assert data["remote_available"] is True
        assert "remote_regions" not in data
        assert data["seniority"] == "senior"
        assert data["salary_min"] == 120
        assert data["salary_max"] == 150
        assert data["salary_currency"] == "PLN"
        assert data["salary_period"] == "hourly"
        assert data["_salary_disclosed"] is True
        assert data["stack_required"] == ["Python", "PostgreSQL"]

    def test_follows_numbered_pages_with_pn_parameter(self):
        src = _make_source()
        first = _search_payload([_group(offer_url="https://www.pracuj.pl/praca/a,oferta,1", partition_id=1)])
        first["props"]["pageProps"]["dehydratedState"]["queries"][0]["state"]["data"]["offersTotalCount"] = 2
        second = _search_payload([_group(offer_url="https://www.pracuj.pl/praca/b,oferta,2", partition_id=2)])
        src._page.eval_on_selector.side_effect = [json.dumps(first), json.dumps(second)]
        results = src.search("PHP", "Poland")
        assert len(results) == 2
        assert src.last_search_diagnostics["pages_fetched"] == 2
        # Verify pn=2 is used for pagination, not page=2
        src._page.goto.assert_called_with("https://it.pracuj.pl/praca/PHP;kw?pn=2", wait_until="domcontentloaded", timeout=20000)

    def test_filters_by_date(self):
        src = _make_source()
        fresh = _group(offer_url="https://www.pracuj.pl/praca/fresh,oferta,1", days_ago=1)
        old = _group(offer_url="https://www.pracuj.pl/praca/old,oferta,2", days_ago=30)
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([fresh, old]))
        results = src.search("PHP", "Poland", days_back=7)
        assert len(results) == 1
        assert "fresh" in results[0].url

    def test_respects_max_results(self):
        src = _make_source()
        groups = [_group(offer_url=f"https://www.pracuj.pl/praca/job-{i},oferta,{i}", partition_id=i) for i in range(5)]
        src._page.eval_on_selector.return_value = json.dumps(_search_payload(groups))
        results = src.search("PHP", "Poland", max_results=2)
        assert len(results) == 2

    def test_group_without_offers_skipped(self):
        src = _make_source()
        group = _group()
        group["offers"] = []
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([group]))
        results = src.search("PHP", "Poland")
        assert results == []

    def test_empty_title_returns_empty(self):
        src = _make_source()
        results = src.search("   ", "Poland")
        assert results == []
        src._page.goto.assert_not_called()

    def test_goto_timeout_returns_empty(self):
        src = _make_source()
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        src._page.goto.side_effect = PlaywrightTimeout("timeout")
        results = src.search("PHP", "Poland")
        assert results == []

    def test_posted_at_captures_last_publicated(self):
        src = _make_source()
        src._page.eval_on_selector.return_value = json.dumps(_search_payload([_group(days_ago=1)]))
        results = src.search("PHP Developer", "Poland")
        assert results[0].posted_at is not None
        posted = datetime.fromisoformat(results[0].posted_at)
        assert (datetime.now(timezone.utc) - posted).days == 1


class TestItPracujSourceFetchDescription:
    def test_fetch_description_success(self):
        src = _make_source()
        payload = {
            "props": {
                "pageProps": {
                    "dehydratedState": {
                        "queries": [
                            {
                                "queryKey": ["jobOffer", "1005101176", "pl"],
                                "state": {
                                    "data": {
                                        "textSections": [
                                            {"sectionType": "responsibilities", "plainText": "Responsibilities: code"},
                                            {"sectionType": "about-us-gallery", "plainText": "Ignore gallery"},
                                            {"sectionType": "requirements", "plainText": "Requirements: Python"},
                                        ]
                                    }
                                },
                            }
                        ]
                    }
                }
            }
        }
        src._page.eval_on_selector.return_value = json.dumps(payload)
        desc = src.fetch_description("https://www.pracuj.pl/praca/python-dev,oferta,1005101176")
        assert desc == "Responsibilities: code\n\nRequirements: Python"

    def test_fetch_description_returns_none_on_missing_data(self):
        src = _make_source()
        src._page.eval_on_selector.return_value = json.dumps({"props": {"pageProps": {}}})
        desc = src.fetch_description("https://www.pracuj.pl/praca/python-dev,oferta,1005101176")
        assert desc is None

    def test_fetch_description_returns_none_on_timeout(self):
        src = _make_source()
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        src._page.goto.side_effect = PlaywrightTimeout("timeout")
        desc = src.fetch_description("https://www.pracuj.pl/praca/python-dev,oferta,1005101176")
        assert desc is None
