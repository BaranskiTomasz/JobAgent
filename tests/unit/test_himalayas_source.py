"""Unit tests for the Himalayas.app RSS source, no real HTTP calls made."""
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import MagicMock

from collector.sources.himalayas import HimalayasSource


def _item_xml(
    link="https://himalayas.app/companies/acme/jobs/php-developer",
    title="PHP Developer",
    company="Acme",
    categories: list[str] | None = None,
    description="<p>Great role</p>",
    content_encoded: str | None = None,
    days_ago: int = 0,
    include_pub_date: bool = True,
) -> str:
    pub_date_tag = ""
    if include_pub_date:
        pub_date = format_datetime(datetime.now(timezone.utc) - timedelta(days=days_ago))
        pub_date_tag = f"<pubDate>{pub_date}</pubDate>"
    categories_xml = "".join(f"<category><![CDATA[{c}]]></category>" for c in (categories or []))
    content_xml = (
        f"<content:encoded><![CDATA[{content_encoded}]]></content:encoded>"
        if content_encoded is not None else ""
    )
    return f"""
        <item>
            <title><![CDATA[{title}]]></title>
            <link>{link}</link>
            <guid isPermaLink="true">{link}</guid>
            {categories_xml}
            {pub_date_tag}
            <himalayasJobs:companyName>{company}</himalayasJobs:companyName>
            <description><![CDATA[{description}]]></description>
            {content_xml}
        </item>
    """


def _feed_xml(items: list[str]) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
        <rss xmlns:himalayasJobs="https://himalayas.app/ns/jobs"
             xmlns:content="http://purl.org/rss/1.0/modules/content/"
             version="2.0">
            <channel>
                <title>Remote jobs from Himalayas</title>
                {"".join(items)}
            </channel>
        </rss>
    """


def _build_source(items_xml: list[str], days_back: int = 7) -> HimalayasSource:
    src = HimalayasSource(days_back=days_back)
    src._feed_cache = None
    root = ET.fromstring(_feed_xml(items_xml))
    src._fetch_items = MagicMock(return_value=root.find("channel").findall("item"))
    return src


class TestHimalayasSourceSearch:
    def test_returns_matching_job(self):
        src = _build_source([_item_xml()])
        results = src.search("PHP", "Remote")
        assert len(results) == 1
        assert results[0].title == "PHP Developer"
        assert results[0].company == "Acme"
        assert results[0].source == "himalayas"

    def test_source_id_from_link(self):
        src = _build_source([_item_xml(link="https://himalayas.app/companies/acme/jobs/php-1")])
        results = src.search("PHP", "Remote")
        assert results[0].source_id == "php-1"

    def test_filters_by_keyword_in_title(self):
        php = _item_xml(title="PHP Developer", link="https://himalayas.app/companies/acme/jobs/1")
        py  = _item_xml(title="Python Developer", link="https://himalayas.app/companies/acme/jobs/2")
        src = _build_source([php, py])
        results = src.search("PHP", "Remote")
        assert len(results) == 1
        assert results[0].title == "PHP Developer"

    def test_filters_by_keyword_in_category(self):
        # A job whose title doesn't say PHP but is tagged with it should still match,
        # mirroring remotive.py's title-or-tags matching.
        job = _item_xml(title="Backend Engineer", categories=["PHP-Developer", "Backend-Engineer"])
        src = _build_source([job])
        results = src.search("PHP", "Remote")
        assert len(results) == 1

    def test_excludes_job_with_no_keyword_match(self):
        job = _item_xml(title="Python Developer", categories=["Python-Developer"])
        src = _build_source([job])
        results = src.search("PHP", "Remote")
        assert results == []

    def test_skips_known_urls(self):
        url = "https://himalayas.app/companies/acme/jobs/dup"
        src = _build_source([_item_xml(link=url)])
        results = src.search("PHP", "Remote", known_urls={url})
        assert results == []

    def test_filters_by_date(self):
        fresh = _item_xml(link="https://himalayas.app/companies/acme/jobs/fresh", days_ago=1)
        old   = _item_xml(link="https://himalayas.app/companies/acme/jobs/old", days_ago=30)
        src = _build_source([fresh, old], days_back=7)
        results = src.search("PHP", "Remote")
        assert len(results) == 1
        assert results[0].url.endswith("/fresh")

    def test_respects_max_results(self):
        items = [_item_xml(link=f"https://himalayas.app/companies/acme/jobs/{i}") for i in range(5)]
        src = _build_source(items)
        results = src.search("PHP", "Remote", max_results=2)
        assert len(results) == 2

    def test_location_is_always_remote(self):
        src = _build_source([_item_xml()])
        results = src.search("PHP", "Poland")
        assert len(results) == 1
        assert results[0].location == "Remote"

    def test_prefers_content_encoded_over_description(self):
        src = _build_source([_item_xml(description="<p>Short preview</p>", content_encoded="<p>Full rich description</p>")])
        results = src.search("PHP", "Remote")
        assert results[0].description == "Full rich description"

    def test_falls_back_to_description_when_no_content_encoded(self):
        src = _build_source([_item_xml(description="<p>Only a preview</p>", content_encoded=None)])
        results = src.search("PHP", "Remote")
        assert results[0].description == "Only a preview"

    def test_empty_feed_returns_empty(self):
        src = _build_source([])
        results = src.search("PHP", "Remote")
        assert results == []

    def test_posted_at_captures_the_publication_date(self):
        # Unlike weworkremotely.py's feed, this one's pubDate is trustworthy
        # (verified live: correctly descending, matching lastBuildDate), so it's
        # surfaced as posted_at rather than discarded.
        src = _build_source([_item_xml(days_ago=3)])
        results = src.search("PHP", "Remote")
        assert results[0].posted_at is not None
        posted = datetime.fromisoformat(results[0].posted_at)
        assert (datetime.now(timezone.utc) - posted).days == 3

    def test_missing_pub_date_never_crashes_and_posted_at_is_none(self):
        src = _build_source([_item_xml(include_pub_date=False)])
        results = src.search("PHP", "Remote")
        assert len(results) == 1
        assert results[0].posted_at is None

    def test_http_error_returns_empty(self, mocker):
        mocker.patch("httpx.get", side_effect=Exception("connection error"))
        src = HimalayasSource()
        results = src.search("PHP", "Remote")
        assert results == []
