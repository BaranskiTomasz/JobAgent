"""Unit tests for the justjoin.it public JSON API source."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from collector.sources.justjoin import (
    JustJoinSource,
    _DETAIL_API_URL,
    _LISTING_URL,
    _extract_source_structured_data,
)


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


def _response(data, *, next_cursor=None, status_code=200):
    response = MagicMock(status_code=status_code)
    response.json.return_value = {"data": data, "meta": {"next": {"cursor": next_cursor}}}
    return response


def _make_source(*, catalog=False) -> JustJoinSource:
    source = JustJoinSource(catalog=catalog)
    source._client = MagicMock()
    return source


def _api_side_effect(pages, details=None, failure_at=None):
    details = details or {}

    def get(url, params=None):
        if url == _LISTING_URL:
            cursor = params["from"]
            if cursor == failure_at:
                raise RuntimeError("listing network error")
            return pages[cursor]
        slug = url.rsplit("/", 1)[-1]
        response = MagicMock(status_code=200)
        response.json.return_value = details.get(slug, {"body": "<p>Detail text</p>"})
        return response

    return get


class TestJustJoinSourceSearch:
    def test_non_poland_location_returns_empty_without_request(self):
        source = _make_source()
        assert source.search("PHP Developer", "Germany") == []
        source._client.get.assert_not_called()
        assert source.last_search_diagnostics["source_status"] == "skipped"

    def test_paginates_to_completion_and_uses_json_detail_body(self):
        source = _make_source()
        first_page = [_offer(slug=f"noise-{index}", title="Java Developer", guid=f"noise-{index}") for index in range(99)]
        first_page.append(_offer(slug="first", guid="first"))
        pages = {
            0: _response(first_page, next_cursor=100),
            100: _response([_offer(slug="second", guid="second")]),
        }
        source._client.get.side_effect = _api_side_effect(
            pages, {"first": {"body": "<p>First <strong>description</strong></p>"}, "second": {"body": "<div>Second description</div>"}}
        )

        results = source.search("PHP Developer", "Poland")

        assert [result.url.rsplit("/", 1)[-1] for result in results] == ["first", "second"]
        assert results[0].description == "First description"
        listing_calls = [call for call in source._client.get.call_args_list if call.args[0] == _LISTING_URL]
        assert [call.kwargs["params"]["from"] for call in listing_calls] == [0, 100]
        assert source.last_search_diagnostics["pages_fetched"] == 2

    def test_catalog_filters_hybrid_and_office_before_detail_request(self):
        source = _make_source(catalog=True)
        offers = [_offer(slug="remote", guid="remote"), _offer(slug="hybrid", guid="hybrid", workplace_type="hybrid"), _offer(slug="office", guid="office", workplace_type="office")]
        source._client.get.side_effect = _api_side_effect({0: _response(offers)})

        results = source.search("PHP Developer", "Poland")

        assert [result.url.rsplit("/", 1)[-1] for result in results] == ["remote"]
        detail_calls = [call.args[0] for call in source._client.get.call_args_list if call.args[0] != _LISTING_URL]
        assert detail_calls == [_DETAIL_API_URL.format(slug="remote")]

    def test_non_catalog_keeps_hybrid_and_office(self):
        source = _make_source(catalog=False)
        offers = [_offer(slug="hybrid", guid="hybrid", workplace_type="hybrid"), _offer(slug="office", guid="office", workplace_type="office")]
        source._client.get.side_effect = _api_side_effect({0: _response(offers)})

        results = source.search("PHP Developer", "Poland")

        assert [result.url.rsplit("/", 1)[-1] for result in results] == ["hybrid", "office"]
        assert results[0].location == "Warszawa, Poland (Hybrid)"
        assert results[1].location == "Warszawa, Poland"

    def test_date_and_known_url_filters_skip_detail_requests(self):
        source = _make_source()
        offers = [_offer(slug="fresh", guid="fresh"), _offer(slug="old", guid="old", days_ago=30), _offer(slug="known", guid="known")]
        source._client.get.side_effect = _api_side_effect({0: _response(offers)})

        results = source.search(
            "PHP Developer", "Poland", days_back=7,
            known_urls={"https://JUSTJOIN.IT/job-offer/known/?tracking=yes"},
        )

        assert [result.url.rsplit("/", 1)[-1] for result in results] == ["fresh"]
        detail_calls = [call.args[0] for call in source._client.get.call_args_list if call.args[0] != _LISTING_URL]
        assert detail_calls == [_DETAIL_API_URL.format(slug="fresh")]
        assert source.last_search_diagnostics["known_url_filtered"] == 1

    def test_detail_is_requested_only_after_every_listing_filter(self):
        source = _make_source(catalog=True)
        offers = [
            _offer(slug="match", guid="match"),
            _offer(slug="wrong-title", guid="wrong-title", title="Java Developer"),
            _offer(slug="old", guid="old", days_ago=20),
            _offer(slug="office", guid="office", workplace_type="office"),
            _offer(slug="known", guid="known"),
        ]
        source._client.get.side_effect = _api_side_effect({0: _response(offers)})

        results = source.search("PHP Developer", "Poland", known_urls={"https://justjoin.it/job-offer/known"})

        assert len(results) == 1
        detail_calls = [call for call in source._client.get.call_args_list if call.args[0] != _LISTING_URL]
        assert len(detail_calls) == 1
        assert detail_calls[0].args[0] == _DETAIL_API_URL.format(slug="match")

    def test_partial_pagination_returns_prior_candidates_and_diagnostics(self):
        source = _make_source()
        page = [_offer(slug=f"noise-{index}", title="Java Developer", guid=f"noise-{index}") for index in range(99)]
        page.append(_offer(slug="first", guid="first"))
        source._client.get.side_effect = _api_side_effect({0: _response(page, next_cursor=100)}, failure_at=100)

        results = source.search("PHP Developer", "Poland")

        assert [result.url.rsplit("/", 1)[-1] for result in results] == ["first"]
        assert source.last_search_diagnostics["source_status"] == "partial"
        assert source.last_search_diagnostics["source_error"]
        assert source.last_search_diagnostics["pages_fetched"] == 1

    def test_posted_at_is_preserved_without_inferring_remote_eligibility(self):
        source = _make_source()
        source._client.get.side_effect = _api_side_effect({0: _response([_offer(days_ago=3)])})
        result = source.search("PHP Developer", "Poland")[0]

        assert result.posted_at is not None
        assert (datetime.now(timezone.utc) - datetime.fromisoformat(result.posted_at)).days == 3
        assert "remote_regions" not in result.source_structured_data

    def test_remote_offer_discloses_remote_fact_without_claiming_a_country(self):
        # workplaceType=="remote" is a disclosed fact worth keeping natively
        # (saves Haiku re-deriving it from the description), but NOT evidence
        # of which country the role is open to - a real justjoin.it listing
        # with workplaceType=="remote" had city "Stockholm City" (verified
        # live). extractor/runner.py's eligibility gate treats any PL/BG name
        # inside remote_regions as outright eligible=True, bypassing the real
        # evidence check - so remote_regions must stay absent even though
        # remote/remote_available are safe to assert.
        source = _make_source()
        source._client.get.side_effect = _api_side_effect(
            {0: _response([_offer(workplace_type="remote")])}
        )
        result = source.search("PHP Developer", "Poland")[0]

        assert result.source_structured_data["remote"] is True
        assert result.source_structured_data["remote_available"] is True
        assert "remote_regions" not in result.source_structured_data

    def test_hybrid_and_office_offers_do_not_disclose_remote_fact(self):
        source = _make_source(catalog=False)
        offers = [
            _offer(slug="hybrid", guid="hybrid", workplace_type="hybrid"),
            _offer(slug="office", guid="office", workplace_type="office"),
        ]
        source._client.get.side_effect = _api_side_effect({0: _response(offers)})

        results = source.search("PHP Developer", "Poland")

        for result in results:
            assert "remote" not in result.source_structured_data
            assert "remote_available" not in result.source_structured_data

    def test_fetch_description_overrides_base_default_for_runners_backfill_path(self):
        # collector/runner.py's _fetch_one() backfill retries a job whose
        # description came back empty from search() by calling
        # source.fetch_description(url) directly, by URL - not through
        # search(). JobSource's own default is a no-op returning None, so
        # this source must override it, or every justjoin.it backfill retry
        # silently does nothing forever.
        source = _make_source()
        source._client.get.return_value = MagicMock(
            status_code=200, json=lambda: {"body": "<p>Backfilled description</p>"},
        )

        description = source.fetch_description("https://justjoin.it/job-offer/some-slug/")

        assert description == "Backfilled description"
        source._client.get.assert_called_once_with(_DETAIL_API_URL.format(slug="some-slug"))

    def test_listing_pagination_is_cached_across_queries_in_one_session(self):
        # collector/runner.py calls search() once per catalog query (18 for
        # a full catalog run) within one `with source:` session - a live run
        # against the real API timed out at 600s before this cache existed,
        # because every query re-paginated the same ~10k-listing set from
        # scratch. Each search() call below is a different query/title, same
        # session: the listing endpoint must be hit only once in total.
        source = _make_source()
        source._client.get.side_effect = _api_side_effect(
            {0: _response([_offer(slug="a", guid="a", title="PHP Developer")])}
        )

        source.search("PHP Developer", "Poland")
        source.search("Python Developer", "Poland")
        source.search("Java Developer", "Poland")

        listing_calls = [c for c in source._client.get.call_args_list if c.args[0] == _LISTING_URL]
        assert len(listing_calls) == 1

    def test_listing_cache_is_cleared_on_a_new_session(self):
        source = _make_source()
        source._client.get.side_effect = _api_side_effect(
            {0: _response([_offer(slug="a", guid="a")])}
        )
        source.search("PHP Developer", "Poland")
        source.__exit__()
        source._client = MagicMock()
        source._client.get.side_effect = _api_side_effect(
            {0: _response([_offer(slug="b", guid="b")])}
        )

        source.search("PHP Developer", "Poland")

        listing_calls = [c for c in source._client.get.call_args_list if c.args[0] == _LISTING_URL]
        assert len(listing_calls) == 1


class TestExtractSourceStructuredData:
    def test_extracts_salary_and_skills(self):
        data = _extract_source_structured_data({
            "experienceLevel": "senior",
            "employmentTypes": [
                {"from": 15000, "to": 25000, "currency": "PLN", "currencySource": "original", "unit": "month"},
                {"from": 3500, "to": 5800, "currency": "EUR", "currencySource": "conversion", "unit": "month"},
            ],
            "requiredSkills": [{"name": "PHP"}],
            "niceToHaveSkills": [{"name": "Docker"}],
        })
        assert data["seniority"] == "senior"
        assert data["salary_min"] == 15000
        assert data["salary_max"] == 25000
        assert data["salary_currency"] == "PLN"
        assert data["stack_required"] == [{"name": "PHP"}]

    def test_skips_unknown_salary_or_seniority_values(self):
        assert _extract_source_structured_data({
            "experienceLevel": "c-level",
            "employmentTypes": [{"from": 1, "to": 2, "currency": "CHF", "unit": "sprint"}],
        }) == {"_salary_disclosed": False}
