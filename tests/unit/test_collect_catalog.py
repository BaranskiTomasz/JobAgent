from unittest.mock import patch

import pytest

from scripts.collect_catalog import CATALOG_QUERIES, FACT_SCHEMA_VERSION, catalog_sources, main, queries_for_slot


def test_catalog_queries_rotate_without_exceeding_linkedin_budget():
    seen = set()
    for slot in range((len(CATALOG_QUERIES) + 1) // 2):
        queries = queries_for_slot(slot)
        assert len(queries) == 2
        seen.update(queries)
    assert seen == set(CATALOG_QUERIES)


def test_catalog_queries_cover_technology_and_role_discovery():
    assert {"Java Developer", ".NET Developer", "Go Developer"} <= set(CATALOG_QUERIES)
    assert {"Software Developer", "Software Engineer", "Backend Developer", "Frontend Developer"} <= set(CATALOG_QUERIES)


def test_catalog_query_rotation_wraps():
    assert queries_for_slot(2) == ["Angular Developer", "QA Engineer"]


def test_default_catalog_sources_exclude_linkedin():
    sources = catalog_sources()
    assert "linkedin" not in sources
    assert "greenhouse" in sources


def test_catalog_rejects_explicit_linkedin_source():
    with pytest.raises(ValueError, match="linkedin"):
        catalog_sources(["linkedin", "greenhouse"])


def test_catalog_run_extracts_the_full_pending_backlog_not_just_current_run():
    # Not scoped to this run's own job_ids: a prior run's extraction can fail
    # wholesale (an Anthropic outage, say) and leave postings stuck with
    # missing facts forever, since they're already in known_urls and nothing
    # will re-insert them. "survivor" here represents exactly that backlog.
    pending = [
        {"id": "survivor", "description": "Old remote Python role"},
        {"id": "original", "description": "New remote Python role"},
    ]
    with patch("sys.argv", ["collect_catalog.py", "--max-jobs", "3"]), \
         patch("scripts.collect_catalog.run", return_value={
             "jobs_found": 1, "jobs_new": 1, "job_ids": ["original"],
         }), \
         patch("scripts.collect_catalog.job_repository.get_missing_facts", return_value=pending) as missing, \
         patch("scripts.collect_catalog.run_extraction", return_value=2) as extract:
        assert main() == 0

    missing.assert_called_once_with(FACT_SCHEMA_VERSION, 3, catalog=True)
    extract.assert_called_once_with(pending, catalog=True)


def test_catalog_run_extracts_backlog_even_when_this_run_found_nothing_new():
    pending = [{"id": "survivor", "description": "Old remote Python role"}]
    with patch("sys.argv", ["collect_catalog.py", "--max-jobs", "3"]), \
         patch("scripts.collect_catalog.run", return_value={
             "jobs_found": 0, "jobs_new": 0, "job_ids": [],
         }), \
         patch("scripts.collect_catalog.job_repository.get_missing_facts", return_value=pending), \
         patch("scripts.collect_catalog.run_extraction", return_value=1) as extract:
        assert main() == 0

    extract.assert_called_once_with(pending, catalog=True)


def test_catalog_run_skips_extraction_when_nothing_pending():
    with patch("sys.argv", ["collect_catalog.py", "--max-jobs", "3"]), \
         patch("scripts.collect_catalog.run", return_value={
             "jobs_found": 0, "jobs_new": 0, "job_ids": [],
         }), \
         patch("scripts.collect_catalog.job_repository.get_missing_facts", return_value=[]), \
         patch("scripts.collect_catalog.run_extraction", return_value=0) as extract:
        assert main() == 0

    extract.assert_not_called()
