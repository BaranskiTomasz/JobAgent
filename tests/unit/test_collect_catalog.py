import pytest

from scripts.collect_catalog import CATALOG_QUERIES, catalog_sources, queries_for_slot


def test_catalog_queries_rotate_without_exceeding_linkedin_budget():
    seen = set()
    for slot in range(3):
        queries = queries_for_slot(slot)
        assert len(queries) == 2
        seen.update(queries)
    assert seen == set(CATALOG_QUERIES)


def test_catalog_query_rotation_wraps():
    assert queries_for_slot(2) == ["Angular Developer", "QA Engineer"]


def test_default_catalog_sources_exclude_linkedin():
    sources = catalog_sources()
    assert "linkedin" not in sources
    assert "greenhouse" in sources


def test_catalog_rejects_explicit_linkedin_source():
    with pytest.raises(ValueError, match="linkedin"):
        catalog_sources(["linkedin", "greenhouse"])
