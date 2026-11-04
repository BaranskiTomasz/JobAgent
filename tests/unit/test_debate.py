from unittest.mock import MagicMock, patch

import anthropic
import httpx

from ranker.debate import DEBATE_UNAVAILABLE_FLAG, debate_rank, _format_job_for_review, _parse_reviews


def _rate_limit_error():
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.RateLimitError("rate limited", response=httpx.Response(429, request=req), body=None)


def _job(job_id="j1", title="Dev", rank=1, rank_reason="Good fit", breakdown=None, **overrides):
    job = {
        "id": job_id, "title": title, "company": "Acme", "description": "PHP role",
        "listwise_rank": rank, "rank_reason": rank_reason,
        "score_breakdown": breakdown,
    }
    job.update(overrides)
    return job


def _debate_response(reviews: list[dict], stop_reason="tool_use"):
    block = MagicMock()
    block.type = "tool_use"
    block.input = {"reviews": reviews}
    response = MagicMock()
    response.content = [block]
    response.stop_reason = stop_reason
    response.usage = MagicMock(input_tokens=100, output_tokens=50, cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return response


class TestFormatJobForReview:
    def test_includes_rank_reason(self):
        text = _format_job_for_review(_job(rank_reason="Great Symfony match"))
        assert "Great Symfony match" in text

    def test_includes_pros_cons_from_breakdown(self):
        import json
        breakdown = json.dumps({"pros": ["Strong stack"], "cons": ["No salary"]})
        text = _format_job_for_review(_job(breakdown=breakdown))
        assert "Strong stack" in text
        assert "No salary" in text

    def test_missing_breakdown_does_not_crash(self):
        text = _format_job_for_review(_job(breakdown=None))
        assert "Dev" in text

    def test_includes_rank_from_job_not_position(self):
        # Rank shown must come from the job's own listwise_rank, not wherever it
        # lands in a (now shuffled) presentation order, see debate_rank.
        text = _format_job_for_review(_job(rank=7))
        assert "Rank #7" in text

    def test_includes_sub_scores_from_breakdown(self):
        # Regression: sub_scores (stack_fit/seniority_fit/company_fit/
        # compensation_fit) were computed by the scorer and stored, but never
        # shown to this reviewer, despite its own brief explicitly asking it to
        # check for exactly a seniority or company-type mismatch.
        import json
        breakdown = json.dumps({"sub_scores": {"seniority_fit": 3, "company_fit": 8}, "pros": [], "cons": []})
        text = _format_job_for_review(_job(breakdown=breakdown))
        assert "seniority_fit=3" in text
        assert "company_fit=8" in text

    def test_missing_sub_scores_does_not_crash_or_add_a_line(self):
        import json
        breakdown = json.dumps({"pros": ["Strong stack"], "cons": []})
        text = _format_job_for_review(_job(breakdown=breakdown))
        assert "sub-scores" not in text.lower()


class TestParseReviews:
    def test_valid_flag_kept(self):
        reviews = _parse_reviews({"reviews": [{"job_id": "j1", "flag": "dealbreaker_risk", "note": "x"}]})
        assert reviews["j1"]["flag"] == "dealbreaker_risk"

    def test_invalid_flag_dropped(self):
        reviews = _parse_reviews({"reviews": [{"job_id": "j1", "flag": "not_a_real_flag", "note": "x"}]})
        assert reviews == {}

    def test_missing_job_id_dropped(self):
        reviews = _parse_reviews({"reviews": [{"flag": "overrated", "note": "x"}]})
        assert reviews == {}

    def test_non_dict_item_dropped_without_losing_valid_reviews(self):
        reviews = _parse_reviews({
            "reviews": [
                {"job_id": "j1", "flag": "overrated", "note": "Ranked too high"},
                "malformed review",
                {"job_id": "j2", "flag": "underrated", "note": "Ranked too low"},
            ],
        })

        assert reviews == {
            "j1": {"flag": "overrated", "note": "Ranked too high"},
            "j2": {"flag": "underrated", "note": "Ranked too low"},
        }


class TestDebateRank:
    def test_empty_input_returns_empty(self):
        assert debate_rank([], "profile") == []

    @patch("ranker.debate.random.shuffle")
    @patch("ranker.debate.anthropic.Anthropic")
    def test_presentation_order_is_shuffled(self, mock_anthropic, mock_shuffle):
        # Regression: jobs used to be sent to the reviewer in a fixed best-to-
        # worst order (matching listwise_rank), exactly the anchoring risk
        # ranker/listwise.py already shuffles against for the primary ranking.
        jobs = [_job("j1", rank=1), _job("j2", rank=2), _job("j3", rank=3)]
        mock_anthropic.return_value.messages.create.return_value = _debate_response([])
        debate_rank(jobs, "profile")
        mock_shuffle.assert_called_once()

    @patch("ranker.debate.anthropic.Anthropic")
    def test_rank_label_sent_to_reviewer_reflects_listwise_rank_not_presentation_order(self, mock_anthropic):
        jobs = [_job("j1", rank=5), _job("j2", rank=1)]
        mock_anthropic.return_value.messages.create.return_value = _debate_response([])
        debate_rank(jobs, "profile")
        sent_text = mock_anthropic.return_value.messages.create.call_args.kwargs["messages"][0]["content"]
        assert "[Rank #5 | ID: j1]" in sent_text
        assert "[Rank #1 | ID: j2]" in sent_text

    @patch("ranker.debate.anthropic.Anthropic")
    def test_no_flags_preserves_order(self, mock_anthropic):
        jobs = [_job("j1", rank=1), _job("j2", rank=2)]
        mock_anthropic.return_value.messages.create.return_value = _debate_response([])
        result = debate_rank(jobs, "profile")
        assert [j["id"] for j in result] == ["j1", "j2"]

    @patch("ranker.debate.anthropic.Anthropic")
    def test_no_flags_does_not_get_the_unavailable_sentinel(self, mock_anthropic):
        # A genuine review that found nothing to flag must stay indistinguishable
        # from "no opinion", only an actual failure should ever set
        # DEBATE_UNAVAILABLE_FLAG.
        jobs = [_job("j1", rank=1)]
        mock_anthropic.return_value.messages.create.return_value = _debate_response([])
        result = debate_rank(jobs, "profile")
        assert result[0].get("debate_flag") != DEBATE_UNAVAILABLE_FLAG

    @patch("ranker.debate.anthropic.Anthropic")
    def test_stale_flag_cleared_when_this_job_is_not_reflagged(self, mock_anthropic):
        # Regression: a job carrying a stale debate_flag/debate_note from a
        # previous run (fetched straight from the DB row) used to keep it forever
        # once no fresh review mentioned that specific job again, permanently
        # blocking would_apply (dealbreaker_risk) or re-applying the same rank
        # nudge (overrated/underrated) with no new evidence.
        jobs = [
            _job("j1", rank=1, debate_flag="dealbreaker_risk", debate_note="Old note from weeks ago"),
            _job("j2", rank=2),
        ]
        mock_anthropic.return_value.messages.create.return_value = _debate_response([
            {"job_id": "j2", "flag": "overrated", "note": "fresh flag on a different job"},
        ])
        result = debate_rank(jobs, "profile")
        j1 = next(j for j in result if j["id"] == "j1")
        assert j1["debate_flag"] is None
        assert j1["debate_note"] is None

    @patch("ranker.debate.anthropic.Anthropic")
    def test_stale_flag_cleared_when_the_whole_review_is_clean(self, mock_anthropic):
        # Same regression as above, for the "reviews entirely empty" early return.
        jobs = [_job("j1", rank=1, debate_flag="overrated", debate_note="Stale")]
        mock_anthropic.return_value.messages.create.return_value = _debate_response([])
        result = debate_rank(jobs, "profile")
        assert result[0]["debate_flag"] is None
        assert result[0]["debate_note"] is None

    @patch("ranker.debate.anthropic.Anthropic")
    def test_dealbreaker_risk_demotes_job_to_bottom(self, mock_anthropic):
        jobs = [_job("j1", rank=1), _job("j2", rank=2), _job("j3", rank=3)]
        mock_anthropic.return_value.messages.create.return_value = _debate_response([
            {"job_id": "j1", "flag": "dealbreaker_risk", "note": "Seniority mismatch"},
        ])
        result = debate_rank(jobs, "profile")
        assert [j["id"] for j in result] == ["j2", "j3", "j1"]
        assert result[-1]["listwise_rank"] == 3
        assert result[-1]["debate_flag"] == "dealbreaker_risk"
        assert result[-1]["debate_note"] == "Seniority mismatch"

    @patch("ranker.debate.anthropic.Anthropic")
    def test_overrated_flag_moves_job_down(self, mock_anthropic):
        # Regression: overrated/underrated used to be attached to the job
        # (debate_flag/debate_note, shown in the UI) but never changed its
        # actual position, only dealbreaker_risk did anything.
        jobs = [_job("j1", rank=1), _job("j2", rank=2)]
        mock_anthropic.return_value.messages.create.return_value = _debate_response([
            {"job_id": "j1", "flag": "overrated", "note": "Slightly overrated"},
        ])
        result = debate_rank(jobs, "profile")
        assert [j["id"] for j in result] == ["j2", "j1"]
        assert result[-1]["debate_flag"] == "overrated"
        assert result[-1]["listwise_rank"] == 2

    @patch("ranker.debate.anthropic.Anthropic")
    def test_overrated_flag_moves_down_by_rank_nudge_positions(self, mock_anthropic):
        jobs = [_job(f"j{i}", rank=i) for i in range(1, 8)]  # j1..j7, plenty of room below
        mock_anthropic.return_value.messages.create.return_value = _debate_response([
            {"job_id": "j1", "flag": "overrated", "note": "x"},
        ])
        result = debate_rank(jobs, "profile")
        ids = [j["id"] for j in result]
        assert ids.index("j1") == 3  # moved from index 0 down exactly _RANK_NUDGE=3

    @patch("ranker.debate.anthropic.Anthropic")
    def test_underrated_flag_moves_up_by_rank_nudge_positions(self, mock_anthropic):
        jobs = [_job(f"j{i}", rank=i) for i in range(1, 8)]  # j1..j7
        mock_anthropic.return_value.messages.create.return_value = _debate_response([
            {"job_id": "j7", "flag": "underrated", "note": "x"},
        ])
        result = debate_rank(jobs, "profile")
        ids = [j["id"] for j in result]
        assert ids.index("j7") == 3  # moved from index 6 up exactly _RANK_NUDGE=3

    @patch("ranker.debate.anthropic.Anthropic")
    def test_underrated_flag_clamped_at_top_when_not_enough_room(self, mock_anthropic):
        jobs = [_job("j1", rank=1), _job("j2", rank=2), _job("j3", rank=3)]
        mock_anthropic.return_value.messages.create.return_value = _debate_response([
            {"job_id": "j2", "flag": "underrated", "note": "x"},
        ])
        result = debate_rank(jobs, "profile")
        assert result[0]["id"] == "j2"

    @patch("ranker.debate.anthropic.Anthropic")
    def test_overrated_flag_clamped_at_bottom_when_not_enough_room(self, mock_anthropic):
        jobs = [_job("j1", rank=1), _job("j2", rank=2), _job("j3", rank=3)]
        mock_anthropic.return_value.messages.create.return_value = _debate_response([
            {"job_id": "j2", "flag": "overrated", "note": "x"},
        ])
        result = debate_rank(jobs, "profile")
        assert result[-1]["id"] == "j2"

    @patch("ranker.debate.anthropic.Anthropic")
    def test_dealbreaker_demotion_and_nudge_combine_correctly(self, mock_anthropic):
        jobs = [_job("j1", rank=1), _job("j2", rank=2), _job("j3", rank=3), _job("j4", rank=4)]
        mock_anthropic.return_value.messages.create.return_value = _debate_response([
            {"job_id": "j1", "flag": "dealbreaker_risk", "note": "x"},
            {"job_id": "j4", "flag": "underrated", "note": "y"},
        ])
        result = debate_rank(jobs, "profile")
        ids = [j["id"] for j in result]
        assert ids[-1] == "j1"  # dealbreaker still goes to the absolute bottom
        assert ids.index("j4") < ids.index("j2")  # underrated j4 nudged ahead of j2/j3

    @patch("ranker.debate.anthropic.Anthropic")
    def test_ranks_renumbered_sequentially_after_demotion(self, mock_anthropic):
        jobs = [_job("j1", rank=1), _job("j2", rank=2), _job("j3", rank=3)]
        mock_anthropic.return_value.messages.create.return_value = _debate_response([
            {"job_id": "j2", "flag": "dealbreaker_risk", "note": "x"},
        ])
        result = debate_rank(jobs, "profile")
        assert [j["listwise_rank"] for j in result] == [1, 2, 3]

    @patch("ranker.debate.anthropic.Anthropic")
    def test_api_error_marks_jobs_unavailable_instead_of_looking_like_a_clean_review(self, mock_anthropic):
        # Regression: this used to return ranked_jobs completely untouched,
        # identical in the stored data to "the reviewer checked everything and
        # flagged nothing", with no way to tell the two apart later.
        jobs = [_job("j1", rank=1), _job("j2", rank=2)]
        mock_anthropic.return_value.messages.create.side_effect = Exception("boom")
        result = debate_rank(jobs, "profile")
        assert [j["id"] for j in result] == ["j1", "j2"]
        assert all(j["debate_flag"] == DEBATE_UNAVAILABLE_FLAG for j in result)
        assert all(j["debate_note"] for j in result)

    @patch("ranker.retry.time.sleep")
    @patch("ranker.debate.anthropic.Anthropic")
    def test_retries_a_transient_error_instead_of_returning_original_ranking(self, mock_anthropic, mock_sleep):
        # Regression: debate_rank used to have no retry at all, a single
        # transient hiccup fell straight through to "return ranked_jobs
        # unchanged", silently skipping the whole debate review for that run.
        jobs = [_job("j1", rank=1), _job("j2", rank=2)]
        mock_anthropic.return_value.messages.create.side_effect = [
            _rate_limit_error(),
            _debate_response([{"job_id": "j1", "flag": "dealbreaker_risk", "note": "risk"}]),
        ]

        result = debate_rank(jobs, "profile")

        assert mock_anthropic.return_value.messages.create.call_count == 2
        assert result[-1]["id"] == "j1"  # the flag from the *retried* response was actually applied
        assert result[-1]["debate_flag"] == "dealbreaker_risk"

    @patch("ranker.debate.anthropic.Anthropic")
    def test_no_tool_use_block_marks_jobs_unavailable(self, mock_anthropic):
        text_block = MagicMock(type="text")
        response = MagicMock(
            content=[text_block], stop_reason="end_turn",
            usage=MagicMock(input_tokens=1, output_tokens=1, cache_creation_input_tokens=0, cache_read_input_tokens=0),
        )
        mock_anthropic.return_value.messages.create.return_value = response
        jobs = [_job("j1", rank=1)]
        result = debate_rank(jobs, "profile")
        assert result[0]["id"] == "j1"
        assert result[0]["debate_flag"] == DEBATE_UNAVAILABLE_FLAG

    @patch("ranker.debate.anthropic.Anthropic")
    def test_truncated_response_marks_jobs_unavailable_not_a_clean_review(self, mock_anthropic):
        # Regression: a truncated reviews array can still parse as valid-but-
        # partial JSON, without an explicit stop_reason check, a partial
        # critique could apply flags based on an incomplete review. Separately,
        # this used to return ranked_jobs untouched (no debate_flag at all),
        # indistinguishable from a genuine "nothing to flag" review.
        jobs = [_job("j1", rank=1), _job("j2", rank=2)]
        mock_anthropic.return_value.messages.create.return_value = _debate_response(
            [{"job_id": "j1", "flag": "dealbreaker_risk", "note": "x"}], stop_reason="max_tokens"
        )
        result = debate_rank(jobs, "profile")
        assert [j["id"] for j in result] == ["j1", "j2"]
        assert result[0]["debate_flag"] == DEBATE_UNAVAILABLE_FLAG  # not "dealbreaker_risk", that flag must be ignored

    @patch("ranker.debate.anthropic.Anthropic")
    def test_questionnaire_included_in_system_prompt_when_given(self, mock_anthropic):
        jobs = [_job("j1", rank=1)]
        mock_anthropic.return_value.messages.create.return_value = _debate_response([])
        debate_rank(jobs, "profile", questionnaire="CANDIDATE QUESTIONNAIRE:\n- Work mode: remote")
        system_prompt = mock_anthropic.return_value.messages.create.call_args.kwargs["system"]
        assert "CANDIDATE QUESTIONNAIRE" in system_prompt
        assert "Work mode: remote" in system_prompt

    @patch("ranker.debate.anthropic.Anthropic")
    def test_no_questionnaire_section_when_omitted(self, mock_anthropic):
        jobs = [_job("j1", rank=1)]
        mock_anthropic.return_value.messages.create.return_value = _debate_response([])
        debate_rank(jobs, "profile")
        system_prompt = mock_anthropic.return_value.messages.create.call_args.kwargs["system"]
        assert "CANDIDATE QUESTIONNAIRE" not in system_prompt

    @patch("ranker.debate.anthropic.Anthropic")
    def test_preference_profile_included_in_system_prompt_when_given(self, mock_anthropic):
        # Regression: the reviewer used to critique a ranking that WAS built
        # from the learned preference profile while having no visibility into
        # that profile itself, auditing with less information than the thing
        # it was auditing had.
        jobs = [_job("j1", rank=1)]
        preferences = [{"type": "ACCEPT", "dim": "company_type", "value": "product", "conf": "HIGH", "n_match": 3, "n_total": 3}]
        mock_anthropic.return_value.messages.create.return_value = _debate_response([])
        debate_rank(jobs, "profile", preferences)
        system_prompt = mock_anthropic.return_value.messages.create.call_args.kwargs["system"]
        assert "PREFERENCE PROFILE" in system_prompt
        assert "ACCEPT[company_type=product" in system_prompt

    @patch("ranker.debate.anthropic.Anthropic")
    def test_no_preference_section_when_omitted(self, mock_anthropic):
        jobs = [_job("j1", rank=1)]
        mock_anthropic.return_value.messages.create.return_value = _debate_response([])
        debate_rank(jobs, "profile")
        system_prompt = mock_anthropic.return_value.messages.create.call_args.kwargs["system"]
        assert "PREFERENCE PROFILE" not in system_prompt

    @patch("ranker.debate.anthropic.Anthropic")
    def test_neutral_only_preferences_omit_the_section(self, mock_anthropic):
        # Mirrors listwise_rank's own filtering: NEUTRAL signals aren't
        # actionable, so a profile containing only NEUTRAL entries shouldn't
        # render an empty/useless PREFERENCE PROFILE section.
        jobs = [_job("j1", rank=1)]
        preferences = [{"type": "NEUTRAL", "dim": "compensation"}]
        mock_anthropic.return_value.messages.create.return_value = _debate_response([])
        debate_rank(jobs, "profile", preferences)
        system_prompt = mock_anthropic.return_value.messages.create.call_args.kwargs["system"]
        assert "PREFERENCE PROFILE" not in system_prompt
