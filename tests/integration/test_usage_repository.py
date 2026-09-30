import time
from unittest.mock import MagicMock

from db.repositories import job_repository, usage_repository


def _insert_job(url="https://example.com/1"):
    return job_repository.insert(
        title="Backend Dev", company="AcmeCo", location="Remote",
        url=url, source="linkedin", description="PHP role.",
    )


class TestLogUsage:
    def test_log_usage_computes_cost_from_model_rates(self):
        usage_repository.log_usage("claude-sonnet-4-6", "scorer", 1_000_000, 1_000_000)
        summary = usage_repository.get_summary()
        # sonnet: $3 in + $15 out per 1M tokens -> $18 for 1M+1M tokens
        assert summary["total_cost_usd"] == 18.0

    def test_unknown_model_costs_zero(self):
        usage_repository.log_usage("some-unknown-model", "scorer", 1000, 1000)
        summary = usage_repository.get_summary()
        assert summary["total_cost_usd"] == 0.0

    def test_cache_tokens_priced_off_the_model_s_input_rate(self):
        # sonnet input rate $3/1M; cache write 1.25x, cache read 0.1x (config.py).
        usage_repository.log_usage(
            "claude-sonnet-4-6", "scorer", 0, 0,
            cache_creation_tokens=1_000_000, cache_read_tokens=1_000_000,
        )
        summary = usage_repository.get_summary()
        assert summary["total_cost_usd"] == 3.0 * 1.25 + 3.0 * 0.1

    def test_cache_tokens_folded_into_reported_input_tokens(self):
        usage_repository.log_usage(
            "claude-sonnet-4-6", "scorer", 100, 0,
            cache_creation_tokens=10, cache_read_tokens=20,
        )
        summary = usage_repository.get_summary()
        assert summary["total_tokens"] == 130

    def test_log_anthropic_bills_prompt_cache_tokens(self):
        # This is the exact reported bug: evaluator/scorer.py caches its system prompt,
        # but response.usage.input_tokens alone excludes cache_creation/cache_read
        # tokens, so those calls used to silently cost $0 in the tracked total.
        response = MagicMock(usage=MagicMock(
            input_tokens=0, output_tokens=0,
            cache_creation_input_tokens=1_000_000, cache_read_input_tokens=0,
        ))
        usage_repository.log_anthropic(response, "scorer", "claude-sonnet-4-6")
        summary = usage_repository.get_summary()
        assert summary["total_cost_usd"] == 3.0 * 1.25


class TestGetSummary:
    def test_empty_db_returns_zeros(self):
        summary = usage_repository.get_summary()
        assert summary["today_cost_usd"] == 0
        assert summary["total_cost_usd"] == 0
        assert summary["cost_per_100_usd"] is None

    def test_today_and_total_both_reflect_logged_usage(self):
        usage_repository.log_usage("claude-haiku-4-5-20251001", "extractor", 1_000_000, 0)
        summary = usage_repository.get_summary()
        assert summary["today_cost_usd"] == 1.0  # $1/1M input tokens
        assert summary["total_cost_usd"] == 1.0


class TestRecordRunSummary:
    def test_creates_breakdown_per_model(self):
        started_at = usage_repository.now_iso()
        usage_repository.log_usage("claude-sonnet-4-6", "scorer", 1000, 500)
        usage_repository.log_usage("claude-haiku-4-5-20251001", "extractor", 2000, 0)
        usage_repository.record_run_summary("run_agent", started_at)

        row = usage_repository.get_history()[0]
        breakdown = row["breakdown"]
        assert "claude-sonnet-4-6" in breakdown
        assert "claude-haiku-4-5-20251001" in breakdown
        assert breakdown["claude-sonnet-4-6"]["input_tokens"] == 1000

    def test_jobs_evaluated_counts_only_scorer_module(self):
        started_at = usage_repository.now_iso()
        usage_repository.log_usage("claude-sonnet-4-6", "scorer", 100, 50)
        usage_repository.log_usage("claude-sonnet-4-6", "scorer", 100, 50)
        usage_repository.log_usage("claude-haiku-4-5-20251001", "extractor", 200, 0)
        usage_repository.record_run_summary("run_agent", started_at)

        assert usage_repository.get_history()[0]["jobs_evaluated"] == 2

    def test_cost_per_100_computed_for_the_run(self):
        started_at = usage_repository.now_iso()
        for _ in range(10):
            usage_repository.log_usage("claude-sonnet-4-6", "scorer", 1_000_000, 0)  # $3 each
        usage_repository.record_run_summary("run_agent", started_at)

        # 10 jobs cost $30 total -> per 100 jobs = $300
        assert usage_repository.get_history()[0]["cost_per_100_usd"] == 300.0

    def test_zero_jobs_evaluated_leaves_cost_per_100_null(self):
        started_at = usage_repository.now_iso()
        usage_repository.log_usage("voyage-3-large", "embed", 1000, 0)
        usage_repository.record_run_summary("rank", started_at)

        row = usage_repository.get_history()[0]
        assert row["jobs_evaluated"] == 0
        assert row["cost_per_100_usd"] is None

    def test_no_usage_since_start_records_nothing(self):
        started_at = usage_repository.now_iso()
        usage_repository.record_run_summary("run_agent", started_at)
        assert usage_repository.get_history() == []

    def test_usage_logged_before_started_at_is_excluded(self):
        usage_repository.log_usage("claude-sonnet-4-6", "scorer", 1_000_000, 0)  # before the run
        time.sleep(1.1)  # usage_log's created_at has second resolution

        started_at = usage_repository.now_iso()
        usage_repository.log_usage("claude-sonnet-4-6", "scorer", 500_000, 0)  # during the run
        usage_repository.record_run_summary("run_agent", started_at)

        row = usage_repository.get_history()[0]
        assert row["jobs_evaluated"] == 1
        assert row["total_cost_usd"] == 1.5  # only the $1.5 (500k tokens @ $3/1M) call counted


class TestGetCostPer100:
    def test_no_summaries_returns_none(self):
        assert usage_repository.get_cost_per_100() is None

    def test_averages_across_multiple_runs(self):
        started_at = usage_repository.now_iso()
        for _ in range(5):
            usage_repository.log_usage("claude-sonnet-4-6", "scorer", 1_000_000, 0)  # $3 each, 5 jobs = $15
        usage_repository.record_run_summary("run_agent", started_at)

        time.sleep(1.1)
        started_at2 = usage_repository.now_iso()
        for _ in range(5):
            usage_repository.log_usage("claude-sonnet-4-6", "scorer", 1_000_000, 0)  # another $15, 5 jobs
        usage_repository.record_run_summary("run_agent", started_at2)

        # 10 jobs total, $30 total -> $300 per 100
        assert usage_repository.get_cost_per_100() == 300.0

    def test_ignores_runs_with_zero_jobs_evaluated(self):
        started_at = usage_repository.now_iso()
        usage_repository.log_usage("claude-sonnet-4-6", "scorer", 1_000_000, 0)
        usage_repository.record_run_summary("run_agent", started_at)  # 1 job, $3

        time.sleep(1.1)  # keep run 2's window strictly after run 1's
        started_at2 = usage_repository.now_iso()
        usage_repository.log_usage("voyage-3-large", "embed", 1_000_000, 0)  # rank-only run, no scoring
        usage_repository.record_run_summary("rank", started_at2)

        # All pipeline cost counts against the one scored job: $3 + $0.18 -> $318 per 100
        assert usage_repository.get_cost_per_100() == 318.0

    def test_deleting_jobs_does_not_change_cost_per_100(self):
        # This is the exact reported bug: cost-per-100 used to be computed as
        # total_cost_usd / COUNT(*) FROM jobs, so deleting jobs (a normal cleanup
        # action, unrelated to cost) inflated the ratio because the denominator
        # shrank while the numerator (all-time spend) didn't.
        job_a = _insert_job("https://example.com/a")
        job_b = _insert_job("https://example.com/b")

        started_at = usage_repository.now_iso()
        for _ in range(2):
            usage_repository.log_usage("claude-sonnet-4-6", "scorer", 1_000_000, 0)
        usage_repository.record_run_summary("run_agent", started_at)

        before = usage_repository.get_cost_per_100()
        assert before == 300.0  # 2 jobs, $6 total -> $300/100

        job_repository.delete_by_filter(["new"])
        assert job_repository.search(status="all") == []

        after = usage_repository.get_cost_per_100()
        assert after == before == 300.0
