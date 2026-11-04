from unittest.mock import patch

import scripts.run_all as run_all


@patch("scripts.run_all.collect")
@patch("db.repositories.job_repository.get_new_needing_extraction", return_value=[])
@patch("db.repositories.job_repository.get_unscored", return_value=[])
@patch("scripts.run_all.evaluate")
@patch("scripts.run_all.run_extraction", return_value=0)
@patch("scripts.run_all._run_script", return_value=0)
@patch("scripts.run_all.usage_repository.record_run_summary")
def test_main_records_a_run_summary_on_success(
    mock_record, mock_run_script, mock_run_extraction, mock_evaluate,
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
def test_main_uses_stage_specific_job_queries(
    mock_record, mock_run_script, mock_run_extraction, mock_evaluate,
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
def test_main_records_a_run_summary_even_when_collector_fails(mock_record, mock_collect):
    # A run that fails partway through (the collector, here) can still have
    # billed real API calls up to that point, the cost snapshot must not be
    # skipped just because the run overall failed.
    mock_collect.side_effect = Exception("network error")

    with patch("sys.argv", ["run_all.py"]):
        exit_code = run_all.main()

    assert exit_code == 1
    mock_record.assert_called_once()
    assert mock_record.call_args[0][0] == "run_all"
