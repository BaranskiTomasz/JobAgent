from unittest.mock import patch

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
