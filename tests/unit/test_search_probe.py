from unittest.mock import MagicMock, patch

from collector.base import RawJob
from scripts.search_probe import probe


@patch("scripts.search_probe.make")
def test_probe_does_not_persist_and_returns_samples(mock_make):
    source = MagicMock()
    source.__enter__.return_value = source
    source.search.return_value = [RawJob("Backend Engineer", "Acme", "Europe", "https://example/1", "jobscollider")]
    source.last_search_diagnostics = {"upstream_found": 5, "geo_matched": 1}
    mock_make.return_value = source

    result = probe(["jobscollider"], ["Backend Engineer"], ["Poland"], 7, 10)

    item = result["sources"]["jobscollider"][0]
    assert item["jobs"][0]["title"] == "Backend Engineer"
    assert item["diagnostics"]["upstream_found"] == 5
