from unittest.mock import patch
from xml.etree import ElementTree as ET

from collector.sources.dou import DouSource


def _item(
    title: str = "Middle Python Engineer в Pine Software, віддалено",
    link: str = "https://jobs.dou.ua/companies/pine-software/vacancies/379273/?utm_source=jobsrss",
) -> ET.Element:
    published = "Mon, 09 Nov 2099 12:22:23 +0200"
    element = ET.Element("item")
    for tag, value in {
        "title": title,
        "link": link,
        "description": "<p>Build Python services.</p>",
        "pubDate": published,
    }.items():
        ET.SubElement(element, tag).text = value
    return element


def test_requests_narrowed_remote_feed_and_maps_real_item():
    with patch("collector.sources.dou.fetch_rss_items", return_value=[_item()]) as fetch:
        jobs = DouSource().search("Python Developer", "Ukraine")
    requested_url = fetch.call_args.args[0]
    assert "category=Python" in requested_url
    assert requested_url.endswith("&remote")
    assert jobs[0].title == "Middle Python Engineer"
    assert jobs[0].company == "Pine Software"
    assert jobs[0].location == "Remote"
    assert jobs[0].url == "https://jobs.dou.ua/companies/pine-software/vacancies/379273/"
    assert jobs[0].source_id == "379273"
    assert jobs[0].source_structured_data["remote_regions"] == ["Ukraine"]


def test_unmapped_query_uses_search_and_never_bare_feed():
    with patch("collector.sources.dou.fetch_rss_items", return_value=[]) as fetch:
        DouSource().search("Security Architect", "Ukraine")
    requested_url = fetch.call_args.args[0]
    assert "search=Security+Architect" in requested_url
    assert requested_url.endswith("&remote")


def test_company_falls_back_to_url_slug():
    with patch("collector.sources.dou.fetch_rss_items", return_value=[_item(title="Python Engineer")]):
        job = DouSource().search("Python Developer", "Ukraine")[0]
    assert job.company == "Pine Software"


def test_empty_company_segment_falls_back_to_url_slug():
    with patch("collector.sources.dou.fetch_rss_items", return_value=[_item(title="Python Engineer в , віддалено")]):
        job = DouSource().search("Python Developer", "Ukraine")[0]
    assert job.company == "Pine Software"


def test_salary_segments_are_not_in_location():
    title = "QA Engineer в Example, до\xa0$6000, Київ"
    with patch("collector.sources.dou.fetch_rss_items", return_value=[_item(title=title)]):
        job = DouSource().search("QA Engineer", "Ukraine")[0]
    assert job.location == "Київ"
    assert "$6000" not in job.location
    assert job.source_structured_data is None


def test_feed_error_returns_empty_with_diagnostic():
    source = DouSource()
    with patch("collector.sources.dou.fetch_rss_items", side_effect=RuntimeError("bad xml")):
        assert source.search("QA Engineer", "Ukraine") == []
    assert source.last_search_diagnostics["source_status"] == "error"
