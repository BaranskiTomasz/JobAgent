from scripts.collect_catalog import CATALOG_QUERIES, queries_for_slot


def test_catalog_queries_rotate_without_exceeding_linkedin_budget():
    seen = set()
    for slot in range(3):
        queries = queries_for_slot(slot)
        assert len(queries) == 2
        seen.update(queries)
    assert seen == set(CATALOG_QUERIES)


def test_catalog_query_rotation_wraps():
    assert queries_for_slot(2) == ["Angular Developer", "QA Engineer"]
