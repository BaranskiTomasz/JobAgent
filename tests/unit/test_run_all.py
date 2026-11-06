import logging
from unittest.mock import patch

import pytest

import scripts.run_all as run_all


@patch("scripts.run_all.collect")
@patch("db.repositories.job_repository.get_new_needing_extraction", return_value=[])
@patch("db.repositories.job_repository.get_unscored", return_value=[])
@patch("scripts.run_all.evaluate")
@patch("scripts.run_all.run_extraction", return_value=0)
@patch("scripts.run_all._run_script", return_value=0)
@patch("scripts.run_all.usage_repository.record_run_summary")
@patch("scripts.run_all._nudge_catalog_collection")
def test_main_records_a_run_summary_on_success(
    mock_nudge, mock_record, mock_run_script, mock_run_extraction, mock_evaluate,
    mock_get_unscored, mock_get_needing_extraction, mock_collect,
):
    # Regression: run_all.py never went through web/routes/runner.py's
    # started_at -> record_run_summary envelope, every run's cost (collector,
    # distill, extractor, evaluator, prune, ranking) was silently missing from
    # cost_summaries entirely.
    mock_collect.return_value = {"jobs_found": 0, "jobs_new": 0}
    mock_evaluate.return_value = {"jobs_scored": 0}

    with patch("sys.argv", ["run_all.py"]):
        exit_code = run_all.main()

    assert exit_code == 0
    mock_record.assert_called_once()
    assert mock_record.call_args[0][0] == "run_all"


@patch("scripts.run_all.collect")
@patch("db.repositories.job_repository.get_new_needing_extraction")
@patch("db.repositories.job_repository.get_unscored")
@patch("scripts.run_all.evaluate")
@patch("scripts.run_all.run_extraction", return_value=1)
@patch("scripts.run_all._run_script", return_value=0)
@patch("scripts.run_all.usage_repository.record_run_summary")
@patch("scripts.run_all._nudge_catalog_collection")
def test_main_uses_stage_specific_job_queries(
    mock_nudge, mock_record, mock_run_script, mock_run_extraction, mock_evaluate,
    mock_get_unscored, mock_get_needing_extraction, mock_collect,
):
    collected_id = "collected-job"
    catalog_id = "catalog-job"
    extraction_jobs = [
        {"id": collected_id, "title": "Extraction 1"},
        {"id": catalog_id, "title": "Extraction 2"},
    ]
    evaluation_jobs = [
        {"id": collected_id, "title": "Evaluation 1"},
        {"id": catalog_id, "title": "Evaluation 2"},
    ]
    mock_collect.return_value = {
        "jobs_found": 1,
        "jobs_new": 1,
        "job_ids": [collected_id],
    }
    mock_get_needing_extraction.return_value = extraction_jobs
    mock_get_unscored.return_value = evaluation_jobs
    mock_evaluate.return_value = {"jobs_scored": 2}

    with patch("sys.argv", ["run_all.py", "--days", "1"]):
        exit_code = run_all.main()

    assert exit_code == 0
    mock_get_needing_extraction.assert_called_once_with()
    mock_run_extraction.assert_called_once_with(extraction_jobs)
    mock_get_unscored.assert_called_once_with()
    mock_evaluate.assert_called_once_with(jobs=evaluation_jobs)


@patch("scripts.run_all.collect")
@patch("scripts.run_all.usage_repository.record_run_summary")
@patch("scripts.run_all._nudge_catalog_collection")
def test_main_records_a_run_summary_even_when_collector_fails(mock_nudge, mock_record, mock_collect):
    # A run that fails partway through (the collector, here) can still have
    # billed real API calls up to that point, the cost snapshot must not be
    # skipped just because the run overall failed.
    mock_collect.side_effect = Exception("network error")

    with patch("sys.argv", ["run_all.py"]):
        exit_code = run_all.main()

    assert exit_code == 1
    mock_record.assert_called_once()
    assert mock_record.call_args[0][0] == "run_all"


class _FakeClock:
    """Deterministic stand-in for the `time` module as run_all's catalog-nudge
    loop uses it: sleep() records the requested duration (and refuses a
    non-positive one) and advances the clock by it, monotonic() reads it."""

    def __init__(self, start: float = 0.0):
        self.now = start
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds > 0, "the polling loop must never sleep a non-positive amount"
        self.sleeps.append(seconds)
        self.now += seconds


class TestCatalogNudgeStage:
    @pytest.fixture
    def nudge_mocks(self, monkeypatch):
        """Runs _nudge_catalog_collection() against a fake clock: no real sleeping,
        no real HTTP. Yields (clock, nudge_mock, running_mock)."""
        clock = _FakeClock()
        monkeypatch.setattr(run_all, "time", clock)
        with patch("scripts.run_all.nudge_catalog_collection") as nudge, \
                patch("scripts.run_all.catalog_collection_running") as running:
            running.return_value = False
            yield clock, nudge, running

    def test_first_poll_is_not_trusted_until_a_grace_sleep_happened(self, nudge_mocks, caplog):
        clock, nudge, running = nudge_mocks
        check_times: list[float] = []

        def fake_running() -> bool:
            check_times.append(clock.now)
            # First-poll race simulation: at t<7 the server has only just
            # scheduled the run and its running flag is not set yet (reads
            # False); from t=7 the run is visible, and it "finishes" at t>=21.
            return 7 <= clock.now < 21

        running.side_effect = fake_running
        with caplog.at_level(logging.INFO, logger="scripts.run_all"):
            run_all._nudge_catalog_collection()

        nudge.assert_called_once_with()
        # The very first status check must happen only after a full grace
        # sleep - the pre-fix code checked at t=0, read the not-yet-set flag
        # as False, and exited immediately believing collection was done.
        assert clock.sleeps[0] == float(run_all.CATALOG_NUDGE_POLL_INTERVAL_SECONDS)
        assert check_times[0] >= run_all.CATALOG_NUDGE_POLL_INTERVAL_SECONDS
        # t=7 and t=14 read True (still running), t=21 reads False -> info exit.
        assert check_times == [7.0, 14.0, 21.0]
        assert any(r.levelno == logging.INFO and "finished waiting" in r.message for r in caplog.records)
        assert not any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_never_running_exits_after_one_grace_poll_with_info(self, nudge_mocks, caplog):
        clock, nudge, running = nudge_mocks  # running always False
        with caplog.at_level(logging.INFO, logger="scripts.run_all"):
            run_all._nudge_catalog_collection()

        # "Actually not running, never was" is still recognized promptly: one
        # grace sleep, one status check, info-level exit (not a warning).
        assert clock.sleeps == [float(run_all.CATALOG_NUDGE_POLL_INTERVAL_SECONDS)]
        assert running.call_count == 1
        assert clock.now == float(run_all.CATALOG_NUDGE_POLL_INTERVAL_SECONDS)
        assert any(r.levelno == logging.INFO and "finished waiting" in r.message for r in caplog.records)
        assert not any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_running_then_finished_logs_info_not_warning(self, nudge_mocks, caplog):
        clock, nudge, running = nudge_mocks
        running.side_effect = [True, True, False]
        with caplog.at_level(logging.INFO, logger="scripts.run_all"):
            run_all._nudge_catalog_collection()

        assert clock.sleeps == [7.0, 7.0, 7.0]
        assert running.call_count == 3
        assert any(r.levelno == logging.INFO and "finished waiting" in r.message for r in caplog.records)
        assert not any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_deadline_expiry_logs_a_warning(self, nudge_mocks, caplog):
        clock, nudge, running = nudge_mocks
        running.return_value = True  # collection never finishes within the budget
        with caplog.at_level(logging.INFO, logger="scripts.run_all"):
            run_all._nudge_catalog_collection()

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("did not finish" in r.message and "5-minute" in r.message for r in warnings), caplog.text
        # The success-path info log must NOT be used for the deadline exit.
        assert not any(r.levelno == logging.INFO and "finished waiting" in r.message for r in caplog.records)

    def test_sleeps_are_clamped_to_the_remaining_budget(self, nudge_mocks):
        clock, nudge, running = nudge_mocks
        running.return_value = True  # run until the deadline
        run_all._nudge_catalog_collection()

        budget = run_all.CATALOG_NUDGE_WAIT_BUDGET_SECONDS
        interval = run_all.CATALOG_NUDGE_POLL_INTERVAL_SECONDS
        # _FakeClock.sleep already asserts no non-positive sleep; here: no sleep
        # ever pushes the clock past the 300s deadline (a poll at t=294 sleeps
        # only the remaining 6s, not a full 7s to t=301).
        assert all(s > 0 for s in clock.sleeps)
        assert clock.now <= budget
        assert sum(clock.sleeps) <= budget
        full_polls, remainder = divmod(budget, interval)
        assert clock.sleeps == [float(interval)] * full_polls + [float(remainder)]
        assert clock.now == float(budget)
        # The final sleep lands exactly on the deadline, which is rechecked
        # before polling again -- no status request is made once the budget
        # is already exhausted (that extra request's own timeout could
        # otherwise push elapsed time past 300s).
        assert running.call_count == full_polls

    def test_nudge_request_failure_is_non_fatal(self, nudge_mocks, caplog):
        clock, nudge, running = nudge_mocks
        nudge.side_effect = Exception("connection refused")
        with caplog.at_level(logging.INFO, logger="scripts.run_all"):
            run_all._nudge_catalog_collection()  # must not raise

        running.assert_not_called()
        assert clock.sleeps == []
        assert any(r.levelno == logging.WARNING and "non-fatal" in r.message for r in caplog.records)

    def test_status_check_failure_is_non_fatal(self, nudge_mocks, caplog):
        clock, nudge, running = nudge_mocks
        running.side_effect = Exception("timeout")
        with caplog.at_level(logging.INFO, logger="scripts.run_all"):
            run_all._nudge_catalog_collection()  # must not raise

        assert clock.sleeps == [7.0]
        assert any(r.levelno == logging.WARNING and "non-fatal" in r.message for r in caplog.records)

    @patch("scripts.run_all.collect")
    @patch("db.repositories.job_repository.get_new_needing_extraction", return_value=[])
    @patch("db.repositories.job_repository.get_unscored", return_value=[])
    @patch("scripts.run_all.evaluate")
    @patch("scripts.run_all.run_extraction", return_value=0)
    @patch("scripts.run_all._run_script", return_value=0)
    @patch("scripts.run_all.usage_repository.record_run_summary")
    def test_main_runs_the_catalog_nudge_stage(
        self, mock_record, mock_run_script, mock_run_extraction, mock_evaluate,
        mock_get_unscored, mock_get_needing_extraction, mock_collect, monkeypatch,
    ):
        mock_collect.return_value = {"jobs_found": 0, "jobs_new": 0}
        mock_evaluate.return_value = {"jobs_scored": 0}
        clock = _FakeClock()
        monkeypatch.setattr(run_all, "time", clock)
        with patch("scripts.run_all.nudge_catalog_collection") as nudge, \
                patch("scripts.run_all.catalog_collection_running", return_value=False):
            with patch("sys.argv", ["run_all.py", "--days", "1"]):
                exit_code = run_all.main()

        assert exit_code == 0
        nudge.assert_called_once_with()
        # One grace sleep, one status read (not running) -> the stage ends.
        assert clock.sleeps == [float(run_all.CATALOG_NUDGE_POLL_INTERVAL_SECONDS)]
