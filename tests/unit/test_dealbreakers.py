import json
from unittest.mock import patch

from evaluator.dealbreakers import apply_dealbreaker_filter


def _job(job_id="job1", structured=None, **overrides):
    if structured is not None and "_field_confidence" not in structured:
        structured = {
            **structured,
            "_field_confidence": {key: "high" for key, value in structured.items() if value not in (None, [], "unknown")},
        }
    job = {
        "id": job_id, "title": "PHP Developer", "company": "Acme",
        "structured_data": json.dumps(structured) if structured is not None else None,
    }
    job.update(overrides)
    return job


def _prefs(**overrides):
    base = {
        "salary_min": None, "salary_currency": None, "work_mode": [],
        "work_country": "", "remote_countries": [], "seniority_levels": [],
        "show_jobs_without_salary": None, "preferred_company_types": [], "languages": [],
    }
    base.update(overrides)
    return base


class TestNoActivePreferences:
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active", return_value=None)
    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    def test_passes_everything_through(self, mock_update, mock_prefs):
        jobs = [_job("a"), _job("b")]
        surviving, stats = apply_dealbreaker_filter(jobs)
        assert surviving == jobs
        assert stats == {"checked": 2, "auto_rejected": 0}
        mock_update.assert_not_called()


class TestPreferenceStrengthAndConfidence:
    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_soft_seniority_and_company_preferences_do_not_reject(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(
            seniority_levels=["senior"],
            required_seniority_levels=[],
            preferred_company_types=["product"],
            required_company_types=[],
        )
        job = _job(structured={"seniority": "mid", "product_vs_outsourcing": "outsourcing"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        assert stats["auto_rejected"] == 0
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_work_country_not_employer_country_drives_remote_eligibility(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(
            work_mode=["remote"], work_country="Poland", employer_countries=["United States"]
        )
        job = _job(structured={"remote": True, "remote_regions": ["United States"]})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == []
        assert stats["auto_rejected"] == 1

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_legacy_untrusted_extraction_does_not_hard_reject(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(work_mode=["remote"], work_country="Poland")
        structured = {"remote": True, "remote_regions": ["United States"]}
        job = _job(structured=structured)
        job["structured_data"] = json.dumps(structured)
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        assert stats["auto_rejected"] == 0
        mock_update.assert_not_called()


class TestSalaryFloor:
    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_rejects_below_minimum_yearly(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(salary_min=150000, salary_currency="PLN")
        job = _job(structured={"salary_max": 100000, "salary_currency": "PLN", "salary_period": "yearly"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == []
        assert stats["auto_rejected"] == 1
        mock_update.assert_called_once()
        assert mock_update.call_args[0][3] == "auto_rejected"
        assert "salary_max 100000" in mock_update.call_args[0][2]

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_passes_at_or_above_minimum_yearly(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(salary_min=150000, salary_currency="PLN")
        job = _job(structured={"salary_max": 200000, "salary_currency": "PLN", "salary_period": "yearly"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        assert stats["auto_rejected"] == 0
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_monthly_rate_normalized_before_comparison(self, mock_prefs, mock_update):
        # 20000 PLN/month = 240000 PLN/year, comfortably above a 150000/year floor.
        mock_prefs.return_value = _prefs(salary_min=150000, salary_currency="PLN")
        job = _job(structured={"salary_max": 20000, "salary_currency": "PLN", "salary_period": "monthly"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_low_monthly_rate_still_rejected_after_normalization(self, mock_prefs, mock_update):
        # 5000 PLN/month = 60000 PLN/year, below a 150000/year floor.
        mock_prefs.return_value = _prefs(salary_min=150000, salary_currency="PLN")
        job = _job(structured={"salary_max": 5000, "salary_currency": "PLN", "salary_period": "monthly"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == []
        assert stats["auto_rejected"] == 1

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_hourly_b2b_rate_not_mistaken_for_a_starvation_salary(self, mock_prefs, mock_update):
        # Regression test for the real bug: "100-145 PLN/h" B2B rate must never be
        # compared directly against an annual floor as if it were annual pay.
        # 145 PLN/h * 168h/month * 12 = 292,320 PLN/year, well above 150000/year.
        mock_prefs.return_value = _prefs(salary_min=150000, salary_currency="PLN")
        job = _job(structured={"salary_max": 145, "salary_currency": "PLN", "salary_period": "hourly"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_low_hourly_rate_still_rejected_after_normalization(self, mock_prefs, mock_update):
        # 20 PLN/h * 2016h/year = 40,320 PLN/year, below a 150000/year floor.
        mock_prefs.return_value = _prefs(salary_min=150000, salary_currency="PLN")
        job = _job(structured={"salary_max": 20, "salary_currency": "PLN", "salary_period": "hourly"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == []
        assert stats["auto_rejected"] == 1

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_skips_when_pay_period_unknown(self, mock_prefs, mock_update):
        # Old/un-migrated structured_data without salary_period, never guess a
        # basis, skip rather than risk a false rejection.
        mock_prefs.return_value = _prefs(salary_min=150000, salary_currency="PLN")
        job = _job(structured={"salary_max": 100, "salary_currency": "PLN"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_skips_when_job_has_no_structured_data(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(salary_min=15000, salary_currency="PLN")
        job = _job(structured=None)
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_skips_when_job_salary_currency_missing(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(salary_min=15000, salary_currency="PLN")
        job = _job(structured={"salary_max": 5000, "salary_currency": None, "salary_period": "monthly"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_currency_mismatch_now_converted_and_compared(self, mock_prefs, mock_update):
        # Regression: a currency mismatch used to skip the check entirely
        # rather than convert. 3000 EUR/month * 12 * 4.3 ~= 154,800 PLN/year,
        # comfortably above a 15,000 PLN/year floor, passes, but now because
        # it was genuinely compared, not because the check was bypassed.
        mock_prefs.return_value = _prefs(salary_min=15000, salary_currency="PLN")
        job = _job(structured={"salary_max": 3000, "salary_currency": "EUR", "salary_period": "monthly"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_low_foreign_currency_salary_now_rejected_after_conversion(self, mock_prefs, mock_update):
        # Regression for the audit's exact finding: before this fix, this job
        # would have silently skipped the floor check entirely (currency
        # mismatch) instead of being conservatively converted and compared.
        # 500 EUR/month * 12 * 4.3 ~= 25,800 PLN/year, below a 150,000 floor.
        mock_prefs.return_value = _prefs(salary_min=150000, salary_currency="PLN")
        job = _job(structured={"salary_max": 500, "salary_currency": "EUR", "salary_period": "monthly"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == []
        assert stats["auto_rejected"] == 1
        assert "PLN" in mock_update.call_args[0][2]

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_candidate_salary_in_foreign_currency_also_converted(self, mock_prefs, mock_update):
        # Candidate's own floor is in EUR, job pay is in PLN, both sides must
        # convert, not just the job side.
        mock_prefs.return_value = _prefs(salary_min=30000, salary_currency="EUR")  # ~129,000 PLN/year
        job = _job(structured={"salary_max": 100000, "salary_currency": "PLN", "salary_period": "yearly"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == []
        assert stats["auto_rejected"] == 1

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_unsupported_currency_skips_rather_than_guesses(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(salary_min=150000, salary_currency="PLN")
        job = _job(structured={"salary_max": 100, "salary_currency": "JPY", "salary_period": "hourly"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_no_candidate_salary_min_never_filters(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(salary_min=None, salary_currency=None)
        job = _job(structured={"salary_max": 1, "salary_currency": "PLN", "salary_period": "yearly"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()


class TestRemoteOnlyMismatch:
    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_rejects_hybrid_job_for_remote_only_candidate(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(work_mode=["remote"])
        job = _job(structured={"remote": False, "hybrid": True})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == []
        assert stats["auto_rejected"] == 1
        assert "remote-only" in mock_update.call_args[0][2]

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_rejects_onsite_job_for_remote_only_candidate(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(work_mode=["remote"])
        job = _job(structured={"remote": False, "hybrid": False})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == []
        assert stats["auto_rejected"] == 1

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_passes_remote_job_for_remote_only_candidate(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(work_mode=["remote"])
        job = _job(structured={"remote": True, "hybrid": False})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_rejects_job_offering_both_remote_and_hybrid(self, mock_prefs, mock_update):
        # hybrid=True disqualifies a remote-only candidate even when remote=True is
        # also set, confirmed with the candidate: hybrid means occasional required
        # office days regardless of what else the posting advertises.
        mock_prefs.return_value = _prefs(work_mode=["remote"])
        job = _job(structured={"remote": True, "hybrid": True})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == []
        assert stats["auto_rejected"] == 1

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_rejects_hybrid_only_job_when_remote_unknown(self, mock_prefs, mock_update):
        # remote wasn't extracted (None), but hybrid=True is confirmed, still a
        # violation, since we only know about the hybrid requirement, not full remote.
        mock_prefs.return_value = _prefs(work_mode=["remote"])
        job = _job(structured={"remote": None, "hybrid": True})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == []
        assert stats["auto_rejected"] == 1

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_skips_when_no_structured_work_mode_data(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(work_mode=["remote"])
        job = _job(structured={})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_hybrid_candidate_never_filtered_by_work_mode_rule(self, mock_prefs, mock_update):
        # work_mode includes hybrid, not remote-only, rule only applies to exactly ["remote"].
        mock_prefs.return_value = _prefs(work_mode=["remote", "hybrid"])
        job = _job(structured={"remote": False, "hybrid": False})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()


class TestGeoRestriction:
    # Etap 2-4 geo hole: a job can be genuinely remote (passes the remote-only
    # check above) but restricted to countries that don't include the
    # candidate's, e.g. "Remote, US only" reaching a candidate in Poland.

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_rejects_remote_job_restricted_to_a_different_country(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(work_mode=["remote"], remote_countries=["Poland"])
        job = _job(structured={"remote": True, "remote_regions": ["United States"]})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == []
        assert stats["auto_rejected"] == 1
        assert "restricted to United States" in mock_update.call_args[0][2]

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_passes_remote_job_when_candidate_country_is_eligible(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(work_mode=["remote"], remote_countries=["Poland"])
        job = _job(structured={"remote": True, "remote_regions": ["Poland", "Ukraine"]})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_eu_region_matches_an_eu_candidate_country(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(work_mode=["remote"], remote_countries=["Poland"])
        job = _job(structured={"remote": True, "remote_regions": ["EU"]})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_worldwide_region_never_restricts_anyone(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(work_mode=["remote"], remote_countries=["Poland"])
        job = _job(structured={"remote": True, "remote_regions": ["Worldwide"]})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_empty_remote_regions_never_rejects(self, mock_prefs, mock_update):
        # Critical null-safety case: empty list means "unstated" per the
        # extractor's own schema, never "no countries allowed".
        mock_prefs.return_value = _prefs(work_mode=["remote"], remote_countries=["Poland"])
        job = _job(structured={"remote": True, "remote_regions": []})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_missing_remote_regions_key_never_rejects(self, mock_prefs, mock_update):
        # Every job extracted before this field existed has no "remote_regions"
        # key at all, the entire existing pool must be unaffected by enabling
        # this check.
        mock_prefs.return_value = _prefs(work_mode=["remote"], remote_countries=["Poland"])
        job = _job(structured={"remote": True})  # pre-migration shape, key absent
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_no_candidate_remote_countries_never_rejects(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(work_mode=["remote"], remote_countries=[])
        job = _job(structured={"remote": True, "remote_regions": ["United States"]})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_non_remote_job_never_checked_for_geo(self, mock_prefs, mock_update):
        # remote is False, a hybrid/onsite posting, a different dealbreaker's
        # concern, not the geo-remote one. Uses work_mode=["remote","hybrid"] so
        # _remote_only_reason's own (unrelated, exact-match-["remote"]) rule
        # doesn't also fire and mask what this test is isolating.
        mock_prefs.return_value = _prefs(work_mode=["remote", "hybrid"], remote_countries=["Poland"])
        job = _job(structured={"remote": False, "remote_regions": ["United States"]})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_candidate_not_wanting_remote_never_triggers_geo_check(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(work_mode=["hybrid"], remote_countries=["Poland"])
        job = _job(structured={"remote": True, "remote_regions": ["United States"]})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_geo_check_applies_even_when_candidate_also_open_to_hybrid(self, mock_prefs, mock_update):
        # Broadened trigger: "remote" in work_mode, not work_mode == ["remote"],
        # a candidate open to both hybrid and remote still cares whether a
        # specific remote posting's geo restriction excludes them.
        mock_prefs.return_value = _prefs(work_mode=["remote", "hybrid"], remote_countries=["Poland"])
        job = _job(structured={"remote": True, "remote_regions": ["United States"]})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == []
        assert stats["auto_rejected"] == 1


class TestSeniorityMismatch:
    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_rejects_job_outside_selected_levels(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(seniority_levels=["senior", "lead"])
        job = _job(structured={"seniority": "junior"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == []
        assert stats["auto_rejected"] == 1
        assert "junior" in mock_update.call_args[0][2]

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_passes_job_within_selected_levels(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(seniority_levels=["senior", "lead"])
        job = _job(structured={"seniority": "senior"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_unknown_job_seniority_never_rejects(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(seniority_levels=["senior"])
        job = _job(structured={"seniority": None})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_no_candidate_seniority_preference_never_filters(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(seniority_levels=[])
        job = _job(structured={"seniority": "junior"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_seniority_check_runs_even_with_no_salary_or_work_mode_preference(self, mock_prefs, mock_update):
        # Regression: the early-exit skip must also consider seniority_levels
        # on its own, not just salary_min/work_mode.
        mock_prefs.return_value = _prefs(seniority_levels=["lead"])
        job = _job(structured={"seniority": "junior"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == []
        assert stats["auto_rejected"] == 1


class TestNoSalaryDisclosed:
    # Regression: the "Also show postings with no salary listed" checkbox
    # (checked/True by default) was saved to preferences but never read.

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_unverified_undisclosed_salary_is_not_hard_rejected(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(show_jobs_without_salary=False)
        job = _job(structured={})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        assert stats["auto_rejected"] == 0
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_disclosed_salary_never_rejected_by_this_check(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(show_jobs_without_salary=False)
        job = _job(structured={"salary_max": 100000, "salary_currency": "PLN", "salary_period": "yearly"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_default_true_shows_undisclosed_salary_jobs(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(show_jobs_without_salary=True)
        job = _job(structured={})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_unset_key_defaults_to_showing_undisclosed_salary_jobs(self, mock_prefs, mock_update):
        # Preferences saved before this field existed, must not suddenly
        # start hiding jobs for an existing candidate.
        mock_prefs.return_value = _prefs(show_jobs_without_salary=None)
        job = _job(structured={})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_unverified_missing_salary_survives_with_other_extracted_fields(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(show_jobs_without_salary=False)
        job = _job(structured={"seniority": "senior"})  # no salary fields at all
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        assert stats["auto_rejected"] == 0
        mock_update.assert_not_called()


class TestCompanyTypeFilter:
    # Regression: questionnaire.html promises "Selecting anything here filters
    # out every other type" for the company-type chips, previously saved and
    # never read anywhere.

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_no_preference_never_checked(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(preferred_company_types=[])
        job = _job(structured={"company_type": "agency"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_rejects_confirmed_mismatch_via_company_type_axis(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(preferred_company_types=["startup"])
        job = _job(structured={"company_type": "enterprise"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == []
        assert stats["auto_rejected"] == 1
        assert "company type" in mock_update.call_args[0][2]

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_matching_company_type_survives(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(preferred_company_types=["startup"])
        job = _job(structured={"company_type": "startup"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_or_semantics_any_selected_type_matching_survives(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(preferred_company_types=["startup", "enterprise"])
        job = _job(structured={"company_type": "enterprise"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_unknown_company_type_skipped_not_rejected(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(preferred_company_types=["startup"])
        job = _job(structured={"company_type": "unknown"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_missing_structured_data_skipped_not_rejected(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(preferred_company_types=["startup"])
        job = _job(structured={})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_product_maps_to_product_vs_outsourcing_axis(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(preferred_company_types=["product"])
        job = _job(structured={"product_vs_outsourcing": "product"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_product_preference_rejects_confirmed_outsourcing_job(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(preferred_company_types=["product"])
        job = _job(structured={"product_vs_outsourcing": "outsourcing"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == []
        assert stats["auto_rejected"] == 1

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_product_preference_unknown_outsourcing_axis_skipped(self, mock_prefs, mock_update):
        # company_type known as "startup" (not directly relevant to the "product"
        # axis) but product_vs_outsourcing is unstated, can't confirm a mismatch.
        mock_prefs.return_value = _prefs(preferred_company_types=["product"])
        job = _job(structured={"company_type": "startup"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_consulting_matches_agency_company_type(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(preferred_company_types=["consulting"])
        job = _job(structured={"company_type": "agency"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_consulting_matches_outsourcing_axis(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(preferred_company_types=["consulting"])
        job = _job(structured={"product_vs_outsourcing": "outsourcing"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_product_preference_mixed_outsourcing_treated_as_inconclusive(self, mock_prefs, mock_update):
        # Regression: "mixed" is a genuine partial match, not evidence of a
        # mismatch, it used to resolve to a confirmed False (reject) the same
        # way a hard "outsourcing" value does, which is wrong.
        mock_prefs.return_value = _prefs(preferred_company_types=["product"])
        job = _job(structured={"product_vs_outsourcing": "mixed"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_consulting_preference_mixed_outsourcing_with_known_non_agency_type_inconclusive(self, mock_prefs, mock_update):
        # Regression: company_type confirmed non-agency ("startup") used to be
        # enough on its own to reject a "consulting" preference outright, even
        # though the other axis (product_vs_outsourcing="mixed") can't rule out
        # a partial consulting/outsourcing fit.
        mock_prefs.return_value = _prefs(preferred_company_types=["consulting"])
        job = _job(structured={"company_type": "startup", "product_vs_outsourcing": "mixed"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()


class TestWorkingLanguageMismatch:
    # Closes the hole collector/language_filter.py can't: that filter only detects
    # the POSTING TEXT's language, so an English-language posting at a company that
    # actually works in Polish sails through untouched.

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_rejects_polish_only_company_for_non_polish_speaker(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(languages=[{"language": "english", "level": "c1"}])
        job = _job(structured={"working_language": "polish"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == []
        assert stats["auto_rejected"] == 1
        assert "polish" in mock_update.call_args[0][2]

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_passes_polish_only_company_for_polish_speaker(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(languages=[{"language": "polish", "level": "native"}])
        job = _job(structured={"working_language": "polish"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_rejects_english_only_company_for_non_english_speaker(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(languages=[{"language": "polish", "level": "native"}])
        job = _job(structured={"working_language": "english"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == []
        assert stats["auto_rejected"] == 1

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_below_working_level_does_not_count_as_covering_the_language(self, mock_prefs, mock_update):
        # B2 is the minimum "usable for work" threshold, same as
        # collector/language_filter.py, A1 Polish shouldn't count, even though
        # English (present, at working level) doesn't help for a Polish-only job.
        mock_prefs.return_value = _prefs(languages=[
            {"language": "english", "level": "c1"}, {"language": "polish", "level": "a1"},
        ])
        job = _job(structured={"working_language": "polish"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == []
        assert stats["auto_rejected"] == 1

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_both_never_rejects(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(languages=[{"language": "english", "level": "c1"}])
        job = _job(structured={"working_language": "both"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_unknown_never_rejects(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(languages=[{"language": "english", "level": "c1"}])
        job = _job(structured={"working_language": "unknown"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_missing_working_language_field_never_rejects(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(languages=[{"language": "english", "level": "c1"}])
        job = _job(structured={})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_no_candidate_languages_configured_never_rejects(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(languages=[])
        job = _job(structured={"working_language": "polish"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == [job]
        mock_update.assert_not_called()

    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_check_runs_even_with_no_other_preference(self, mock_prefs, mock_update):
        # Regression: the early-exit skip must also consider languages on its
        # own, not just salary_min/work_mode/seniority/company_type.
        mock_prefs.return_value = _prefs(languages=[{"language": "english", "level": "c1"}])
        job = _job(structured={"working_language": "polish"})
        surviving, stats = apply_dealbreaker_filter([job])
        assert surviving == []
        assert stats["auto_rejected"] == 1


class TestMultipleJobs:
    @patch("evaluator.dealbreakers.job_repository.update_score_and_status")
    @patch("evaluator.dealbreakers.candidate_preferences_repository.get_active")
    def test_mixed_batch_filters_only_violators(self, mock_prefs, mock_update):
        mock_prefs.return_value = _prefs(salary_min=150000, salary_currency="PLN")
        good = _job("good", structured={"salary_max": 200000, "salary_currency": "PLN", "salary_period": "yearly"})
        bad = _job("bad", structured={"salary_max": 50000, "salary_currency": "PLN", "salary_period": "yearly"})
        surviving, stats = apply_dealbreaker_filter([good, bad])
        assert surviving == [good]
        assert stats == {"checked": 2, "auto_rejected": 1}


class TestAnnualize:
    def test_yearly_returned_unchanged(self):
        from evaluator.dealbreakers import _annualize
        assert _annualize(200000, "yearly") == 200000

    def test_monthly_multiplied_by_12(self):
        from evaluator.dealbreakers import _annualize
        assert _annualize(20000, "monthly") == 240000

    def test_hourly_multiplied_by_2016(self):
        from evaluator.dealbreakers import _annualize
        assert _annualize(100, "hourly") == 201600

    def test_unknown_period_returns_none(self):
        from evaluator.dealbreakers import _annualize
        assert _annualize(100, None) is None
        assert _annualize(100, "fortnightly") is None
