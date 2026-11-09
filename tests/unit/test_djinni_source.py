from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from xml.etree import ElementTree as ET

from collector.sources.djinni import DjinniSource


def _item(url: str, *, age_days: int = 0) -> ET.Element:
    published = (datetime.now(timezone.utc) - timedelta(days=age_days)).strftime("%a, %d %b %Y %H:%M:%S %z")
    element = ET.Element("item")
    ET.SubElement(element, "link").text = url
    ET.SubElement(element, "pubDate").text = published
    return element


def _payload() -> dict:
    return {
        "@type": "JobPosting",
        "title": "UI/UX Designer",
        "hiringOrganization": {"name": "AnvilEight"},
        "jobLocationType": "TELECOMMUTE",
        "applicantLocationRequirements": {"address": {"addressCountry": "UA"}},
        "datePosted": "2026-11-09T12:29:47",
        "description": "<p>Design product interfaces.</p>",
    }


def test_requests_narrowed_feed_and_maps_page_data():
    url = "https://djinni.co/jobs/123-ui-ux-designer/"
    with patch("collector.sources.djinni.fetch_rss_items", return_value=[_item(url)]) as fetch, \
         patch("collector.sources.djinni.fetch_jobposting_ld_json", return_value=_payload()):
        jobs = DjinniSource().search("QA Engineer", "Ukraine")
    assert "primary_keyword=QA" in fetch.call_args.args[0]
    assert jobs[0].company == "AnvilEight"
    assert jobs[0].source_id == "123"
    assert jobs[0].description == "Design product interfaces."
    assert jobs[0].source_structured_data["remote_available"] is True
    assert jobs[0].source_structured_data["remote_regions"] == ["Ukraine"]


def test_unmapped_query_skips_feed_request():
    source = DjinniSource()
    with patch("collector.sources.djinni.fetch_rss_items") as fetch:
        assert source.search("Security Architect", "Ukraine") == []
    fetch.assert_not_called()
    assert source.last_search_diagnostics["source_status"] == "empty"


def test_missing_page_data_is_skipped_without_empty_company():
    with patch("collector.sources.djinni.fetch_rss_items", return_value=[_item("https://djinni.co/jobs/123-role/")]), \
         patch("collector.sources.djinni.fetch_jobposting_ld_json", return_value=None):
        assert DjinniSource().search("QA Engineer", "Ukraine") == []


def test_page_fetch_only_runs_after_date_and_known_url_filters():
    known = "https://djinni.co/jobs/1-known/"
    fresh = "https://djinni.co/jobs/2-fresh/"
    items = [_item(known), _item("https://djinni.co/jobs/3-old/", age_days=30), _item(fresh)]
    with patch("collector.sources.djinni.fetch_rss_items", return_value=items), \
         patch("collector.sources.djinni.fetch_jobposting_ld_json", return_value=_payload()) as page_fetch:
        jobs = DjinniSource(days_back=7).search("QA Engineer", "Ukraine", known_urls={known})
    page_fetch.assert_called_once_with(fresh)
    assert len(jobs) == 1


def test_page_http_error_skips_item_and_marks_empty():
    source = DjinniSource()
    with patch("collector.sources.djinni.fetch_rss_items", return_value=[_item("https://djinni.co/jobs/123-role/")]), \
         patch("collector.sources.djinni.fetch_jobposting_ld_json", return_value=None):
        assert source.search("QA Engineer", "Ukraine") == []
    assert source.last_search_diagnostics["source_status"] == "empty"
