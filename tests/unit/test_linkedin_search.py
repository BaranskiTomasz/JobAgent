from unittest.mock import MagicMock, patch

from collector.base import RawJob
from collector.sources.linkedin import LinkedInSource


@patch.object(LinkedInSource, "_wait")
@patch.object(LinkedInSource, "_goto")
@patch.object(LinkedInSource, "_collect_cards")
def test_search_drops_unrelated_recommendation_cards(mock_collect, mock_goto, mock_wait):
    mock_collect.return_value = [
        RawJob("Senior PHP Engineer", "Acme", "Poland", "https://example.com/1", "linkedin"),
        RawJob("Paid Media Manager", "Other", "Poland", "https://example.com/2", "linkedin"),
    ]
    source = LinkedInSource()

    results = source.search("PHP Developer", "Poland", max_results=3)

    assert [job.title for job in results] == ["Senior PHP Engineer"]
    assert results[0].source_structured_data == {"remote": True, "remote_regions": ["Poland"]}


@patch.object(LinkedInSource, "_scroll_to_bottom")
@patch.object(LinkedInSource, "_wait")
def test_collection_stops_after_ten_pages(mock_wait, mock_scroll):
    source = LinkedInSource()
    source._page = MagicMock()
    next_button = MagicMock()
    source._page.query_selector.side_effect = lambda selector: (
        None if selector == ".jobs-search-no-results-banner" else next_button
    )
    source._page.evaluate.return_value = [{
        "title": "PHP Engineer",
        "company": "Acme",
        "location": "Poland",
        "url": "https://www.linkedin.com/jobs/view/1",
    }]

    results = source._collect_cards()

    assert len(results) == 10
    assert next_button.click.call_count == 9
