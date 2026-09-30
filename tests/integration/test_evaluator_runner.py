import uuid
from contextlib import contextmanager
from unittest.mock import patch
from db.repositories import job_repository, candidate_preferences_repository
from evaluator.fingerprints import score_fingerprint
from evaluator.runner import run


def _unique_url(url: str) -> str:
    """job_postings is shared/global and never truncated between tests, reusing
    a literal url across tests would silently reuse another test's stale posting
    fields (title/company) instead of creating a fresh one."""
    return f"{url}?t={uuid.uuid4().hex}"


def _insert_scoreable(url="https://example.com/1", **kwargs):
    """Insert a job that has a description and is ready to be scored."""
    defaults = dict(title="PHP Developer", company=f"Acme Corp {uuid.uuid4().hex}",
                    location="Poland", source="linkedin",
                    description="Symfony expertise required.")
    return job_repository.insert(**{**defaults, "url": _unique_url(url), **kwargs})


def _good_score(**overrides):
    return {
        "score": 7.5,
        "score_reason": "Good PHP and Symfony match",
        **overrides,
    }


@contextmanager
def _patched_run(score_result=None, side_effect=None):
    """Patch load_active_profile, build_system_prompt, and score_job together."""
    score_kwargs = {"side_effect": side_effect} if side_effect else {"return_value": score_result or _good_score()}
    with patch("evaluator.runner.load_active_profile", return_value="fake profile"):
        with patch("evaluator.runner.build_system_prompt", return_value="fake prompt"):
            with patch("evaluator.runner.score_job", **score_kwargs) as mock_score:
                yield mock_score


class TestEvaluatorRunner:
    def test_no_jobs_returns_zero_counts(self):
        result = run()
        assert result == {"jobs_scored": 0}

    def test_job_without_description_is_skipped(self):
        job_repository.insert("No desc", "Co", "PL", _unique_url("https://a.com/1"), "linkedin")
        with _patched_run() as mock_score:
            run()
        mock_score.assert_not_called()

    def test_missing_cv_profile_returns_zero_counts(self):
        _insert_scoreable()
        with patch("evaluator.runner.load_active_profile", side_effect=ValueError("No CV")):
            result = run()
        assert result == {"jobs_scored": 0}

    def test_normal_scoring_sets_score_keeps_new_status(self):
        job_id = _insert_scoreable()
        with _patched_run():
            result = run()
        assert result["jobs_scored"] == 1
        job = next(j for j in job_repository.search(status="all") if j["id"] == job_id)
        assert job["score"] == 7.5
        assert job["status"] == "new"

    def test_explicit_jobs_list_skips_the_fetch_and_is_used_directly(self):
        # Regression: scripts/rescore_new.py already fetches this same list to
        # check emptiness and print a count before calling run(force_rescore=True)
        #, run() used to redundantly re-fetch the identical (potentially large,
        # full-description) list itself.
        job_id = _insert_scoreable()
        job = next(j for j in job_repository.search(status="all") if j["id"] == job_id)
        with _patched_run(), \
             patch("evaluator.runner.job_repository.get_new_with_descriptions") as mock_fetch, \
             patch("evaluator.runner.job_repository.get_unscored") as mock_unscored:
            result = run(force_rescore=True, jobs=[job])
        mock_fetch.assert_not_called()
        mock_unscored.assert_not_called()
        assert result["jobs_scored"] == 1

    def test_build_system_prompt_called_once_for_entire_batch(self):
        _insert_scoreable(url="https://a.com/1")
        _insert_scoreable(company="Beta", url="https://a.com/2")
        with patch("evaluator.runner.load_active_profile", return_value="fake profile"):
            with patch("evaluator.runner.build_system_prompt", return_value="fake") as mock_prompt:
                with patch("evaluator.runner.score_job", return_value=_good_score()):
                    run()
        mock_prompt.assert_called_once()

    def test_already_scored_job_not_rescored(self):
        job_id = _insert_scoreable()
        job = next(j for j in job_repository.search(status="all") if j["id"] == job_id)
        job_repository.update_score(job_id, 6.0, "Already scored", fingerprint=score_fingerprint(job, "fake prompt"))
        with _patched_run() as mock_score:
            run()
        mock_score.assert_not_called()

    def test_score_reason_stored_in_db(self):
        job_id = _insert_scoreable()
        with _patched_run(_good_score(score_reason="Symfony match, remote OK")):
            run()
        job = next(j for j in job_repository.search(status="all") if j["id"] == job_id)
        assert "Symfony match" in job["score_reason"]

    def test_multiple_jobs_all_scored(self):
        _insert_scoreable(url="https://a.com/1")
        _insert_scoreable(company="Beta", url="https://a.com/2")
        with _patched_run(side_effect=[_good_score(), _good_score(score=5.0)]):
            result = run()
        assert result["jobs_scored"] == 2

    def test_scoring_failure_leaves_job_unscored_for_retry(self):
        job_id = _insert_scoreable()
        error_result = {"score": None, "score_reason": "API error: timeout"}
        with _patched_run(side_effect=[error_result]):
            result = run()
        assert result["jobs_scored"] == 0
        job = next(j for j in job_repository.search(status="all") if j["id"] == job_id)
        assert job["score"] is None

    def test_low_score_auto_rejects_job(self):
        job_id = _insert_scoreable()
        with _patched_run(_good_score(score=0.5, score_reason="No PHP, wrong field entirely")):
            result = run()
        assert result["jobs_scored"] == 1
        assert result["jobs_auto_rejected"] == 1
        job = next(j for j in job_repository.get_by_status("auto_rejected") if j["id"] == job_id)
        assert job["score"] == 0.5

    def test_score_at_threshold_is_auto_rejected(self):
        job_id = _insert_scoreable()
        with _patched_run(_good_score(score=1.0)):
            run()
        job = next(j for j in job_repository.search(status="all") if j["id"] == job_id)
        assert job["status"] == "auto_rejected"

    def test_score_just_above_threshold_stays_new(self):
        job_id = _insert_scoreable()
        with _patched_run(_good_score(score=1.5)):
            result = run()
        assert result["jobs_auto_rejected"] == 0
        job = next(j for j in job_repository.search(status="all") if j["id"] == job_id)
        assert job["status"] == "new"

    def test_divergence_cases_passed_to_build_system_prompt(self):
        _insert_scoreable()
        fake_cases = [{"divergence_type": "false_positive", "listwise_rank": 2, "title": "X", "rejection_reason": "Y"}]
        with patch("evaluator.runner.load_active_profile", return_value="fake profile"):
            with patch("evaluator.runner.divergence_cases", return_value=fake_cases):
                with patch("evaluator.runner.build_system_prompt", return_value="fake") as mock_prompt:
                    with patch("evaluator.runner.score_job", return_value=_good_score()):
                        run()
        assert mock_prompt.call_args.kwargs["divergence_cases"] == fake_cases

    def test_previously_scored_job_rejected_when_it_now_violates_a_dealbreaker(self):
        # Regression: the dealbreaker filter used to run only once, at first
        # scoring (get_unscored()'s score IS NULL filter excluded it from ever
        # being checked again), a job that was fine when first scored but now
        # violates a tightened/newly-added criterion (or was scored
        # dealbreaker-blind because extraction hadn't succeeded yet) used to sit
        # in the pool forever.
        job_id = _insert_scoreable()
        job_repository.update_structured_data(job_id, {
            "seniority": "junior", "_field_confidence": {"seniority": "high"},
        })
        job_repository.update_score(job_id, 6.0, "scored before this seniority preference existed")
        candidate_preferences_repository.insert(None, {
            "seniority_levels": ["senior"], "required_seniority_levels": ["senior"],
        })

        with _patched_run() as mock_score:
            result = run()

        mock_score.assert_not_called()  # dealbreaker catches it before any LLM call
        assert result["jobs_auto_rejected"] == 1
        job = next(j for j in job_repository.get_by_status("auto_rejected") if j["id"] == job_id)
        assert job["score"] == 0.0

    def test_previously_scored_job_left_alone_when_still_dealbreaker_clean(self):
        job_id = _insert_scoreable()
        job_repository.update_structured_data(job_id, {
            "seniority": "senior", "_field_confidence": {"seniority": "high"},
        })
        candidate_preferences_repository.insert(None, {
            "seniority_levels": ["senior"], "required_seniority_levels": ["senior"],
        })
        job = next(j for j in job_repository.search(status="all") if j["id"] == job_id)
        job_repository.update_score(job_id, 6.0, "still a fine match", fingerprint=score_fingerprint(job, "fake prompt"))

        with _patched_run() as mock_score:
            result = run()

        mock_score.assert_not_called()  # already scored, not re-sent to the LLM
        assert result == {"jobs_scored": 0, "jobs_auto_rejected": 0}
        job = next(j for j in job_repository.search(status="all") if j["id"] == job_id)
        assert job["status"] == "new"
        assert job["score"] == 6.0

    def test_dealbreaker_rejected_job_returns_when_requirement_is_relaxed(self):
        job_id = _insert_scoreable()
        job_repository.update_structured_data(job_id, {
            "seniority": "junior", "_field_confidence": {"seniority": "high"},
        })
        candidate_preferences_repository.insert(None, {
            "required_seniority_levels": ["senior"],
        })
        with _patched_run() as mock_score:
            first = run()
        assert first["jobs_auto_rejected"] == 1
        mock_score.assert_not_called()

        candidate_preferences_repository.insert(None, {"required_seniority_levels": []})
        with _patched_run(_good_score(score=8.0)):
            second = run()

        assert second["jobs_scored"] == 1
        job = next(j for j in job_repository.search(status="all") if j["id"] == job_id)
        assert job["status"] == "new"
        assert job["score"] == 8.0

    def test_divergence_cases_capped_at_limit(self):
        _insert_scoreable()
        many_cases = [
            {"divergence_type": "false_positive", "listwise_rank": i, "title": f"Job {i}", "rejection_reason": "x"}
            for i in range(50)
        ]
        with patch("evaluator.runner.load_active_profile", return_value="fake profile"):
            with patch("evaluator.runner.divergence_cases", return_value=many_cases):
                with patch("evaluator.runner.build_system_prompt", return_value="fake") as mock_prompt:
                    with patch("evaluator.runner.score_job", return_value=_good_score()):
                        run()
        assert len(mock_prompt.call_args.kwargs["divergence_cases"]) == 10
