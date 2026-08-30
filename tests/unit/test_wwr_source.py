"""Unit tests for the We Work Remotely source."""
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest

from collector.sources.weworkremotely import (
    WWRSource,
    _region_matches,
)
from collector.utils import strip_html as _strip_html


# ── Helpers ───────────────────────────────────────────────────────────────────

def _recent_pub_date() -> str:
    """A pubDate that's always within the default 7-day search window, regardless of
    when the test suite happens to run (a hardcoded absolute date here previously went
    stale and started failing every "days_back" filter test once real time caught up)."""
    return (datetime.now(tz=timezone.utc) - timedelta(days=1)).strftime("%a, %d %b %Y %H:%M:%S +0000")


def _make_feed(items: list[dict]) -> str:
    """Build a minimal RSS feed XML string from a list of item dicts."""
    items_xml = ""
    for it in items:
        title = it.get("title", "AcmeCo: PHP Developer")
        region = it.get("region", "Anywhere in the World")
        link = it.get("link", "https://weworkremotely.com/remote-jobs/acmeco-php-developer")
        pub_date = it.get("pubDate") or _recent_pub_date()
        description = it.get("description", "<p>Some description</p>")
        items_xml += f"""
        <item>
            <title>{title}</title>
            <region>{region}</region>
            <link>{link}</link>
            <guid>{link}</guid>
            <pubDate>{pub_date}</pubDate>
            <description><![CDATA[{description}]]></description>
        </item>
        """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>We Work Remotely: Remote Programming Jobs</title>
    {items_xml}
  </channel>
</rss>"""


def _mock_response(text: str, status_code: int = 200):
    resp = MagicMock()
    resp.text = text
    resp.status_code = status_code
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        resp.raise_for_status.return_value = None
    return resp


# ── TestRegionMatches ─────────────────────────────────────────────────────────

class TestRegionMatches:
    def test_empty_region_matches_any(self):
        assert _region_matches("", "Poland") is True

    def test_anywhere_in_world_matches_any(self):
        assert _region_matches("Anywhere in the World", "Poland") is True

    def test_europe_only_matches_eu_country(self):
        assert _region_matches("Europe Only", "Poland") is True

    def test_europe_only_matches_germany(self):
        assert _region_matches("Europe Only", "Germany") is True

    def test_europe_only_does_not_match_usa(self):
        assert _region_matches("Europe Only", "United States") is False

    def test_usa_only_matches_us(self):
        assert _region_matches("USA Only", "United States") is True

    def test_usa_only_does_not_match_poland(self):
        assert _region_matches("USA Only", "Poland") is False

    def test_us_state_does_not_match_poland(self):
        assert _region_matches("Texas", "Poland") is False

    def test_specific_country_match(self):
        assert _region_matches("Germany", "Germany") is True

    def test_alias_pl_matches_europe_only(self):
        # "pl" alias → "poland" which is in _EU_COUNTRIES
        assert _region_matches("Europe Only", "pl") is True

    def test_remote_search_matches_worldwide(self):
        assert _region_matches("Anywhere in the World", "Remote") is True

    def test_europe_only_matches_remote_search(self):
        assert _region_matches("Europe Only", "Remote") is True


# ── TestStripHtml ─────────────────────────────────────────────────────────────

class TestStripHtml:
    def test_strips_tags(self):
        result = _strip_html("<p>Hello</p>")
        assert result == "Hello"

    def test_none_on_empty(self):
        assert _strip_html("") is None

    def test_none_on_whitespace_only(self):
        assert _strip_html("   ") is None

    def test_br_adds_space(self):
        result = _strip_html("line1<br>line2")
        # After joining and squishing whitespace the newline becomes a space
        assert "line1" in result
        assert "line2" in result


# ── TestWWRSourceSearch ───────────────────────────────────────────────────────

class TestWWRSourceSearch:
    def test_returns_matching_job(self, mocker):
        feed = _make_feed([{"title": "AcmeCo: PHP Developer", "region": "Anywhere in the World"}])
        mocker.patch("httpx.get", return_value=_mock_response(feed))

        jobs = WWRSource().search("PHP", "Remote")
        assert len(jobs) == 1
        assert "PHP" in jobs[0].title

    def test_title_keyword_filter(self, mocker):
        feed = _make_feed([{"title": "AcmeCo: Java Engineer", "region": "Anywhere in the World"}])
        mocker.patch("httpx.get", return_value=_mock_response(feed))

        jobs = WWRSource().search("PHP", "Remote")
        assert jobs == []

    def test_old_pub_date_is_not_filtered_out(self, mocker):
        # Regression: this feed's own pubDate is unreliable (verified live,
        # 2026-08-30 - the newest RSS item claimed an 18-day-old publish date while
        # the site's own HTML page showed a "Today" posting), so a days_back cutoff
        # against it silently rejected every single item, every run. The feed only
        # ever returns its ~25 latest postings anyway, so no date filter is applied.
        old_date = (datetime.now(tz=timezone.utc) - timedelta(days=60)).strftime(
            "%a, %d %b %Y %H:%M:%S +0000"
        )
        feed = _make_feed([{"title": "AcmeCo: PHP Dev", "pubDate": old_date}])
        mocker.patch("httpx.get", return_value=_mock_response(feed))

        jobs = WWRSource().search("PHP", "Remote", days_back=30)
        assert len(jobs) == 1

    def test_posted_at_is_always_none(self, mocker):
        # This feed's pubDate can't be trusted (see test above), so posted_at is
        # never populated from it rather than risk feeding the ranker a wrong age.
        feed = _make_feed([{"title": "AcmeCo: PHP Dev"}])
        mocker.patch("httpx.get", return_value=_mock_response(feed))

        jobs = WWRSource().search("PHP", "Remote")
        assert jobs[0].posted_at is None

    def test_skips_known_urls(self, mocker):
        url = "https://weworkremotely.com/remote-jobs/acmeco-php-developer"
        feed = _make_feed([{"title": "AcmeCo: PHP Developer", "link": url}])
        mocker.patch("httpx.get", return_value=_mock_response(feed))

        jobs = WWRSource().search("PHP", "Remote", known_urls={url})
        assert jobs == []

    def test_location_filter(self, mocker):
        feed = _make_feed([{"title": "AcmeCo: PHP Dev", "region": "USA Only"}])
        mocker.patch("httpx.get", return_value=_mock_response(feed))

        jobs = WWRSource().search("PHP", "Poland")
        assert jobs == []

    def test_max_results_respected(self, mocker):
        items = [
            {"title": f"Co{i}: PHP Dev {i}",
             "link": f"https://weworkremotely.com/remote-jobs/job-{i}",
             "region": "Anywhere in the World"}
            for i in range(5)
        ]
        feed = _make_feed(items)
        mocker.patch("httpx.get", return_value=_mock_response(feed))

        jobs = WWRSource().search("PHP", "Remote", max_results=3)
        assert len(jobs) == 3

    def test_company_extracted_from_title(self, mocker):
        feed = _make_feed([{"title": "AcmeCo: PHP Dev"}])
        mocker.patch("httpx.get", return_value=_mock_response(feed))

        jobs = WWRSource().search("PHP", "Remote")
        assert jobs[0].company == "AcmeCo"
        assert jobs[0].title == "PHP Dev"

    def test_empty_feed_returns_empty(self, mocker):
        feed = _make_feed([])
        mocker.patch("httpx.get", return_value=_mock_response(feed))

        jobs = WWRSource().search("PHP", "Remote")
        assert jobs == []

    def test_http_error_returns_empty(self, mocker):
        mocker.patch("httpx.get", side_effect=Exception("connection error"))

        jobs = WWRSource().search("PHP", "Remote")
        assert jobs == []

    def test_source_id_is_slug(self, mocker):
        url = "https://weworkremotely.com/remote-jobs/acmeco-php-developer"
        feed = _make_feed([{"title": "AcmeCo: PHP Dev", "link": url}])
        mocker.patch("httpx.get", return_value=_mock_response(feed))

        jobs = WWRSource().search("PHP", "Remote")
        assert jobs[0].source_id == "acmeco-php-developer"

    def test_worldwide_region_matches_any_location(self, mocker):
        feed = _make_feed([{"title": "AcmeCo: PHP Dev", "region": "Anywhere in the World"}])
        mocker.patch("httpx.get", return_value=_mock_response(feed))

        jobs = WWRSource().search("PHP", "Poland")
        assert len(jobs) == 1

    def test_http_error_status_returns_empty(self, mocker):
        mocker.patch("httpx.get", return_value=_mock_response("", status_code=503))

        jobs = WWRSource().search("PHP", "Remote")
        assert jobs == []

    def test_source_name_is_weworkremotely(self, mocker):
        feed = _make_feed([{"title": "AcmeCo: PHP Dev"}])
        mocker.patch("httpx.get", return_value=_mock_response(feed))

        jobs = WWRSource().search("PHP", "Remote")
        assert jobs[0].source == "weworkremotely"
