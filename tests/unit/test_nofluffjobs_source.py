"""Unit tests for the NoFluffJobs scraper, no real HTTP calls made."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from collector.sources.nofluffjobs import (
    NoFluffJobsSource, _parse_server_state, _find_postings, _find_description,
    _extract_source_structured_data,
)


def _state_html(inner_json: str) -> str:
    return f'<html><script id="serverApp-state" type="application/json">{inner_json}</script></html>'


class TestParseServerState:
    def test_parses_valid_state(self):
        html = _state_html('{&q;a&q;:1}')
        assert _parse_server_state(html) == {"a": 1}

    def test_returns_none_when_script_missing(self):
        assert _parse_server_state("<html>no state here</html>") is None

    def test_returns_none_on_invalid_json(self):
        html = _state_html("not valid json at all")
        assert _parse_server_state(html) is None

    def test_unescapes_ampersand_and_brackets(self):
        html = _state_html('{&q;text&q;:&q;a &a; b &l;tag&g;&q;}')
        data = _parse_server_state(html)
        assert data == {"text": "a & b <tag>"}


class TestFindPostings:
    def test_finds_postings_in_nested_value(self):
        state = {"SOME_KEY": {"searchResponse": {"postings": [{"id": "1"}]}}}
        assert _find_postings(state) == [{"id": "1"}]

    def test_returns_empty_when_not_found(self):
        assert _find_postings({"SOME_KEY": {"other": 1}}) == []


class TestFindDescription:
    def test_finds_description_html_and_strips_it(self):
        state = {"/posting/foo": {"details": {"description": "<p>Hello <b>world</b></p>"}}}
        assert _find_description(state) == "Hello world"

    def test_returns_none_when_not_found(self):
        assert _find_description({"SOME_KEY": {}}) is None


def test_salary_period_uses_explicit_hourly_unit_from_description():
    posting = {"salary": {"from": 140, "to": 160, "currency": "PLN"}}
    data = _extract_source_structured_data(posting, "Stawka: 140-160 PLN/h + VAT")
    assert data["salary_period"] == "hourly"


def test_salary_period_uses_native_period_when_description_omits_salary():
    posting = {"salary": {"from": 110, "to": 120, "currency": "PLN", "period": "Hour"}}
    data = _extract_source_structured_data(posting, "Opis bez informacji o stawce")
    assert data["salary_period"] == "hourly"


def test_native_period_takes_precedence_over_description():
    posting = {"salary": {"from": 14000, "to": 16000, "currency": "PLN", "period": "Month"}}
    data = _extract_source_structured_data(posting, "Inny fragment wspomina 100 PLN/h")
    assert data["salary_period"] == "monthly"


def test_salary_period_defaults_to_monthly_without_hourly_unit():
    posting = {"salary": {"from": 14000, "to": 16000, "currency": "PLN"}}
    data = _extract_source_structured_data(posting, "Wynagrodzenie miesięczne")
    assert data["salary_period"] == "monthly"


def _posting(
    slug="php-developer-remote", title="PHP Developer", company="Acme", posting_id="abc-1",
    days_ago=0, places=None, seniority=None, tiles=None, technology=None,
):
    posted_ms = int((datetime.now(timezone.utc) - timedelta(days=days_ago)).timestamp() * 1000)
    posting = {"id": posting_id, "name": company, "title": title, "url": slug, "posted": posted_ms}
    if places is not None:
        posting["location"] = {"places": places}
    if seniority is not None:
        posting["seniority"] = seniority
    if tiles is not None:
        posting["tiles"] = {"values": tiles}
    if technology is not None:
        posting["technology"] = technology
    return posting


def _search_html(postings: list[dict]) -> str:
    import json
    # Real state shape wraps searchResponse under a store-specific key (e.g. STORE_KEY
    # or a hashed request identity), _find_postings scans for it, so the wrapper key
    # name itself doesn't matter here, just that it's nested one level deep.
    payload = json.dumps({"STORE_KEY": {"searchResponse": {"postings": postings}}})
    return _state_html(payload.replace('"', "&q;"))


def _make_source() -> NoFluffJobsSource:
    src = NoFluffJobsSource()
    src._client = MagicMock()
    src.fetch_description = MagicMock(return_value="Full description")
    return src


class TestNoFluffJobsSearch:
    def test_non_poland_location_returns_empty_without_request(self):
        src = _make_source()
        results = src.search("PHP", "Germany")
        assert results == []
        src._client.get.assert_not_called()

    def test_poland_search_returns_mapped_job(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, text=_search_html([_posting()]))
        results = src.search("PHP", "Poland")
        assert len(results) == 1
        assert results[0].company == "Acme"
        assert results[0].source == "nofluffjobs"

    def test_url_built_from_slug(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, text=_search_html([_posting(slug="foo-bar")]))
        results = src.search("PHP", "Poland")
        assert results[0].url == "https://nofluffjobs.com/pl/job/foo-bar"

    def test_description_fetched_inline(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, text=_search_html([_posting()]))
        results = src.search("PHP", "Poland")
        assert results[0].description == "Full description"
        src.fetch_description.assert_called_once()

    def test_skips_known_urls(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, text=_search_html([_posting(slug="dup")]))
        known = {"https://nofluffjobs.com/pl/job/dup"}
        results = src.search("PHP", "Poland", known_urls=known)
        assert results == []
        src.fetch_description.assert_not_called()

    def test_known_url_is_canonical_and_remote_fact_is_retained(self):
        src = _make_source()
        posting = _posting(slug="remote-job", places=[{"city": "Remote"}])
        posting.pop("id")
        src._client.get.return_value = MagicMock(status_code=200, text=_search_html([posting]))
        result = src.search("PHP", "Poland")[0]
        assert result.source_id == "https://nofluffjobs.com/pl/job/remote-job"
        assert result.source_structured_data["remote"] is True
        assert result.source_structured_data["remote_available"] is True
        assert src.search("PHP", "Poland", known_urls={result.url + "/?utm=career"}) == []

    def test_remote_regions_is_never_asserted(self):
        # Regression: extractor/runner.py's eligibility gate treats any PL/BG
        # country name found inside source_structured_data["remote_regions"]
        # as outright eligible=True, bypassing the real evidence check. A
        # live sample of 240 NoFluffJobs postings found a genuine Hungary-
        # based "Remote"-tagged listing, so remote_regions must stay absent
        # even though every synthetic fixture here is Poland-labeled.
        src = _make_source()
        posting = _posting(places=[{"city": "Remote"}])
        src._client.get.return_value = MagicMock(status_code=200, text=_search_html([posting]))
        result = src.search("PHP", "Poland")[0]
        assert "remote_regions" not in result.source_structured_data

    def test_technology_string_is_not_split_into_characters(self):
        # Regression: `technology` is a single string (e.g. "Golang"), not a
        # list. Iterating it char-by-char used to turn it into "G o l a n g"
        # for query matching, which breaks the word-boundary phrase match a
        # real Golang-only (title doesn't say "Golang") posting needs.
        src = _make_source()
        posting = _posting(title="Backend Developer", technology="Golang")
        src._client.get.return_value = MagicMock(status_code=200, text=_search_html([posting]))
        results = src.search("Golang", "Poland")
        assert len(results) == 1

    def test_local_query_guard_drops_upstream_noise(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(
            status_code=200, text=_search_html([_posting(title="Java Developer")])
        )
        assert src.search("PHP", "Poland") == []

    def test_filters_by_date(self):
        src = _make_source()
        fresh = _posting(slug="fresh", days_ago=1)
        old = _posting(slug="old", days_ago=30)
        src._client.get.return_value = MagicMock(status_code=200, text=_search_html([fresh, old]))
        results = src.search("PHP", "Poland", days_back=7)
        assert len(results) == 1
        assert "fresh" in results[0].url

    def test_respects_max_results(self):
        src = _make_source()
        postings = [_posting(slug=f"job-{i}") for i in range(5)]
        src._client.get.return_value = MagicMock(status_code=200, text=_search_html(postings))
        results = src.search("PHP", "Poland", max_results=2)
        assert len(results) == 2

    def test_search_does_not_restrict_to_remote_criteria(self):
        # Regression: NoFluffJobs is routed for hybrid/onsite Polish-city
        # candidates too (collector/runner.py's _POLAND_ONLY_SOURCES), so a
        # hardcoded remote=remote criteria silently returned nothing relevant
        # for them.
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, text=_search_html([_posting()]))
        src.search("PHP", "Poland")
        args, kwargs = src._client.get.call_args
        assert "params" not in kwargs

    def test_remote_place_labeled_remote_with_no_real_city(self):
        src = _make_source()
        posting = _posting(places=[{"city": "Remote"}])
        src._client.get.return_value = MagicMock(status_code=200, text=_search_html([posting]))
        results = src.search("PHP", "Poland")
        assert results[0].location == "Poland (Remote)"

    def test_remote_place_with_a_real_city_uses_the_city_and_remote_suffix(self):
        src = _make_source()
        posting = _posting(places=[{"city": "Remote"}, {"city": "Krakow"}])
        src._client.get.return_value = MagicMock(status_code=200, text=_search_html([posting]))
        results = src.search("PHP", "Poland")
        assert results[0].location == "Krakow, Poland (Remote)"

    def test_real_city_without_remote_place_has_no_suffix(self):
        src = _make_source()
        posting = _posting(places=[{"city": "Poznan"}])
        src._client.get.return_value = MagicMock(status_code=200, text=_search_html([posting]))
        results = src.search("PHP", "Poland")
        assert results[0].location == "Poznan, Poland"

    def test_no_location_data_falls_back_to_bare_poland(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, text=_search_html([_posting()]))
        results = src.search("PHP", "Poland")
        assert results[0].location == "Poland"

    def test_non_200_response_returns_empty(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=500, text="")
        results = src.search("PHP", "Poland")
        assert results == []

    def test_request_exception_returns_empty(self):
        src = _make_source()
        src._client.get.side_effect = Exception("network error")
        results = src.search("PHP", "Poland")
        assert results == []

    def test_empty_title_returns_empty(self):
        src = _make_source()
        results = src.search("   ", "Poland")
        assert results == []
        src._client.get.assert_not_called()

    def test_missing_state_returns_empty(self):
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, text="<html></html>")
        results = src.search("PHP", "Poland")
        assert results == []

    def test_source_structured_data_captures_salary(self):
        # Regression: NoFluffJobs discloses salary as a structured search-result
        # field (verified live against the real site), Haiku shouldn't have to
        # re-guess it from the description text later.
        posting = _posting()
        posting["salary"] = {"from": 13000, "to": 15000, "type": "b2b", "currency": "PLN"}
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, text=_search_html([posting]))
        results = src.search("PHP", "Poland")
        ssd = results[0].source_structured_data
        assert ssd["salary_min"] == 13000
        assert ssd["salary_max"] == 15000
        assert ssd["salary_currency"] == "PLN"
        assert ssd["salary_period"] == "monthly"

    def test_source_structured_data_captures_native_seniority_and_stack(self):
        posting = _posting(
            seniority=["Senior"],
            tiles=[{"value": "Python", "type": "requirement"}, {"value": "GCP", "type": "requirement"}],
        )
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, text=_search_html([posting]))
        results = src.search("PHP", "Poland")
        ssd = results[0].source_structured_data
        assert ssd["seniority"] == "senior"
        assert ssd["stack_required"] == ["Python", "GCP"]

    def test_posted_at_captures_the_posted_timestamp(self):
        # "posted" was already parsed for the days_back cutoff, then discarded,
        # RawJob.posted_at carries it through instead.
        src = _make_source()
        src._client.get.return_value = MagicMock(status_code=200, text=_search_html([_posting(days_ago=4)]))
        results = src.search("PHP", "Poland")
        assert results[0].posted_at is not None
        posted = datetime.fromisoformat(results[0].posted_at)
        assert (datetime.now(timezone.utc) - posted).days == 4


class TestExtractSourceStructuredData:
    def test_extracts_salary_as_monthly(self):
        data = _extract_source_structured_data({"salary": {"from": 13000, "to": 15000, "currency": "PLN"}})
        assert data == {"salary_min": 13000, "salary_max": 15000, "salary_currency": "PLN", "salary_period": "monthly", "_salary_disclosed": True}

    def test_undisclosed_salary_returns_empty(self):
        # NoFluffJobs omits from/to entirely when salary is only shown "at first
        # interview", never fabricate a range.
        assert _extract_source_structured_data({"salary": {"type": "b2b", "currency": "PLN"}}) == {"_salary_disclosed": False}

    def test_missing_salary_block_returns_empty(self):
        assert _extract_source_structured_data({}) == {"_salary_disclosed": False}

    def test_unsupported_currency_skipped(self):
        data = _extract_source_structured_data({"salary": {"from": 1000, "to": 2000, "currency": "JPY"}})
        assert data == {"_salary_disclosed": False}

    def test_native_seniority_is_mapped_onto_the_schema_enum(self):
        data = _extract_source_structured_data({"seniority": ["Senior"]})
        assert data["seniority"] == "senior"

    def test_unmapped_seniority_values_are_skipped(self):
        # "Trainee" and "Expert" are real native values (verified live) with
        # no confident mapping onto extractor/runner.py's junior/mid/senior/
        # lead/director enum - never guess, just omit the field.
        assert "seniority" not in _extract_source_structured_data({"seniority": ["Trainee"]})
        assert "seniority" not in _extract_source_structured_data({"seniority": ["Expert"]})

    def test_missing_seniority_is_skipped(self):
        assert "seniority" not in _extract_source_structured_data({})

    def test_required_technologies_captured_as_plain_strings(self):
        posting = {
            "tiles": {"values": [
                {"value": "backend", "type": "category"},
                {"value": "Python", "type": "requirement"},
                {"value": "Linux", "type": "requirement"},
            ]},
        }
        data = _extract_source_structured_data(posting)
        assert data["stack_required"] == ["Python", "Linux"]
        assert "stack_preferred" not in data

    def test_no_requirement_tiles_omits_stack_required(self):
        assert "stack_required" not in _extract_source_structured_data({"tiles": {"values": []}})
        assert "stack_required" not in _extract_source_structured_data({})


class TestNoFluffJobsFetchDescription:
    def test_returns_none_on_non_200(self):
        src = _make_source()
        # unset the mocked fetch_description so we exercise the real method
        src.fetch_description = NoFluffJobsSource.fetch_description.__get__(src)
        src._client.get.return_value = MagicMock(status_code=404, text="")
        assert src.fetch_description("https://nofluffjobs.com/pl/job/foo") is None

    def test_returns_none_on_request_exception(self):
        src = _make_source()
        src.fetch_description = NoFluffJobsSource.fetch_description.__get__(src)
        src._client.get.side_effect = Exception("boom")
        assert src.fetch_description("https://nofluffjobs.com/pl/job/foo") is None

    def test_works_standalone_without_enter(self, monkeypatch):
        # collector/runner.py's generic backfill retry calls fetch_description(url)
        # directly, without necessarily having entered this source first.
        mock_resp = MagicMock(status_code=200, text=_state_html('{&q;STORE_KEY&q;:{&q;details&q;:{&q;description&q;:&q;Hi&q;}}}'))
        mock_client_instance = MagicMock()
        mock_client_instance.get.return_value = mock_resp
        monkeypatch.setattr("collector.sources.nofluffjobs.httpx.Client", MagicMock(return_value=mock_client_instance))

        src = NoFluffJobsSource()
        assert src._client is None
        desc = src.fetch_description("https://nofluffjobs.com/pl/job/foo")
        assert desc == "Hi"
        mock_client_instance.close.assert_called_once()
