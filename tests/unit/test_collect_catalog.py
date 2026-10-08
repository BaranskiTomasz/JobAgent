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


def test_catalog_run_extracts_shared_pending_jobs():
    pending = [{"id": "survivor", "description": "Remote Python role"}]
    with patch("sys.argv", ["collect_catalog.py", "--max-jobs", "3"]), \
         patch("scripts.collect_catalog.run", return_value={
             "jobs_found": 1, "jobs_new": 1, "job_ids": ["original"],
         }), \
         patch("scripts.collect_catalog.job_repository.get_missing_facts", return_value=pending) as missing, \
         patch("scripts.collect_catalog.run_extraction", return_value=1) as extract:
        assert main() == 0

    missing.assert_called_once_with(FACT_SCHEMA_VERSION, 3, catalog=True)
    extract.assert_called_once_with(pending, catalog=True)
