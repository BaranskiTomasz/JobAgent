from unittest.mock import MagicMock, patch

from voyageai.error import RateLimitError

from ranker.reranker import rerank_jobs, _MAX_QUERY_CHARS


def _job(id="j1", title="Developer", company="Corp", description="Python dev role"):
    return {"id": id, "title": title, "company": company, "description": description}


class TestRerankJobs:
    def test_empty_input_returns_empty_list(self):
        assert rerank_jobs([], "python") == []

    def test_returns_jobs_sorted_by_rerank_score(self):
        jobs = [_job("j1", "Dev A"), _job("j2", "Dev B"), _job("j3", "Dev C")]
        rerank_results = [
            {"index": 2, "score": 0.95},
            {"index": 0, "score": 0.80},
            {"index": 1, "score": 0.60},
        ]
        with patch("ranker.reranker._get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.rerank.return_value = rerank_results
            mock_get.return_value = mock_client

            result = rerank_jobs(jobs, "python developer", top_k=3)

        assert len(result) == 3
        assert result[0]["id"] == "j3"
        assert result[1]["id"] == "j1"
        assert result[2]["id"] == "j2"
        assert result[0]["rerank_score"] == 0.95

    def test_attaches_rerank_score_to_each_job(self):
        jobs = [_job("j1")]
        with patch("ranker.reranker._get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.rerank.return_value = [{"index": 0, "score": 0.77}]
            mock_get.return_value = mock_client

            result = rerank_jobs(jobs, "query")

        assert result[0]["rerank_score"] == 0.77

    def test_truncates_description_to_6000_chars(self):
        long_desc = "x" * 10000
        jobs = [_job("j1", description=long_desc)]
        captured = []

        def capture_rerank(query, documents, top_k):
            captured.extend(documents)
            return [{"index": 0, "score": 0.5}]

        with patch("ranker.reranker._get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.rerank.side_effect = capture_rerank
            mock_get.return_value = mock_client

            rerank_jobs(jobs, "query")

        assert len(captured) == 1
        assert "x" * 6001 not in captured[0]
        assert "x" * 6000 in captured[0]

    def test_truncates_query_to_max_query_chars(self):
        long_query = "x" * 3000
        jobs = [_job("j1")]
        captured = {}

        def capture_rerank(query, documents, top_k):
            captured["query"] = query
            return [{"index": 0, "score": 0.5}]

        with patch("ranker.reranker._get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.rerank.side_effect = capture_rerank
            mock_get.return_value = mock_client

            rerank_jobs(jobs, long_query)

        assert len(captured["query"]) == _MAX_QUERY_CHARS

    def test_does_not_truncate_a_realistic_hyde_length_query(self):
        # Regression test: evaluator.profile.build_hyde_query's synthetic job posting
        # (max_tokens=400, "under 200 words") runs up to ~1600 chars worst case, the
        # cap used to be 500, silently cutting HyDE's output right around the company
        # blurb and losing the stack/seniority/preferences that follow.
        hyde_length_query = "x" * 1600
        jobs = [_job("j1")]
        captured = {}

        def capture_rerank(query, documents, top_k):
            captured["query"] = query
            return [{"index": 0, "score": 0.5}]

        with patch("ranker.reranker._get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.rerank.side_effect = capture_rerank
            mock_get.return_value = mock_client

            rerank_jobs(jobs, hyde_length_query)

        assert captured["query"] == hyde_length_query

    def test_skips_cross_encoder_when_no_query_given(self):
        # A generic placeholder query would produce a rerank_score that looks
        # considered but isn't, the cross-encoder must not be called at all.
        jobs = [_job("j1"), _job("j2")]
        with patch("ranker.reranker._get_client") as mock_get:
            mock_client = MagicMock()
            mock_get.return_value = mock_client

            result = rerank_jobs(jobs, query=None)

        mock_client.rerank.assert_not_called()
        assert [r["id"] for r in result] == ["j1", "j2"]

    def test_skips_cross_encoder_when_query_is_blank(self):
        jobs = [_job("j1")]
        with patch("ranker.reranker._get_client") as mock_get:
            mock_client = MagicMock()
            mock_get.return_value = mock_client

            rerank_jobs(jobs, query="   ")

        mock_client.rerank.assert_not_called()

    def test_no_query_falls_back_to_embedding_score(self):
        jobs = [_job("j1")]
        jobs[0]["_embedding_score"] = 0.33

        result = rerank_jobs(jobs, query=None)

        assert result[0]["rerank_score"] == 0.33

    def test_no_query_and_no_embedding_score_defaults_to_zero(self):
        jobs = [_job("j1")]

        result = rerank_jobs(jobs, query=None)

        assert result[0]["rerank_score"] == 0.0

    def test_no_query_respects_top_k(self):
        jobs = [_job(f"j{i}") for i in range(5)]

        result = rerank_jobs(jobs, query=None, top_k=2)

        assert len(result) == 2

    def test_no_query_fallback_marks_jobs_rerank_unreliable(self):
        # scripts/rank_jobs.py re-fuses rerank_score as an RRF leg after this
        # call, it must be able to tell "no real cross-encoder signal" apart
        # from a genuine low score, or a fallback value (== embedding score)
        # would double-count the embedding leg in that fusion.
        jobs = [_job("j1")]
        jobs[0]["_embedding_score"] = 0.5

        result = rerank_jobs(jobs, query=None)

        assert result[0]["_rerank_unreliable"] is True

    def test_api_error_fallback_marks_jobs_rerank_unreliable(self):
        jobs = [_job("j1")]
        jobs[0]["_embedding_score"] = 0.5

        with patch("ranker.reranker._get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.rerank.side_effect = Exception("Voyage down")
            mock_get.return_value = mock_client

            result = rerank_jobs(jobs, "query")

        assert result[0]["_rerank_unreliable"] is True

    def test_successful_rerank_does_not_mark_jobs_unreliable(self):
        jobs = [_job("j1")]
        with patch("ranker.reranker._get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.rerank.return_value = [{"index": 0, "score": 0.77}]
            mock_get.return_value = mock_client

            result = rerank_jobs(jobs, "query")

        assert "_rerank_unreliable" not in result[0]

    def test_fallback_on_api_error_preserves_original_order(self):
        jobs = [_job("j1"), _job("j2"), _job("j3")]
        for j in jobs:
            j["_embedding_score"] = 0.0

        with patch("ranker.reranker._get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.rerank.side_effect = Exception("Voyage down")
            mock_get.return_value = mock_client

            result = rerank_jobs(jobs, "python", top_k=3)

        assert [r["id"] for r in result] == ["j1", "j2", "j3"]

    def test_fallback_sets_rerank_score_from_embedding_score(self):
        jobs = [_job("j1")]
        jobs[0]["_embedding_score"] = 0.42

        with patch("ranker.reranker._get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.rerank.side_effect = Exception("fail")
            mock_get.return_value = mock_client

            result = rerank_jobs(jobs, "query")

        assert result[0]["rerank_score"] == 0.42

    @patch("ranker.retry.time.sleep")
    def test_retries_typed_transient_error_then_succeeds(self, mock_sleep):
        jobs = [_job("j1")]
        with patch("ranker.reranker._get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.rerank.side_effect = [
                RateLimitError("rate limited"),
                [{"index": 0, "score": 0.9}],
            ]
            mock_get.return_value = mock_client

            result = rerank_jobs(jobs, "query")

        assert result[0]["rerank_score"] == 0.9
        assert "_rerank_unreliable" not in result[0]
        assert mock_client.rerank.call_count == 2
        mock_sleep.assert_called_once()

    def test_respects_top_k_limit(self):
        jobs = [_job(f"j{i}") for i in range(5)]
        rerank_results = [{"index": i, "score": 1.0 - i * 0.1} for i in range(5)]

        with patch("ranker.reranker._get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.rerank.return_value = rerank_results[:2]
            mock_get.return_value = mock_client

            result = rerank_jobs(jobs, "query", top_k=2)

        assert len(result) == 2
