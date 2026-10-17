from pathlib import Path

import evaluator.fingerprints as fingerprints
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


def test_ranking_fingerprint_changes_when_newly_covered_implementation_changes(tmp_path, monkeypatch):
    paths = [
        "ranker/listwise.py", "ranker/debate.py", "ranker/reranker.py", "ranker/fusion.py",
        "evaluator/profile.py", "collector/utils.py", "embeddings/indexer.py", "scripts/rank_jobs.py",
        "ranker/rank_cache.py",
    ]
    for path in paths:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {path}\n")
    monkeypatch.setattr(fingerprints, "_ROOT", Path(tmp_path))

    first = ranking_fingerprint([], "profile", "questionnaire", [])
    (tmp_path / "ranker/rank_cache.py").write_text("CACHE_VERSION = 2\n")

    assert ranking_fingerprint([], "profile", "questionnaire", []) != first
