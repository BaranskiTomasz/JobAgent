from collector.query_planner import SourceQuery, order_queries, source_queries


def test_tag_sources_receive_specific_token():
    assert source_queries("jobicy", ["Senior Backend Engineer", "Symfony Developer"]) == [
        SourceQuery("Senior Backend Engineer", "backend"),
        SourceQuery("Symfony Developer", "symfony"),
    ]


def test_regular_sources_keep_full_queries():
    assert source_queries("linkedin", ["Backend Engineer"]) == [SourceQuery("Backend Engineer", "Backend Engineer")]


def test_unseen_queries_run_before_seen_queries():
    summary = [{
        "search_query": "PHP", "total_searches": 3, "total_new_found": 2,
        "last_searched_at": "2026-01-01T10:00:00",
    }]
    queries = source_queries("linkedin", ["PHP", "Symfony"])
    assert order_queries(queries, summary, 1)[0].original == "Symfony"


def test_seen_queries_rotate_by_oldest_search():
    summary = [
        {"search_query": "PHP", "total_searches": 2, "total_new_found": 2, "last_searched_at": "2026-02-01T10:00:00"},
        {"search_query": "Symfony", "total_searches": 2, "total_new_found": 1, "last_searched_at": "2026-01-01T10:00:00"},
    ]
    queries = source_queries("linkedin", ["PHP", "Symfony"])
    assert [q.original for q in order_queries(queries, summary, 1)] == ["Symfony", "PHP"]
