import json
import uuid
from unittest.mock import patch

import api_client
from db.repositories import job_repository
from extractor.runner import FACT_SCHEMA_VERSION
from tests.conftest import TEST_AUTOMATION_API_KEY


def _unique_url(url: str) -> str:
    """job_postings is shared/global across every user and never truncated
    between tests in a session, reusing a literal url across two tests would
    make the second insert() silently return None (an apparent duplicate from
    another test), not the fresh job the test expects. Every call gets its own
    url so tests never collide with each other or with leftover data."""
    return f"{url}?t={uuid.uuid4().hex}"


def _insert(url="https://example.com/job/1", **kwargs):
    defaults = dict(title="PHP Developer", company="Acme Corp",
                    location="Poland", source="linkedin",
                    description="Symfony experience required.")
    return job_repository.insert(**{**defaults, "url": _unique_url(url), **kwargs})


def _mark_extracted(job_id: str) -> None:
    """The personal scoring/ranking feeds now require a non catalog_gate
    job_fact_extractions row (see jobs_repo._personal_extraction_eligibility_sql
    in JobAgentWeb), matching the real pipeline where extraction always runs
    before scoring. Tests asserting a job appears in those feeds need to
    simulate that extraction step. PUT /{job_id}/facts is automation-only
    (require_automation_client), so the API key is patched in just for this one
    call rather than for the whole test, see tests/conftest.py's
    TEST_AUTOMATION_API_KEY for why it must not leak into every other request."""
    with patch("api_client.JOBAGENT_API_KEY", TEST_AUTOMATION_API_KEY):
        job_repository.update_facts(job_id, FACT_SCHEMA_VERSION, "test-model", "test-hash", {"remote": True}, {}, catalog=True)


class TestInsert:
    def test_returns_16_char_id_on_success(self):
        job_id = _insert()
        assert job_id is not None
        assert len(job_id) == 16

    def test_duplicate_url_returns_none(self):
        url = _unique_url("https://a.com/dup")
        assert job_repository.insert(
            title="PHP Developer", company="Acme Corp", location="Poland",
            url=url, source="linkedin", description="desc",
        ) is not None
        assert job_repository.insert(
            title="PHP Developer", company="Acme Corp", location="Poland",
            url=url, source="linkedin", description="desc",
        ) is None

    def test_different_title_and_company_different_url_both_succeed(self):
        """The old title+company dedup heuristic is gone in the shared pool,
        only url identifies a posting now (see JobAgentWeb's jobs_repo.insert)."""
        id1 = _insert(url="https://a.com/1")
        id2 = _insert(url="https://a.com/2")
        assert id1 is not None and id2 is not None and id1 != id2

    def test_insert_without_description_succeeds(self):
        job_id = job_repository.insert(
            "PHP Dev", "Co", "PL", _unique_url("https://a.com/nodesc"), "linkedin"
        )
        assert job_id is not None

    def test_search_query_defaults_to_none(self):
        job_id = _insert()
        row = next(r for r in job_repository.search(status="all") if r["id"] == job_id)
        assert row["search_query"] is None

    def test_search_query_is_stored(self):
        job_id = _insert(search_query="Senior PHP Developer")
        row = next(r for r in job_repository.search(status="all") if r["id"] == job_id)
        assert row["search_query"] == "Senior PHP Developer"


class TestGetQueryOutcomeStats:
    def test_excludes_jobs_still_new(self):
        query = f"PHP Developer {uuid.uuid4().hex[:8]}"
        _insert(search_query=query)
        assert job_repository.get_query_outcome_stats("linkedin") == []

    def test_excludes_jobs_without_search_query(self):
        job_id = _insert()
        job_repository.update_status(job_id, "rejected")
        stats = {r["search_query"] for r in job_repository.get_query_outcome_stats("linkedin")}
        assert None not in stats

    def test_counts_reject_and_applied_totals(self):
        query = f"PHP Developer {uuid.uuid4().hex[:8]}"
        rejected_id = _insert(url="https://a.com/1", company="Co A", search_query=query)
        job_repository.update_status(rejected_id, "rejected")
        auto_rejected_id = _insert(url="https://a.com/2", company="Co B", search_query=query)
        job_repository.update_score_and_status(auto_rejected_id, 0.0, "no match", "auto_rejected")
        applied_id = _insert(url="https://a.com/3", company="Co C", search_query=query)
        job_repository.update_status(applied_id, "applied")

        stats = {r["search_query"]: r for r in job_repository.get_query_outcome_stats("linkedin")}
        row = stats[query]
        assert row["terminal_total"] == 3
        assert row["reject_total"] == 2
        assert row["applied_total"] == 1
        assert row["reviewed_total"] == 0

    def test_filters_by_source(self):
        query = f"PHP Developer {uuid.uuid4().hex[:8]}"
        job_id = job_repository.insert(
            "PHP Dev", "Co", "PL", _unique_url("https://a.com/other-source"), "remotive",
            search_query=query,
        )
        job_repository.update_status(job_id, "rejected")
        stats = {r["search_query"] for r in job_repository.get_query_outcome_stats("linkedin")}
        assert query not in stats

    def test_separate_rows_per_query(self):
        php_query = f"PHP Developer {uuid.uuid4().hex[:8]}"
        python_query = f"Python Developer {uuid.uuid4().hex[:8]}"
        php_id = _insert(url="https://a.com/1", company="Co A", search_query=php_query)
        job_repository.update_status(php_id, "rejected")
        python_id = _insert(url="https://a.com/2", company="Co B", search_query=python_query)
        job_repository.update_status(python_id, "applied")

        stats = {row["search_query"]: row for row in job_repository.get_query_outcome_stats("linkedin")}
        assert stats[php_query]["reject_total"] == 1
        assert stats[python_query]["applied_total"] == 1


class TestGetUnscored:
    def test_returns_job_with_description(self):
        job_id = _insert()
        _mark_extracted(job_id)
        ids = [j["id"] for j in job_repository.get_unscored()]
        assert job_id in ids

    def test_excludes_job_without_description(self):
        job_id = job_repository.insert("No desc", "Co", "PL", _unique_url("https://a.com/nd"), "linkedin")
        ids = [j["id"] for j in job_repository.get_unscored()]
        assert job_id not in ids

    def test_excludes_job_with_empty_description(self):
        # update_description() is write-once now (JobAgentWeb no longer lets a
        # second write clear/overwrite an already-populated description, see
        # jobs_repo.update_description), so this constructs the empty-description
        # state directly at insert time instead of via a follow-up update.
        job_id = _insert(description="")
        ids = [j["id"] for j in job_repository.get_unscored()]
        assert job_id not in ids

    def test_excludes_already_scored_job(self):
        job_id = _insert()
        job_repository.update_score(job_id, 7.5, "Good match")
        ids = [j["id"] for j in job_repository.get_unscored()]
        assert job_id not in ids

    def test_excludes_auto_rejected_job(self):
        job_id = _insert()
        job_repository.update_score_and_status(job_id, 0.0, "On-site only", "auto_rejected")
        ids = [j["id"] for j in job_repository.get_unscored()]
        assert job_id not in ids


class TestUpdateScoreAndStatus:
    def test_both_fields_updated_atomically(self):
        _insert()
        job_id = _insert()
        job_repository.update_score_and_status(job_id, 0.0, "Rejected", "auto_rejected")
        stats = job_repository.get_stats()
        assert stats["auto_rejected"] >= 1

    def test_auto_rejected_no_longer_appears_in_unscored(self):
        job_id = _insert()
        job_repository.update_score_and_status(job_id, 0.0, "Rejected", "auto_rejected")
        ids = [j["id"] for j in job_repository.get_unscored()]
        assert job_id not in ids

    def test_breakdown_stored_as_json(self):
        job_id = _insert()
        breakdown = {"sub_scores": {"stack_fit": 8}, "pros": ["Good stack"], "cons": []}
        job_repository.update_score_and_status(job_id, 0.0, "Rejected", "auto_rejected", breakdown)
        job = job_repository.get_by_status("auto_rejected")
        job = next(j for j in job if j["id"] == job_id)
        assert json.loads(job["score_breakdown"]) == breakdown

    def test_no_breakdown_leaves_column_null(self):
        job_id = _insert()
        job_repository.update_score_and_status(job_id, 0.0, "Rejected", "auto_rejected")
        job = next(j for j in job_repository.get_by_status("auto_rejected") if j["id"] == job_id)
        assert job["score_breakdown"] is None


class TestUpdateScoreBreakdown:
    def test_breakdown_round_trips_as_json(self):
        job_id = _insert()
        breakdown = {"sub_scores": {"stack_fit": 8, "seniority_fit": 6}, "pros": ["A"], "cons": ["B"]}
        job_repository.update_score(job_id, 7.5, "Good fit", breakdown)
        job = next(j for j in job_repository.search(status="all") if j["id"] == job_id)
        assert job["score"] == 7.5
        assert json.loads(job["score_breakdown"]) == breakdown

    def test_no_breakdown_leaves_column_null(self):
        job_id = _insert()
        job_repository.update_score(job_id, 7.5, "Good fit")
        job = next(j for j in job_repository.search(status="all") if j["id"] == job_id)
        assert job["score_breakdown"] is None


class TestGetExamples:
    def test_applied_job_in_positive_list(self):
        job_id = _insert()
        job_repository.update_status(job_id, "applied")
        pos, neg = job_repository.get_examples()
        assert job_id in [j["id"] for j in pos]

    def test_rejected_job_in_negative_list(self):
        job_id = _insert()
        job_repository.update_status(job_id, "rejected")
        pos, neg = job_repository.get_examples()
        assert job_id in [j["id"] for j in neg]

    def test_auto_rejected_excluded_from_both_lists(self):
        job_id = _insert()
        job_repository.update_score_and_status(job_id, 0.0, "Bad", "auto_rejected")
        pos, neg = job_repository.get_examples()
        assert job_id not in [j["id"] for j in pos]
        assert job_id not in [j["id"] for j in neg]

    def test_new_job_excluded_from_both_lists(self):
        job_id = _insert()
        pos, neg = job_repository.get_examples()
        assert job_id not in [j["id"] for j in pos]
        assert job_id not in [j["id"] for j in neg]


class TestSearch:
    def test_no_filters_returns_all(self):
        id1 = _insert(url="https://a.com/1")
        id2 = _insert(company="Beta", url="https://a.com/2")
        ids = [r["id"] for r in job_repository.search(status="all")]
        assert id1 in ids and id2 in ids

    def test_filter_by_status(self):
        id1 = _insert(url="https://a.com/1")
        _insert(company="Beta", url="https://a.com/2")
        job_repository.update_status(id1, "reviewed")
        result = job_repository.search(status="reviewed")
        assert [r["id"] for r in result] == [id1]

    def test_filter_by_min_score_excludes_lower_scored_jobs(self):
        id1 = _insert(url="https://a.com/1")
        id2 = _insert(company="Beta", url="https://a.com/2")
        job_repository.update_score(id1, 8.0, "Great")
        job_repository.update_score(id2, 3.0, "Weak")
        result = job_repository.search(status="all", min_score=6.0)
        ids = [r["id"] for r in result]
        assert id1 in ids
        assert id2 not in ids

    def test_filter_by_text_query_matches_title(self):
        unique = uuid.uuid4().hex[:8]
        id1 = _insert(title=f"PHP Developer {unique}", url="https://a.com/1")
        _insert(title="Java Engineer", company="Beta", url="https://a.com/2")
        result = job_repository.search(status="all", query=unique)
        assert [r["id"] for r in result] == [id1]

    def test_limit_and_offset(self):
        id1 = _insert(url="https://a.com/1")
        id2 = _insert(company="Beta", url="https://a.com/2")
        id3 = _insert(company="Gamma", url="https://a.com/3")
        job_repository.update_score(id1, 9.0, "great")
        job_repository.update_score(id2, 8.0, "good")
        job_repository.update_score(id3, 7.0, "ok")

        result = job_repository.search(status="all", limit=2)
        assert [r["id"] for r in result] == [id1, id2]

        result = job_repository.search(status="all", limit=2, offset=2)
        assert [r["id"] for r in result] == [id3]


class TestGetMissingDescriptions:
    def test_returns_job_without_description(self):
        job_id = job_repository.insert("Dev", "Co", "PL", _unique_url("https://a.com/nd"), "linkedin")
        ids = [j["id"] for j in job_repository.get_missing_descriptions()]
        assert job_id in ids

    def test_excludes_job_with_description(self):
        job_id = _insert()  # has description in defaults
        ids = [j["id"] for j in job_repository.get_missing_descriptions()]
        assert job_id not in ids

    def test_excludes_already_scored_job(self):
        job_id = job_repository.insert("Dev", "Co", "PL", _unique_url("https://a.com/s"), "linkedin")
        job_repository.update_score(job_id, 7.0, "OK")
        ids = [j["id"] for j in job_repository.get_missing_descriptions()]
        assert job_id not in ids

    def test_includes_non_linkedin_sources(self):
        job_id = job_repository.insert("Dev", "Co", "PL", _unique_url("https://justjoin.it/job-offer/nd"), "justjoin")
        result = job_repository.get_missing_descriptions()
        row = next(j for j in result if j["id"] == job_id)
        assert row["source"] == "justjoin"


class TestGetMissingStructuredData:
    def test_returns_job_with_description_and_no_extraction(self):
        job_id = _insert()  # has description in defaults
        ids = [j["id"] for j in job_repository.get_missing_structured_data()]
        assert job_id in ids

    def test_excludes_already_extracted_job(self):
        job_id = _insert()
        job_repository.update_structured_data(job_id, {"remote": True})
        ids = [j["id"] for j in job_repository.get_missing_structured_data()]
        assert job_id not in ids

    def test_excludes_job_without_description(self):
        job_id = job_repository.insert("Dev", "Co", "PL", _unique_url("https://a.com/nd"), "linkedin")
        ids = [j["id"] for j in job_repository.get_missing_structured_data()]
        assert job_id not in ids


class TestGetStats:
    def test_includes_last_run_key(self):
        stats = job_repository.get_stats()
        assert "last_run" in stats

    def test_last_run_is_none_when_no_sessions(self):
        stats = job_repository.get_stats()
        assert stats["last_run"] is None

    def test_counts_per_status(self):
        id1 = _insert(url="https://a.com/1")
        id2 = _insert(company="Beta", url="https://a.com/2")
        job_repository.update_status(id1, "applied")
        job_repository.update_score_and_status(id2, 0.0, "Bad", "auto_rejected")
        stats = job_repository.get_stats()
        assert stats["total"] >= 2
        assert stats["applied"] >= 1
        assert stats["auto_rejected"] >= 1


class TestGetAllUrls:
    def test_returns_all_inserted_urls(self):
        url1 = _unique_url("https://a.com/1")
        url2 = _unique_url("https://a.com/2")
        job_repository.insert(
            title="PHP Developer", company="Acme Corp", location="Poland",
            url=url1, source="linkedin", description="desc",
        )
        job_repository.insert(
            title="PHP Developer", company="Beta", location="Poland",
            url=url2, source="linkedin", description="desc",
        )
        # get_all_urls() is system-wide (shared job pool), assert our urls are
        # present, not that the result equals exactly {url1, url2}.
        urls = job_repository.get_all_urls()
        assert url1 in urls
        assert url2 in urls


class TestGetNew:
    def test_returns_new_jobs_only(self):
        id1 = _insert(url="https://a.com/1")
        id2 = _insert(company="Beta", url="https://a.com/2")
        job_repository.update_status(id2, "applied")
        ids = [j["id"] for j in job_repository.get_new()]
        assert id1 in ids
        assert id2 not in ids


class TestGetNewWithDescriptions:
    def test_returns_new_jobs_with_descriptions(self):
        id1 = _insert(url="https://a.com/1")
        _mark_extracted(id1)
        job_repository.insert("No desc", "Co", "PL", _unique_url("https://a.com/nd"), "linkedin")
        result = job_repository.get_new_with_descriptions()
        ids = [r["id"] for r in result]
        assert id1 in ids

    def test_excludes_jobs_without_description(self):
        job_id = job_repository.insert("No desc", "Co", "PL", _unique_url("https://a.com/nd"), "linkedin")
        result = job_repository.get_new_with_descriptions()
        assert job_id not in [r["id"] for r in result]


class TestGetByStatus:
    def test_returns_only_matching_status(self):
        id1 = _insert(url="https://a.com/1")
        id2 = _insert(company="Beta", url="https://a.com/2")
        job_repository.update_status(id1, "applied")
        result = job_repository.get_by_status("applied")
        ids = [r["id"] for r in result]
        assert id1 in ids
        assert id2 not in ids


class TestGetAllFeedback:
    def test_applied_jobs_in_first_list(self):
        id1 = _insert(url="https://a.com/1")
        job_repository.update_status(id1, "applied")
        applied, rejected = job_repository.get_all_feedback()
        assert len(applied) >= 1

    def test_rejected_jobs_in_second_list(self):
        id1 = _insert(url="https://a.com/1")
        job_repository.update_status(id1, "rejected")
        applied, rejected = job_repository.get_all_feedback()
        assert len(rejected) >= 1

    def test_new_jobs_excluded(self):
        before_applied, before_rejected = job_repository.get_all_feedback()
        _insert(url="https://a.com/1")
        applied, rejected = job_repository.get_all_feedback()
        assert len(applied) == len(before_applied)
        assert len(rejected) == len(before_rejected)


class TestGetFeedbackSince:
    def test_returns_decisions_after_timestamp(self):
        id1 = _insert(url="https://a.com/1")
        job_repository.update_status(id1, "applied")
        applied, rejected = job_repository.get_feedback_since("2000-01-01")
        assert len(applied) >= 1

    def test_excludes_decisions_before_timestamp(self):
        id1 = _insert(url="https://a.com/1")
        job_repository.update_status(id1, "applied")
        applied, rejected = job_repository.get_feedback_since("2099-01-01")
        assert applied == []


class TestCountByFilter:
    def test_returns_zero_for_empty_statuses(self):
        assert job_repository.count_by_filter([]) == 0

    def test_counts_matching_status(self):
        id1 = _insert(url="https://a.com/1")
        _insert(company="Beta", url="https://a.com/2")
        job_repository.update_status(id1, "applied")
        assert job_repository.count_by_filter(["applied"]) >= 1

    def test_counts_multiple_statuses(self):
        before = job_repository.count_by_filter(["applied", "rejected"])
        id1 = _insert(url="https://a.com/1")
        id2 = _insert(company="Beta", url="https://a.com/2")
        job_repository.update_status(id1, "applied")
        job_repository.update_status(id2, "rejected")
        assert job_repository.count_by_filter(["applied", "rejected"]) == before + 2


class TestDeleteByFilter:
    def test_deletes_matching_jobs_and_returns_count(self):
        before = job_repository.count_by_filter(["rejected"])
        id1 = _insert(url="https://a.com/1")
        _insert(company="Beta", url="https://a.com/2")
        job_repository.update_status(id1, "rejected")
        deleted = job_repository.delete_by_filter(["rejected"])
        assert deleted == before + 1
        assert job_repository.count_by_filter(["rejected"]) == 0

    def test_returns_zero_for_empty_statuses(self):
        assert job_repository.delete_by_filter([]) == 0


class TestUpdateStructuredData:
    def test_stores_and_retrieves_json(self):
        id1 = _insert(url="https://a.com/1")
        data = {"remote": True, "seniority": "senior", "stack": ["python"]}
        job_repository.update_structured_data(id1, data)
        result = job_repository.search(status="all")
        job = next(j for j in result if j["id"] == id1)
        stored = json.loads(job["structured_data"])
        assert stored["seniority"] == "senior"
        assert stored["stack"] == ["python"]


class TestUpdateRankingScores:
    def test_updates_all_ranking_fields(self):
        id1 = _insert(url="https://a.com/1")
        job_repository.update_ranking_scores(id1, embedding_score=0.88, rerank_score=0.75, listwise_rank=3)
        job = next(j for j in job_repository.search(status="all") if j["id"] == id1)
        assert abs(job["embedding_score"] - 0.88) < 1e-9
        assert abs(job["rerank_score"] - 0.75) < 1e-9
        assert job["listwise_rank"] == 3

    def test_accepts_none_values(self):
        id1 = _insert(url="https://a.com/1")
        job_repository.update_ranking_scores(id1, None, None, None)  # should not raise


class TestUpdateWouldApply:
    def test_flags_job_true_with_reason(self):
        id1 = _insert(url="https://a.com/1")
        job_repository.update_would_apply(id1, True, "Score 8.0 >= 7.0, no dealbreaker risk flagged")
        job = next(j for j in job_repository.search(status="all") if j["id"] == id1)
        assert job["would_apply"] == 1
        assert job["would_apply_reason"] == "Score 8.0 >= 7.0, no dealbreaker risk flagged"

    def test_flags_job_false(self):
        id1 = _insert(url="https://a.com/1")
        job_repository.update_would_apply(id1, False, "")
        job = next(j for j in job_repository.search(status="all") if j["id"] == id1)
        assert job["would_apply"] == 0


class TestGetWouldApplyStats:
    def test_counts_applied_and_rejected_among_flagged(self):
        applied_id = _insert(company="A", url="https://a.com/1")
        rejected_id = _insert(company="B", url="https://a.com/2")
        open_id = _insert(company="C", url="https://a.com/3")
        for jid in (applied_id, rejected_id, open_id):
            job_repository.update_would_apply(jid, True, "Score 8.0 >= 7.0, no dealbreaker risk flagged")
        job_repository.update_status(applied_id, "applied")
        job_repository.update_status(rejected_id, "rejected")

        stats = job_repository.get_would_apply_stats()
        assert stats["flagged_total"] == 3
        assert stats["applied"] == 1
        assert stats["rejected"] == 1
        assert stats["decided"] == 2
        assert abs(stats["precision"] - 0.5) < 1e-9

    def test_unflagged_jobs_are_excluded(self):
        id1 = _insert(url="https://a.com/1")
        job_repository.update_would_apply(id1, False, "")
        job_repository.update_status(id1, "applied")
        stats = job_repository.get_would_apply_stats()
        assert stats["flagged_total"] == 0

    def test_reviewed_and_new_are_excluded_from_decided(self):
        flagged_new = _insert(company="A", url="https://a.com/1")
        flagged_reviewed = _insert(company="B", url="https://a.com/2")
        job_repository.update_would_apply(flagged_new, True, "reason")
        job_repository.update_would_apply(flagged_reviewed, True, "reason")
        job_repository.update_status(flagged_reviewed, "reviewed")

        stats = job_repository.get_would_apply_stats()
        assert stats["flagged_total"] == 2
        assert stats["decided"] == 0
        assert stats["precision"] is None


class TestGetJobsForRanking:
    def test_returns_new_jobs_without_listwise_rank(self):
        id1 = _insert(url="https://a.com/1")
        _mark_extracted(id1)
        result = job_repository.get_jobs_for_ranking()
        ids = [r["id"] for r in result]
        assert id1 in ids

    def test_includes_already_ranked_jobs(self):
        """The whole active pool is re-ranked every run, not just newly-arrived jobs."""
        id1 = _insert(url="https://a.com/1")
        _mark_extracted(id1)
        job_repository.update_ranking_scores(id1, None, None, listwise_rank=1)
        result = job_repository.get_jobs_for_ranking()
        ids = [r["id"] for r in result]
        assert id1 in ids

    def test_excludes_jobs_without_descriptions(self):
        job_id = job_repository.insert("No desc", "Co", "PL", _unique_url("https://a.com/nd"), "linkedin")
        ids = [r["id"] for r in job_repository.get_jobs_for_ranking()]
        assert job_id not in ids


class TestGetAppliedAndRejectedJobIds:
    def test_get_applied_job_ids(self):
        id1 = _insert(url="https://a.com/1")
        id2 = _insert(company="Beta", url="https://a.com/2")
        job_repository.update_status(id1, "applied")
        result = job_repository.get_applied_job_ids()
        assert id1 in result
        assert id2 not in result

    def test_get_rejected_job_ids(self):
        id1 = _insert(url="https://a.com/1")
        job_repository.update_status(id1, "rejected")
        assert id1 in job_repository.get_rejected_job_ids()


class TestCountDecisions:
    def test_counts_applied_and_rejected(self):
        before = job_repository.count_decisions()
        id1 = _insert(url="https://a.com/1")
        id2 = _insert(company="Beta", url="https://a.com/2")
        job_repository.update_status(id1, "applied")
        job_repository.update_status(id2, "rejected")
        assert job_repository.count_decisions() == before + 2


class TestUpdateStatusWithRejectionReason:
    def test_stores_rejection_reason(self):
        id1 = _insert(url="https://a.com/1")
        job_repository.update_status(id1, "rejected", rejection_reason="stawka za niska")
        result = job_repository.get_by_status("rejected")
        job = next(j for j in result if j["id"] == id1)
        assert job["rejection_reason"] == "stawka za niska"

    def test_no_rejection_reason_for_other_statuses(self):
        id1 = _insert(url="https://a.com/1")
        job_repository.update_status(id1, "applied", rejection_reason="ignored")
        result = job_repository.get_by_status("applied")
        job = next(j for j in result if j["id"] == id1)
        assert job["rejection_reason"] is None


class TestGetStatsRanked:
    def test_ranked_excludes_non_new_status(self):
        id1 = _insert(url="https://a.com/1")
        job_repository.update_ranking_scores(id1, None, None, listwise_rank=1)
        before = job_repository.get_stats()["ranked"]
        job_repository.update_status(id1, "applied")
        after = job_repository.get_stats()["ranked"]
        assert after == before - 1
