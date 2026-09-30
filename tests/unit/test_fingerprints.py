from evaluator.fingerprints import ranking_fingerprint, score_fingerprint


def test_score_fingerprint_changes_with_description():
    first = score_fingerprint({"id": "1", "description": "Python"}, "prompt")
    second = score_fingerprint({"id": "1", "description": "Go"}, "prompt")
    assert first != second


def test_score_fingerprint_changes_with_prompt():
    job = {"id": "1", "description": "Python"}
    assert score_fingerprint(job, "one") != score_fingerprint(job, "two")


def test_ranking_fingerprint_is_order_independent_but_input_sensitive():
    jobs = [{"id": "b", "description": "Go"}, {"id": "a", "description": "Python"}]
    first = ranking_fingerprint(jobs, "profile", "questionnaire", [])
    assert first == ranking_fingerprint(list(reversed(jobs)), "profile", "questionnaire", [])
    assert first != ranking_fingerprint(jobs, "changed", "questionnaire", [])
