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
    assert results[0].source_structured_data == {
        "remote": True,
        "remote_available": True,
        "remote_regions": ["Poland"],
    }


@patch.object(LinkedInSource, "_wait")
@patch.object(LinkedInSource, "_goto")
@patch.object(LinkedInSource, "_collect_cards")
def test_search_encodes_keyword_and_records_diagnostics(mock_collect, mock_goto, mock_wait):
    mock_collect.return_value = [RawJob("Python Engineer", "Acme", "Poland", "https://www.linkedin.com/jobs/view/1?trk=x", "linkedin")]
    source = LinkedInSource()
    results = source.search("Python Engineer", "Poland", days_back=21)
    assert len(results) == 1
    search_url = mock_goto.call_args.args[0]
    assert "keywords=Python%20Engineer" in search_url
    assert "f_TPR=r1814400" in search_url
    assert source.last_search_diagnostics["query_matched"] == 1


@patch.object(LinkedInSource, "_wait")
@patch.object(LinkedInSource, "_goto")
@patch.object(LinkedInSource, "_collect_cards")
def test_search_encodes_location_and_excludes_known_urls(mock_collect, mock_goto, mock_wait):
    mock_collect.return_value = [
        RawJob("Python Engineer", "Acme", "Côte d'Ivoire", "https://www.linkedin.com/jobs/view/1?trk=x", "linkedin"),
        RawJob("Python Engineer", "New", "Côte d'Ivoire", "https://www.linkedin.com/jobs/view/2", "linkedin"),
    ]
    source = LinkedInSource()
    results = source.search("Python Engineer", "Côte d'Ivoire", known_urls={"https://linkedin.com/jobs/view/1"})
    assert [job.url for job in results] == ["https://www.linkedin.com/jobs/view/2"]
    assert "location=C%C3%B4te%20d%27Ivoire" in mock_goto.call_args.args[0]


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

    assert len(results) == 1
    assert next_button.click.call_count == 9


@patch.object(LinkedInSource, "_scroll_to_bottom")
@patch.object(LinkedInSource, "_wait")
def test_collection_deduplicates_cards_and_uses_stable_source_id(mock_wait, mock_scroll):
    source = LinkedInSource()
    source._page = MagicMock()
    source._page.query_selector.side_effect = lambda selector: None if selector == ".jobs-search-no-results-banner" else None
    source._page.evaluate.return_value = [
        {"title": "PHP Engineer", "company": "Acme", "location": "Poland", "url": "https://www.linkedin.com/jobs/view/1?trk=x"},
        {"title": "PHP Engineer", "company": "Acme", "location": "Poland", "url": "https://www.linkedin.com/jobs/view/1?trk=y"},
    ]
    results = source._collect_cards()
    assert len(results) == 1
    assert results[0].source_id == "https://linkedin.com/jobs/view/1"
    assert results[0].url == "https://linkedin.com/jobs/view/1"
