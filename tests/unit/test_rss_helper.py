from datetime import timezone
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree as ET

import httpx
import pytest

from collector.sources.rss import fetch_jobposting_ld_json, fetch_rss_items, parse_pub_date


def _response(*, content: bytes = b"", text: str = "") -> MagicMock:
    response = MagicMock()
    response.content = content
    response.text = text
    response.raise_for_status.return_value = None
    return response


def test_fetch_rss_items_parses_valid_feed():
    response = _response(content=b"<rss><channel><item><title>Role</title></item></channel></rss>")
    with patch("collector.sources.rss.httpx.get", return_value=response):
        items = fetch_rss_items("https://example.com/feed")
    assert len(items) == 1
    assert items[0].findtext("title") == "Role"


@pytest.mark.parametrize("side_effect", [httpx.ConnectError("offline"), None])
def test_fetch_rss_items_raises_when_feed_fails(side_effect):
    response = _response(content=b"not xml")
    with patch("collector.sources.rss.httpx.get", side_effect=side_effect, return_value=response):
        with pytest.raises(RuntimeError):
            fetch_rss_items("https://example.com/feed")


def test_parse_pub_date_is_timezone_aware_and_handles_malformed_value():
    valid = ET.fromstring("<item><pubDate>Mon, 09 Nov 2026 12:22:23 +0200</pubDate></item>")
    invalid = ET.fromstring("<item><pubDate>not-a-date</pubDate></item>")
    assert parse_pub_date(valid).tzinfo is not None
    assert parse_pub_date(valid).astimezone(timezone.utc).hour == 10
    assert parse_pub_date(invalid) is None


def test_fetch_jobposting_ld_json_selects_jobposting_block():
    html = """
    <script type="application/ld+json">{"@type":"Organization","name":"Example"}</script>
    <script type="application/ld+json">{"@type":"JobPosting","title":"QA Engineer"}</script>
    """
    with patch("collector.sources.rss.httpx.get", return_value=_response(text=html)):
        payload = fetch_jobposting_ld_json("https://example.com/job")
    assert payload == {"@type": "JobPosting", "title": "QA Engineer"}


def test_fetch_jobposting_ld_json_returns_none_when_absent():
    with patch("collector.sources.rss.httpx.get", return_value=_response(text="<html></html>")):
        assert fetch_jobposting_ld_json("https://example.com/job") is None
