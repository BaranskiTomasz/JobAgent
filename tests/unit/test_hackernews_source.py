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
    assert result.source_structured_data == {"remote": True, "remote_regions": ["Europe"]}


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
