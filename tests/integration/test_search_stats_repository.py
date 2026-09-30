from db.repositories import session_repository, search_stats_repository


class TestRecord:
    def test_record_is_queryable_via_summary(self):
        session_id = session_repository.start()
        search_stats_repository.record(session_id, "linkedin", "Senior PHP Developer", "Austria", cards_found=7, new_found=2)
        summary = search_stats_repository.get_query_summary("linkedin")
        assert len(summary) == 1
        assert summary[0]["search_query"] == "Senior PHP Developer"
        assert summary[0]["total_searches"] == 1
        assert summary[0]["total_new_found"] == 2

    def test_retrieval_funnel_is_queryable_via_summary(self):
        session_id = session_repository.start()
        search_stats_repository.record(
            session_id, "jobscollider", "Backend Engineer", "Poland",
            cards_found=10, new_found=4, upstream_found=100,
            query_matched=80, date_matched=30, geo_matched=12,
        )
        row = search_stats_repository.get_query_summary("jobscollider")[0]
        assert row["upstream_found"] == 100
        assert row["query_matched"] == 80
        assert row["date_matched"] == 30
        assert row["geo_matched"] == 12

    def test_zero_cards_counted_as_zero_result_search(self):
        session_id = session_repository.start()
        search_stats_repository.record(session_id, "linkedin", "Full Stack Python Developer", "Austria", cards_found=0, new_found=0)
        summary = search_stats_repository.get_query_summary("linkedin")
        assert summary[0]["zero_result_searches"] == 1

    def test_nonzero_cards_not_counted_as_zero_result(self):
        session_id = session_repository.start()
        search_stats_repository.record(session_id, "linkedin", "PHP Developer", "Austria", cards_found=7, new_found=0)
        summary = search_stats_repository.get_query_summary("linkedin")
        assert summary[0]["zero_result_searches"] == 0


class TestGetQuerySummary:
    def test_aggregates_across_locations_and_sessions(self):
        session_id = session_repository.start()
        search_stats_repository.record(session_id, "linkedin", "PHP Developer", "Austria", cards_found=0, new_found=0)
        search_stats_repository.record(session_id, "linkedin", "PHP Developer", "Belgium", cards_found=0, new_found=0)
        session_repository.finish(session_id, jobs_found=0, jobs_scored=0)
        session_id_2 = session_repository.start()
        search_stats_repository.record(session_id_2, "linkedin", "PHP Developer", "Austria", cards_found=5, new_found=1)

        summary = search_stats_repository.get_query_summary("linkedin")
        assert len(summary) == 1
        row = summary[0]
        assert row["total_searches"] == 3
        assert row["zero_result_searches"] == 2
        assert row["total_new_found"] == 1

    def test_filters_by_source(self):
        session_id = session_repository.start()
        search_stats_repository.record(session_id, "linkedin", "PHP Developer", "Austria", cards_found=0, new_found=0)
        search_stats_repository.record(session_id, "remotive", "PHP Developer", "Austria", cards_found=3, new_found=1)

        linkedin_summary = search_stats_repository.get_query_summary("linkedin")
        assert len(linkedin_summary) == 1
        assert linkedin_summary[0]["total_searches"] == 1

    def test_separate_rows_per_distinct_query(self):
        session_id = session_repository.start()
        search_stats_repository.record(session_id, "linkedin", "PHP Developer", "Austria", cards_found=5, new_found=1)
        search_stats_repository.record(session_id, "linkedin", "Python Developer", "Austria", cards_found=0, new_found=0)

        summary = search_stats_repository.get_query_summary("linkedin")
        assert {row["search_query"] for row in summary} == {"PHP Developer", "Python Developer"}

    def test_returns_empty_when_no_stats_recorded(self):
        assert search_stats_repository.get_query_summary("linkedin") == []


class TestGetZeroYieldQueries:
    def test_flags_query_with_zero_cards_across_min_searches(self):
        session_id = session_repository.start()
        for _ in range(5):
            search_stats_repository.record(session_id, "linkedin", "Dead Query", "Austria", cards_found=0, new_found=0)
        assert search_stats_repository.get_zero_yield_queries("linkedin", min_searches=5) == ["Dead Query"]

    def test_below_min_searches_not_flagged(self):
        session_id = session_repository.start()
        for _ in range(4):
            search_stats_repository.record(session_id, "linkedin", "Dead Query", "Austria", cards_found=0, new_found=0)
        assert search_stats_repository.get_zero_yield_queries("linkedin", min_searches=5) == []

    def test_any_returned_cards_exclude_query(self):
        session_id = session_repository.start()
        for _ in range(4):
            search_stats_repository.record(session_id, "linkedin", "Good Query", "Austria", cards_found=0, new_found=0)
        search_stats_repository.record(session_id, "linkedin", "Good Query", "Belgium", cards_found=2, new_found=0)
        assert search_stats_repository.get_zero_yield_queries("linkedin", min_searches=5) == []

    def test_scoped_by_source(self):
        session_id = session_repository.start()
        for _ in range(5):
            search_stats_repository.record(session_id, "remotive", "Dead Query", "Austria", cards_found=0, new_found=0)
        assert search_stats_repository.get_zero_yield_queries("linkedin", min_searches=5) == []
