from unittest.mock import MagicMock, patch

import anthropic
import httpx

from evaluator.scorer import score_job


def _job(**overrides):
    base = {"title": "PHP Developer", "company": "Acme", "location": "Poland", "description": "Symfony required."}
    base.update(overrides)
    return base


def _tool_response(stop_reason="tool_use", **input_overrides):
    tool_input = {
        "sub_scores": {"stack_fit": 8, "seniority_fit": 7, "company_fit": 6, "compensation_fit": 5},
        "pros": ["Strong PHP/Symfony match"],
        "cons": ["No salary listed"],
        "overall_score": 7.5,
        "score_reason": "Good overall fit",
    }
    tool_input.update(input_overrides)

    block = MagicMock()
    block.type = "tool_use"
    block.input = tool_input

    response = MagicMock()
    response.content = [block]
    response.stop_reason = stop_reason
    response.usage = MagicMock(input_tokens=100, output_tokens=50, cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return response


def _request():
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _internal_server_error():
    return anthropic.InternalServerError("overloaded", response=httpx.Response(529, request=_request()), body=None)


def _bad_request_error():
    return anthropic.BadRequestError("invalid request", response=httpx.Response(400, request=_request()), body=None)


class TestScoreJobStructuredResult:
    @patch("evaluator.scorer._get_client")
    def test_returns_overall_score(self, mock_get_client):
        mock_get_client.return_value.messages.create.return_value = _tool_response()
        result = score_job(_job(), "system prompt")
        assert result["score"] == 7.5

    @patch("evaluator.scorer._get_client")
    def test_returns_breakdown_with_subscores_pros_cons(self, mock_get_client):
        mock_get_client.return_value.messages.create.return_value = _tool_response()
        result = score_job(_job(), "system prompt")
        assert result["breakdown"]["sub_scores"]["stack_fit"] == 8
        assert result["breakdown"]["pros"] == ["Strong PHP/Symfony match"]
        assert result["breakdown"]["cons"] == ["No salary listed"]

    @patch("evaluator.scorer._get_client")
    def test_returns_score_reason(self, mock_get_client):
        mock_get_client.return_value.messages.create.return_value = _tool_response(score_reason="Great fit")
        result = score_job(_job(), "system prompt")
        assert result["score_reason"] == "Great fit"

    @patch("evaluator.scorer._get_client")
    def test_missing_pros_cons_default_to_empty_list(self, mock_get_client):
        tool_input = {"sub_scores": {}, "overall_score": 5, "score_reason": "ok"}
        block = MagicMock(type="tool_use", input=tool_input)
        response = MagicMock(content=[block], usage=MagicMock(input_tokens=10, output_tokens=10, cache_creation_input_tokens=0, cache_read_input_tokens=0))
        mock_get_client.return_value.messages.create.return_value = response
        result = score_job(_job(), "system prompt")
        assert result["breakdown"]["pros"] == []
        assert result["breakdown"]["cons"] == []

    @patch("evaluator.scorer._get_client")
    def test_empty_response_returns_error_result_with_null_breakdown(self, mock_get_client):
        mock_get_client.return_value.messages.create.return_value = MagicMock(content=[])
        result = score_job(_job(), "system prompt")
        assert result["score"] is None
        assert result["breakdown"] is None

    @patch("evaluator.scorer._get_client")
    def test_no_tool_use_block_returns_error_result(self, mock_get_client):
        text_block = MagicMock(type="text")
        mock_get_client.return_value.messages.create.return_value = MagicMock(content=[text_block])
        result = score_job(_job(), "system prompt")
        assert result["score"] is None
        assert result["breakdown"] is None

    @patch("evaluator.scorer._get_client")
    def test_truncated_response_returns_error_result_not_zero_score(self, mock_get_client):
        # Regression: a truncated tool_use block can still parse as valid-but-
        # incomplete JSON, missing "overall_score" would previously default
        # to 0.0 via .get("overall_score", 0), permanently auto-rejecting the
        # job instead of leaving it unscored (score=None) for retry.
        response = _tool_response(stop_reason="max_tokens")
        response.content[0].input = {"sub_scores": {}}  # overall_score missing, as if cut mid-object
        mock_get_client.return_value.messages.create.return_value = response
        result = score_job(_job(), "system prompt")
        assert result["score"] is None
        assert "truncated" in result["score_reason"].lower()

    @patch("evaluator.scorer._get_client")
    def test_api_error_returns_error_result(self, mock_get_client):
        mock_get_client.return_value.messages.create.side_effect = Exception("boom")
        result = score_job(_job(), "system prompt")
        assert result["score"] is None
        assert "API error" in result["score_reason"]
        assert result["breakdown"] is None

    @patch("ranker.retry.time.sleep")
    @patch("evaluator.scorer._get_client")
    def test_retries_typed_overloaded_error_then_succeeds(self, mock_get_client, mock_sleep):
        mock_get_client.return_value.messages.create.side_effect = [_internal_server_error(), _tool_response()]

        assert score_job(_job(), "system prompt")["score"] == 7.5
        assert mock_get_client.return_value.messages.create.call_count == 2
        mock_sleep.assert_called_once()

    @patch("ranker.retry.time.sleep")
    @patch("evaluator.scorer._get_client")
    def test_keeps_legacy_overloaded_message_retry(self, mock_get_client, mock_sleep):
        mock_get_client.return_value.messages.create.side_effect = [Exception("service overloaded"), _tool_response()]

        assert score_job(_job(), "system prompt")["score"] == 7.5
        assert mock_get_client.return_value.messages.create.call_count == 2
        mock_sleep.assert_called_once()

    @patch("ranker.retry.time.sleep")
    @patch("evaluator.scorer._get_client")
    def test_does_not_retry_non_transient_api_error(self, mock_get_client, mock_sleep):
        mock_get_client.return_value.messages.create.side_effect = _bad_request_error()

        assert score_job(_job(), "system prompt")["score"] is None
        assert mock_get_client.return_value.messages.create.call_count == 1
        mock_sleep.assert_not_called()
