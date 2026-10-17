import json
import uuid
import pytest
from unittest.mock import MagicMock, patch

import api_client
from db.repositories import job_repository
from embeddings.indexer import (
    _embed_with_retry, _job_to_text, _max_scores, index_jobs, score_by_similarity, score_pool_by_similarity,
)


def _insert_job(status: str = "new") -> str:
    job_id = job_repository.insert(
        title="Dev", company="Corp", location="Remote",
        url=f"https://a.com/{uuid.uuid4().hex}", source="linkedin", description="desc",
    )
    if status != "new":
        job_repository.update_status(job_id, status)
    return job_id


def _insert_embedding(job_id: str, vec: list[float]) -> None:
    api_client.post("/api/embeddings", json={
        "items": [{"job_id": job_id, "embedding": vec, "model": "voyage-3-large"}],
    })


class TestJobToText:
    def test_includes_title_and_company(self):
        job = {"title": "Senior Dev", "company": "Acme", "location": None, "description": None}
        text = _job_to_text(job)
        assert "Senior Dev at Acme" in text

    def test_includes_location_when_present(self):
        job = {"title": "Dev", "company": "Co", "location": "Warsaw", "description": None}
        assert "Warsaw" in _job_to_text(job)

    def test_omits_location_when_absent(self):
        job = {"title": "Dev", "company": "Co", "location": None, "description": None}
        assert "Location:" not in _job_to_text(job)

    def test_includes_description(self):
        job = {"title": "Dev", "company": "Co", "location": None, "description": "Python expert"}
        assert "Python expert" in _job_to_text(job)

    def test_truncates_description_at_2000_chars(self):
        long_desc = "x" * 3000
        job = {"title": "Dev", "company": "Co", "location": None, "description": long_desc}
        text = _job_to_text(job)
        assert "x" * 2000 in text
        assert "x" * 2001 not in text

    def test_strips_linkedin_junk_before_embedding(self):
        # Regression guard: _job_to_text used to slice job["description"] raw, so
        # LinkedIn's page-chrome junk (footer links, "people also viewed") could land
        # inside the embedded text for short postings. It must go through
        # build_excerpt() so junk is stripped first, same as every other consumer.
        job = {
            "title": "Dev", "company": "Co", "location": None, "source": "linkedin",
            "description": "Real job content here.\nSet alert for similar jobs\nUnrelated junk.",
        }
        text = _job_to_text(job)
        assert "Real job content here." in text
        assert "Unrelated junk" not in text

    def test_non_linkedin_source_passes_description_through_unchanged(self):
        job = {"title": "Dev", "company": "Co", "location": None, "source": "remotive", "description": "Clean text"}
        assert "Clean text" in _job_to_text(job)


class TestMaxScores:
    def test_empty_vectors_returns_empty_dict(self):
        j1 = _insert_job()
        _insert_embedding(j1, [1.0, 0.0])
        assert _max_scores([j1], []) == {}

    def test_single_vector_matches_score_by_similarity(self):
        j1 = _insert_job()
        _insert_embedding(j1, [1.0, 0.0])
        assert _max_scores([j1], [[1.0, 0.0]]) == score_by_similarity([j1], [1.0, 0.0])

    def test_takes_max_across_multiple_vectors(self):
        j1 = _insert_job()
        _insert_embedding(j1, [1.0, 0.0])  # perfectly aligned with the 2nd vector below, orthogonal to the 1st
        result = _max_scores([j1], [[0.0, 1.0], [1.0, 0.0]])
        assert abs(result[j1] - 1.0) < 1e-9


class TestScorePoolBySimilarity:
    def test_empty_job_ids_returns_empty_and_no_basis(self):
        assert score_pool_by_similarity([]) == ({}, None)

    def test_no_applied_jobs_no_profile_returns_empty_and_no_basis(self):
        j1 = _insert_job()
        assert score_pool_by_similarity([j1], candidate_profile=None) == ({}, None)
        assert score_pool_by_similarity([j1], candidate_profile="") == ({}, None)

    @patch("embeddings.indexer._get_client")
    def test_falls_back_to_embedding_candidate_profile_when_no_applied_jobs(self, mock_get_client):
        j1 = _insert_job()
        _insert_embedding(j1, [0.7, 0.7])
        mock_client = MagicMock()
        mock_client.embed.return_value = [[0.7, 0.7]]
        mock_get_client.return_value = mock_client

        scores, basis = score_pool_by_similarity([j1], candidate_profile="CANDIDATE:\n- Senior PHP Developer")

        assert basis == "CV profile / questionnaire (no applied jobs yet)"
        assert abs(scores[j1] - 1.0) < 1e-9
        mock_client.embed.assert_called_once_with(["CANDIDATE:\n- Senior PHP Developer"], input_type="query")

    def test_applied_history_takes_priority_over_profile_fallback(self):
        ja = _insert_job(status="applied")
        _insert_embedding(ja, [1.0, 0.0])
        jx = _insert_job()
        _insert_embedding(jx, [1.0, 0.0])

        scores, basis = score_pool_by_similarity([jx], candidate_profile="CANDIDATE:\n- Senior PHP Developer")

        assert basis == "applied jobs (max-sim)"
        assert abs(scores[jx] - 1.0) < 1e-9

    def test_scores_by_max_similarity_to_any_applied_vector(self):
        ja1 = _insert_job(status="applied")
        _insert_embedding(ja1, [1.0, 0.0])
        ja2 = _insert_job(status="applied")
        _insert_embedding(ja2, [0.0, 1.0])
        jx = _insert_job()
        _insert_embedding(jx, [1.0, 0.0])  # identical to ja1, orthogonal to ja2

        scores, basis = score_pool_by_similarity([jx])
        assert abs(scores[jx] - 1.0) < 1e-9

    def test_max_sim_beats_the_old_centroid_approach_on_multi_modal_history(self):
        # Regression for the audit's exact finding: two orthogonal applied clusters
        # (e.g. backend Python roles vs data-engineering roles) used to be averaged
        # into a single centroid sitting in semantic no-man's-land, close to
        # neither. A candidate job identical to ONE of the two applied jobs should
        # score a perfect 1.0 under max-sim, strictly higher than the ~0.707
        # cosine similarity it would have gotten against the old centroid.
        ja1 = _insert_job(status="applied")
        _insert_embedding(ja1, [1.0, 0.0])
        ja2 = _insert_job(status="applied")
        _insert_embedding(ja2, [0.0, 1.0])
        jx = _insert_job()
        _insert_embedding(jx, [1.0, 0.0])

        scores, _ = score_pool_by_similarity([jx])
        old_centroid_similarity = 1.0 / (2 ** 0.5)  # cosine([1,0], normalize([1,1])) = 1/sqrt(2)
        assert scores[jx] > old_centroid_similarity + 0.1

    def test_penalizes_similarity_to_rejected_vectors(self):
        ja = _insert_job(status="applied")
        _insert_embedding(ja, [1.0, 0.0])
        jr = _insert_job(status="rejected")
        _insert_embedding(jr, [0.0, 1.0])
        jx = _insert_job()
        _insert_embedding(jx, [0.0, 1.0])  # matches applied on neither axis, matches rejected exactly

        scores, _ = score_pool_by_similarity([jx])
        # sim to applied ([1,0] vs [0,1]) = 0.0; sim to rejected ([0,1] vs [0,1]) = 1.0
        # score = 0.0 - 0.3 * 1.0 = -0.3
        assert abs(scores[jx] - (-0.3)) < 1e-9

    def test_no_rejected_vectors_skips_penalty(self):
        ja = _insert_job(status="applied")
        _insert_embedding(ja, [1.0, 0.0])
        jx = _insert_job()
        _insert_embedding(jx, [1.0, 0.0])

        scores, _ = score_pool_by_similarity([jx])
        assert abs(scores[jx] - 1.0) < 1e-9

    def test_job_without_embedding_absent_from_result_not_a_crash(self):
        ja = _insert_job(status="applied")
        _insert_embedding(ja, [1.0, 0.0])
        jx = _insert_job()  # no embedding

        scores, basis = score_pool_by_similarity([jx])
        assert basis == "applied jobs (max-sim)"
        assert jx not in scores


class TestEmbedWithRetry:
    def test_returns_on_first_success(self):
        mock_client = MagicMock()
        mock_client.embed.return_value = [[0.1, 0.2]]
        result = _embed_with_retry(mock_client, ["text"])
        assert result == [[0.1, 0.2]]
        assert mock_client.embed.call_count == 1

    @patch("ranker.retry.time.sleep")
    def test_retries_on_rate_limit_error(self, mock_sleep):
        from voyageai.error import RateLimitError
        mock_client = MagicMock()
        mock_client.embed.side_effect = [
            RateLimitError("rate limit exceeded"),
            [[0.5, 0.6]],
        ]
        result = _embed_with_retry(mock_client, ["text"])
        assert result == [[0.5, 0.6]]
        assert mock_client.embed.call_count == 2
        mock_sleep.assert_called_once()

    @patch("ranker.retry.time.sleep")
    def test_retries_on_service_unavailable_error(self, mock_sleep):
        from voyageai.error import ServiceUnavailableError
        mock_client = MagicMock()
        mock_client.embed.side_effect = [
            ServiceUnavailableError("HTTP 503 service unavailable"),
            [[0.3]],
        ]
        result = _embed_with_retry(mock_client, ["text"])
        assert result == [[0.3]]

    def test_raises_immediately_on_non_rate_limit_error(self):
        mock_client = MagicMock()
        mock_client.embed.side_effect = ValueError("invalid input")
        with pytest.raises(ValueError, match="invalid input"):
            _embed_with_retry(mock_client, ["text"])
        assert mock_client.embed.call_count == 1


class TestIndexJobs:
    def test_returns_zero_for_empty_list(self):
        assert index_jobs([]) == 0

    @patch("embeddings.indexer.time.sleep")
    @patch("embeddings.indexer._get_client")
    def test_indexes_jobs_and_returns_count(self, mock_get_client, mock_sleep):
        mock_client = MagicMock()
        mock_client.embed.return_value = [[0.1, 0.2], [0.3, 0.4]]
        mock_get_client.return_value = mock_client

        j1 = _insert_job()
        j2 = _insert_job()
        jobs = [
            {"id": j1, "title": "Dev A", "company": "Corp", "location": None, "description": "Python"},
            {"id": j2, "title": "Dev B", "company": "Corp", "location": None, "description": "Java"},
        ]

        count = index_jobs(jobs)
        assert count == 2

    @patch("embeddings.indexer.time.sleep")
    @patch("embeddings.indexer._get_client")
    def test_stores_embeddings_in_db(self, mock_get_client, mock_sleep):
        mock_client = MagicMock()
        mock_client.embed.return_value = [[0.9, 0.1]]
        mock_get_client.return_value = mock_client

        j1 = _insert_job()
        jobs = [{"id": j1, "title": "Dev", "company": "Corp", "location": None, "description": "desc"}]
        index_jobs(jobs)

        vectors = api_client.post("/api/embeddings/vectors", json={"job_ids": [j1]}).json()
        assert vectors[j1] == [0.9, 0.1]


class TestScoreBySimilarity:
    def test_empty_job_ids_returns_empty_dict(self):
        assert score_by_similarity([], [1.0, 0.0]) == {}

    def test_empty_ideal_returns_empty_dict(self):
        assert score_by_similarity(["j1"], []) == {}

    def test_computes_cosine_similarity(self):
        # Computed server-side now (JobAgentWeb's /api/embeddings/similarity) rather
        # than fetched-then-scored locally, no VoyageClient involved at all here.
        j1 = _insert_job()
        _insert_embedding(j1, [1.0, 0.0])

        scores = score_by_similarity([j1], [1.0, 0.0])

        assert j1 in scores
        assert abs(scores[j1] - 1.0) < 1e-9

    def test_missing_embedding_not_in_result(self):
        j1 = _insert_job()  # no embedding
        scores = score_by_similarity([j1], [1.0, 0.0])
        assert scores == {}
