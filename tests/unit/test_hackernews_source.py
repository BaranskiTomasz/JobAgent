from datetime import datetime, timezone
from unittest.mock import MagicMock

from collector.sources.hackernews import HackerNewsSource, _remote_region


def _comment(comment_id=1, text="Acme | REMOTE Europe | Python Engineer"):
    return {
        "id": comment_id,
        "text": text,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def test_remote_region_recognizes_poland_compatible_scopes():
    assert _remote_region("Acme | REMOTE | Worldwide") == "Worldwide"
    assert _remote_region("Acme | REMOTE (EMEA)") == "EMEA"
    assert _remote_region("Acme | Europe | REMOTE") == "Europe"
    assert _remote_region("Acme | Poland | REMOTE") == "Poland"
    assert _remote_region("Acme | REMOTE US") is None
    assert _remote_region("Acme | London") is None


def test_search_maps_matching_comment():
    source = HackerNewsSource()
    source._fetch_comments = MagicMock(return_value=[_comment()])
    result = source.search("Python", "Poland")[0]
    assert result.source == "hackernews"
    assert result.company == "Acme"
    assert result.location == "Europe"
    assert result.url == "https://news.ycombinator.com/item?id=1"
    assert result.source_structured_data == {
        "remote": True,
        "remote_available": True,
        "remote_regions": ["Europe"],
    }


def test_search_filters_keyword_region_and_known_urls():
    source = HackerNewsSource()
    source._fetch_comments = MagicMock(return_value=[
        _comment(1, "Acme | REMOTE Europe | Go Engineer"),
        _comment(2, "Beta | REMOTE US | Python Engineer"),
        _comment(3, "Gamma | REMOTE Worldwide | Python Engineer"),
    ])
    result = source.search(
        "Python", "Poland", known_urls={"https://news.ycombinator.com/item?id=3"},
    )
    assert result == []


def test_fetches_latest_monthly_thread_once():
    search_response = MagicMock()
    search_response.json.return_value = {"hits": [{
        "title": "Ask HN: Who is hiring? (September 2026)",
        "objectID": "123",
    }]}
    item_response = MagicMock()
    item_response.json.return_value = {"children": [_comment()]}
    source = HackerNewsSource()
    source._client = MagicMock()
    source._client.get.side_effect = [search_response, item_response]
    assert source._fetch_comments() == source._fetch_comments()
    assert source._client.get.call_count == 2


def test_selects_newest_matching_thread_and_reports_diagnostics():
    search_response = MagicMock()
    search_response.json.return_value = {"hits": [
        {"title": "Ask HN: Who is hiring? (August 2026)", "objectID": "old", "created_at_i": 10},
        {"title": "Ask HN: Who is hiring? (September 2026)", "objectID": "new", "created_at_i": 20},
    ]}
    item_response = MagicMock()
    item_response.json.return_value = {"children": [_comment(7, "Acme | REMOTE Bulgaria | Python Engineer")]}
    source = HackerNewsSource()
    source._client = MagicMock()
    source._client.get.side_effect = [search_response, item_response]
    assert source.search("Python", "Bulgaria")[0].location == "Bulgaria"
    assert source.last_search_diagnostics["thread_id"] == "new"
    assert source.last_search_diagnostics["source_status"] == "ok"


def test_known_url_filter_ignores_trailing_slash_and_query_order():
    source = HackerNewsSource()
    source._fetch_comments = MagicMock(return_value=[_comment(42)])
    assert source.search("Python", "Poland", known_urls={"https://news.ycombinator.com/item/?id=42"}) == []


def test_requested_date_window_is_not_expanded_to_forty_days():
    source = HackerNewsSource(days_back=7)
    old = _comment()
    old["created_at"] = "2026-08-01T00:00:00+00:00"
    source._fetch_comments = MagicMock(return_value=[old])

    assert source.search("Python", "Poland") == []


def test_worldwide_excluding_candidate_is_rejected():
    source = HackerNewsSource()
    source._fetch_comments = MagicMock(return_value=[
        _comment(text="Acme | REMOTE Worldwide except Poland | Python Engineer")
    ])

    assert source.search("Python", "Poland") == []
