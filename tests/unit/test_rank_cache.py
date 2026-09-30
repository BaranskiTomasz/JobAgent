from ranker.debate import DEBATE_UNAVAILABLE_FLAG
from ranker.listwise import FALLBACK_RANK_REASON
from ranker.rank_cache import reuse_if_unchanged


def test_empty_pool_returns_empty_list():
    assert reuse_if_unchanged([], []) == []


def test_returns_none_when_never_ranked_before():
    listwise_pool = [{"id": "a"}, {"id": "b"}]
    previous_jobs = [{"id": "a", "listwise_rank": None}, {"id": "b", "listwise_rank": None}]
    assert reuse_if_unchanged(listwise_pool, previous_jobs) is None


def test_returns_none_when_pool_membership_changed():
    listwise_pool = [{"id": "a"}, {"id": "c"}]
    previous_jobs = [
        {"id": "a", "listwise_rank": 1},
        {"id": "b", "listwise_rank": 2},
        {"id": "c", "listwise_rank": None},
    ]
    assert reuse_if_unchanged(listwise_pool, previous_jobs) is None


def test_reuses_and_sorts_by_previous_listwise_rank_when_pool_unchanged():
    listwise_pool = [
        {"id": "b", "listwise_rank": 2, "rank_reason": "second"},
        {"id": "a", "listwise_rank": 1, "rank_reason": "first"},
    ]
    previous_jobs = [
        {"id": "a", "listwise_rank": 1},
        {"id": "b", "listwise_rank": 2},
    ]
    result = reuse_if_unchanged(listwise_pool, previous_jobs)
    assert [j["id"] for j in result] == ["a", "b"]


def test_previous_top_set_larger_than_current_pool_invalidates_cache():
    listwise_pool = [{"id": "a", "listwise_rank": 1}]
    previous_jobs = [
        {"id": "a", "listwise_rank": 1},
        {"id": "z", "listwise_rank": 5},
    ]
    assert reuse_if_unchanged(listwise_pool, previous_jobs) is None


def test_fallback_listwise_result_is_not_reused():
    # Regression: a run where listwise_rank fell back (API failure) used to be
    # cached exactly like a genuine result, reused indefinitely with no retry
    # for as long as the pool composition happened to stay the same.
    listwise_pool = [{"id": "a", "listwise_rank": 1}]
    previous_jobs = [{"id": "a", "listwise_rank": 1, "rank_reason": FALLBACK_RANK_REASON}]
    assert reuse_if_unchanged(listwise_pool, previous_jobs) is None


def test_unavailable_debate_result_is_not_reused():
    # Same reasoning, for debate's own failure marker.
    listwise_pool = [{"id": "a", "listwise_rank": 1}]
    previous_jobs = [{"id": "a", "listwise_rank": 1, "debate_flag": DEBATE_UNAVAILABLE_FLAG}]
    assert reuse_if_unchanged(listwise_pool, previous_jobs) is None


def test_one_degraded_job_invalidates_the_whole_cached_pool():
    # A mixed pool (some genuine, one degraded) forces a full re-run rather
    # than reusing the genuine rows and re-ranking only the degraded one,
    # listwise/debate operate on the whole pool as one batch, not per-job.
    listwise_pool = [{"id": "a", "listwise_rank": 1}, {"id": "b", "listwise_rank": 2}]
    previous_jobs = [
        {"id": "a", "listwise_rank": 1, "rank_reason": "genuine reason"},
        {"id": "b", "listwise_rank": 2, "rank_reason": FALLBACK_RANK_REASON},
    ]
    assert reuse_if_unchanged(listwise_pool, previous_jobs) is None


def test_genuine_result_with_no_debate_flag_at_all_is_still_reused():
    # A job debate reviewed and had nothing to say about has no debate_flag,
    # that must NOT be confused with the failure sentinel.
    listwise_pool = [{"id": "a", "listwise_rank": 1, "rank_reason": "genuine"}]
    previous_jobs = [{"id": "a", "listwise_rank": 1, "rank_reason": "genuine", "debate_flag": None}]
    result = reuse_if_unchanged(listwise_pool, previous_jobs)
    assert result is not None
    assert [j["id"] for j in result] == ["a"]


def test_changed_fingerprint_invalidates_cache():
    pool = [{"id": "a", "listwise_rank": 1, "ranking_fingerprint": "old"}]
    assert reuse_if_unchanged(pool, pool, "new") is None


def test_matching_fingerprint_reuses_cache():
    pool = [{"id": "a", "listwise_rank": 1, "ranking_fingerprint": "same"}]
    assert reuse_if_unchanged(pool, pool, "same") is not None
