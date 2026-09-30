import itertools

import config
from db.repositories import job_repository, search_stats_repository, excluded_search_queries_repository, session_repository
from collector.query_pruning import prune_queries, suggest_queries_for_review

_counter = itertools.count()


def _terminal_jobs(query, rejected=0, auto_rejected=0, applied=0, reviewed=0, source="linkedin"):
    # Globally unique url/title/company per job (across separate calls too), the
    # title+company AND url dedup rules in job_repository.insert() would otherwise
    # silently drop later jobs sharing an earlier call's values.
    for _ in range(rejected):
        n = next(_counter)
        job_id = job_repository.insert(f"Title {n}", f"Co {n}", "PL", f"https://a.com/{query}/{n}", source, search_query=query)
        job_repository.update_status(job_id, "rejected")
    for _ in range(auto_rejected):
        n = next(_counter)
        job_id = job_repository.insert(f"Title {n}", f"Co {n}", "PL", f"https://a.com/{query}/{n}", source, search_query=query)
        job_repository.update_score_and_status(job_id, 0.0, "no match", "auto_rejected")
    for _ in range(applied):
        n = next(_counter)
        job_id = job_repository.insert(f"Title {n}", f"Co {n}", "PL", f"https://a.com/{query}/{n}", source, search_query=query)
        job_repository.update_status(job_id, "applied")
    for _ in range(reviewed):
        n = next(_counter)
        job_id = job_repository.insert(f"Title {n}", f"Co {n}", "PL", f"https://a.com/{query}/{n}", source, search_query=query)
        job_repository.update_status(job_id, "reviewed")


class TestPruneQueriesRejectRate:
    def test_excludes_query_above_threshold_with_enough_samples(self, monkeypatch):
        monkeypatch.setitem(config.QUERY_PRUNING, "min_terminal_sample", 10)
        monkeypatch.setitem(config.QUERY_PRUNING, "reject_rate_threshold", 0.9)
        _terminal_jobs("Bad Query", rejected=10)

        result = prune_queries("linkedin")

        assert [r["search_query"] for r in result] == ["Bad Query"]
        assert excluded_search_queries_repository.get_excluded("linkedin") == {
            "Bad Query": "reject rate 100% over 10 jobs, 0% applied/reviewed"
        }

    def test_below_min_sample_not_excluded(self, monkeypatch):
        monkeypatch.setitem(config.QUERY_PRUNING, "min_terminal_sample", 10)
        monkeypatch.setitem(config.QUERY_PRUNING, "reject_rate_threshold", 0.9)
        _terminal_jobs("Small Sample", rejected=9)

        assert prune_queries("linkedin") == []
        assert excluded_search_queries_repository.get_excluded("linkedin") == {}

    def test_below_reject_rate_threshold_not_excluded(self, monkeypatch):
        monkeypatch.setitem(config.QUERY_PRUNING, "min_terminal_sample", 10)
        monkeypatch.setitem(config.QUERY_PRUNING, "reject_rate_threshold", 0.95)
        _terminal_jobs("Average Query", rejected=9, applied=1)  # 90% reject, but also has an applied

        assert prune_queries("linkedin") == []

    def test_low_success_rate_no_longer_blocks_exclusion(self, monkeypatch):
        # Regression test: this used to be a one-time boolean (any applied/reviewed
        # job, ever, blocked pruning forever), which meant a single early hit could
        # permanently immunize a query even after its success rate collapsed. 1/40
        # applied is 2.5%, below max_success_rate, so it should no longer protect.
        monkeypatch.setitem(config.QUERY_PRUNING, "min_terminal_sample", 10)
        monkeypatch.setitem(config.QUERY_PRUNING, "reject_rate_threshold", 0.9)
        monkeypatch.setitem(config.QUERY_PRUNING, "max_success_rate", 0.05)
        _terminal_jobs("Mostly Noise", rejected=39, applied=1)  # 97.5% reject, 2.5% applied

        result = prune_queries("linkedin")

        assert [r["search_query"] for r in result] == ["Mostly Noise"]

    def test_success_rate_above_threshold_still_blocks_exclusion(self, monkeypatch):
        # A meaningfully non-trivial applied/reviewed share (15%, well above
        # max_success_rate) should still protect the query even at a high reject rate.
        monkeypatch.setitem(config.QUERY_PRUNING, "min_terminal_sample", 10)
        monkeypatch.setitem(config.QUERY_PRUNING, "reject_rate_threshold", 0.8)
        monkeypatch.setitem(config.QUERY_PRUNING, "max_success_rate", 0.05)
        _terminal_jobs("Good But Noisy", rejected=17, applied=2, reviewed=1)  # 85% reject, 15% applied/reviewed

        assert prune_queries("linkedin") == []

    def test_rerunning_refreshes_reason_without_duplicating(self, monkeypatch):
        monkeypatch.setitem(config.QUERY_PRUNING, "min_terminal_sample", 10)
        monkeypatch.setitem(config.QUERY_PRUNING, "reject_rate_threshold", 0.9)
        _terminal_jobs("Bad Query", rejected=10)
        prune_queries("linkedin")

        _terminal_jobs("Bad Query", auto_rejected=5)  # more rejects, same query
        prune_queries("linkedin")

        assert len(excluded_search_queries_repository.get_all()) == 1
        assert "15 jobs" in excluded_search_queries_repository.get_excluded("linkedin")["Bad Query"]

    def test_defaults_to_linkedin_when_no_source_given(self, monkeypatch):
        monkeypatch.setitem(config.QUERY_PRUNING, "min_terminal_sample", 10)
        monkeypatch.setitem(config.QUERY_PRUNING, "reject_rate_threshold", 0.9)
        _terminal_jobs("Bad Query", rejected=10)

        prune_queries()

        assert "Bad Query" in excluded_search_queries_repository.get_excluded("linkedin")


class TestPruneQueriesZeroYield:
    def test_excludes_query_that_never_returns_cards(self, monkeypatch):
        monkeypatch.setitem(config.QUERY_PRUNING, "min_searches_for_zero_yield", 5)
        session_id = session_repository.start()
        for _ in range(5):
            search_stats_repository.record(session_id, "linkedin", "Redundant Query", "Austria", cards_found=0, new_found=0)

        result = prune_queries("linkedin")

        assert [r["search_query"] for r in result] == ["Redundant Query"]

    def test_does_not_duplicate_when_query_already_excluded_by_reject_rate(self, monkeypatch):
        monkeypatch.setitem(config.QUERY_PRUNING, "min_terminal_sample", 10)
        monkeypatch.setitem(config.QUERY_PRUNING, "reject_rate_threshold", 0.9)
        monkeypatch.setitem(config.QUERY_PRUNING, "min_searches_for_zero_yield", 5)
        _terminal_jobs("Both Bad", rejected=10)
        session_id = session_repository.start()
        for _ in range(5):
            search_stats_repository.record(session_id, "linkedin", "Both Bad", "Austria", cards_found=0, new_found=0)

        result = prune_queries("linkedin")

        assert len(result) == 1
        assert len(excluded_search_queries_repository.get_all()) == 1


class TestSuggestQueriesForReview:
    def test_flags_zero_applied_high_reject_query(self, monkeypatch):
        monkeypatch.setitem(config.QUERY_PRUNING, "min_terminal_sample", 10)
        monkeypatch.setitem(config.QUERY_PRUNING, "suggestion_reject_rate_threshold", 0.7)
        _terminal_jobs("Senior Developer", rejected=17, auto_rejected=15, reviewed=2)  # 94% reject, 0 applied

        result = suggest_queries_for_review("linkedin")

        assert [s["search_query"] for s in result] == ["Senior Developer"]
        assert "0 applied" in result[0]["reason"]

    def test_any_applied_job_excludes_from_suggestions(self, monkeypatch):
        # This is the gap prune_queries' max_success_rate protects: a query that
        # still converts, even rarely, shouldn't be silently suggested for removal.
        monkeypatch.setitem(config.QUERY_PRUNING, "min_terminal_sample", 10)
        monkeypatch.setitem(config.QUERY_PRUNING, "suggestion_reject_rate_threshold", 0.7)
        _terminal_jobs("Python", rejected=30, applied=1, reviewed=5)  # high reject, but has converted once

        assert suggest_queries_for_review("linkedin") == []

    def test_below_reject_threshold_not_suggested(self, monkeypatch):
        monkeypatch.setitem(config.QUERY_PRUNING, "min_terminal_sample", 10)
        monkeypatch.setitem(config.QUERY_PRUNING, "suggestion_reject_rate_threshold", 0.7)
        _terminal_jobs("Kubernetes", rejected=6, reviewed=4)  # 60% reject

        assert suggest_queries_for_review("linkedin") == []

    def test_below_min_sample_not_suggested(self, monkeypatch):
        monkeypatch.setitem(config.QUERY_PRUNING, "min_terminal_sample", 10)
        monkeypatch.setitem(config.QUERY_PRUNING, "suggestion_reject_rate_threshold", 0.7)
        _terminal_jobs("Too New", rejected=5)  # 100% reject but too small a sample

        assert suggest_queries_for_review("linkedin") == []

    def test_already_excluded_query_not_suggested_again(self, monkeypatch):
        monkeypatch.setitem(config.QUERY_PRUNING, "min_terminal_sample", 10)
        monkeypatch.setitem(config.QUERY_PRUNING, "suggestion_reject_rate_threshold", 0.7)
        _terminal_jobs("Already Gone", rejected=10)
        excluded_search_queries_repository.exclude("linkedin", "Already Gone", "manually excluded earlier")

        assert suggest_queries_for_review("linkedin") == []

    def test_never_excludes_anything_itself(self, monkeypatch):
        monkeypatch.setitem(config.QUERY_PRUNING, "min_terminal_sample", 10)
        monkeypatch.setitem(config.QUERY_PRUNING, "suggestion_reject_rate_threshold", 0.7)
        _terminal_jobs("Senior Developer", rejected=17, auto_rejected=15, reviewed=2)

        suggest_queries_for_review("linkedin")

        assert excluded_search_queries_repository.get_excluded("linkedin") == {}

    def test_sorted_worst_reject_rate_first(self, monkeypatch):
        monkeypatch.setitem(config.QUERY_PRUNING, "min_terminal_sample", 10)
        monkeypatch.setitem(config.QUERY_PRUNING, "suggestion_reject_rate_threshold", 0.7)
        _terminal_jobs("Backend Developer", rejected=8, reviewed=3)   # 8/11 = 72.7%
        _terminal_jobs("Lead Developer", rejected=13, reviewed=1)     # 13/14 = 92.9%

        result = suggest_queries_for_review("linkedin")

        assert [s["search_query"] for s in result] == ["Lead Developer", "Backend Developer"]
