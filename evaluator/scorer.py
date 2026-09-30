import logging
import time
import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from collector.utils import build_excerpt, excerpt_looks_incomplete, INCOMPLETE_DESCRIPTION_NOTE
from db.repositories.usage_repository import log_anthropic
from db.types import ScoreResult
from preference_agent.profile import render_signals

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


# Mirrors preference_agent/runner.py's _REASON_LIMIT. The system prompt is
# built once per batch and cached, not rebuilt per job, so the extra length
# is paid once per run.
_EXAMPLE_DESC_LIMIT = 400
_EXAMPLE_REASON_LIMIT = 400


def _build_examples_section(positive: list[dict], negative: list[dict]) -> str:
    lines = []
    if positive:
        lines.append("EXAMPLES OF JOBS I APPLIED TO (high quality, learn what I like):")
        for ex in positive:
            desc = build_excerpt(ex.get("description"), ex.get("source"))[:_EXAMPLE_DESC_LIMIT].replace("\n", " ")
            lines.append(f'- "{ex["title"]}" @ {ex["company"]}: {desc}...')
        lines.append("")
    if negative:
        lines.append("EXAMPLES OF JOBS I REJECTED (learn what to avoid):")
        for ex in negative:
            desc = build_excerpt(ex.get("description"), ex.get("source"))[:_EXAMPLE_DESC_LIMIT].replace("\n", " ")
            reason = (ex.get("rejection_reason") or "")[:_EXAMPLE_REASON_LIMIT]
            reason_str = f" [My reason: {reason}]" if reason else ""
            lines.append(f'- "{ex["title"]}" @ {ex["company"]}: {desc}...{reason_str}')
        lines.append("")
    return "\n".join(lines) + "\n" if lines else ""


# Just the REJECT/ACCEPT/INFER syntax, only meaningful alongside a LEARNED
# PREFERENCE PROFILE block. The precedence rule lives in _build_precedence_note
# instead, since MUST HAVE/questionnaire/PREFERRED need it in every prompt,
# not only when a learned profile exists.
_ANNOTATION_LEGEND = (
    "Interpretation:\n"
    "- REJECT[conf=ABSOLUTE/HIGH]: near-dealbreaker, score ≤2 if job matches this pattern\n"
    "- REJECT[conf=MEDIUM]: strong penalty; REJECT[conf=LOW]: minor penalty\n"
    "- ACCEPT[...]: positive signal, weight by conf the same way\n"
    "- INFER[...]: soft signal, if the offer lacks the data (e.g. no salary shown), do NOT penalize\n"
    "- n=X/Y = evidence count; higher Y = more reliable signal\n\n"
)


def _build_precedence_note(has_questionnaire: bool, has_learned_profile: bool) -> str:
    # Only chains in the tiers actually present in this prompt, so it never
    # references a section that was never included.
    if has_questionnaire and has_learned_profile:
        middle = (
            "then the CANDIDATE QUESTIONNAIRE (their own direct, current answers) outranks the "
            "LEARNED PREFERENCE PROFILE (inferred from past applied/rejected jobs, useful nuance, "
            "but a proxy for what they told you directly, not a replacement); that profile in turn "
            "overrides PREFERRED criteria"
        )
    elif has_questionnaire:
        middle = "then the CANDIDATE QUESTIONNAIRE (their own direct, current answers) outranks PREFERRED criteria"
    elif has_learned_profile:
        middle = (
            "then the LEARNED PREFERENCE PROFILE (inferred from past applied/rejected jobs) "
            "outranks PREFERRED criteria"
        )
    else:
        middle = "PREFERRED criteria are the lowest priority"
    return f"Precedence when sources conflict: MUST HAVE always wins; {middle}.\n\n"


def _build_preferences_section(learned_preferences: list[dict] | str) -> str:
    if isinstance(learned_preferences, list):
        scored = [s for s in learned_preferences if s.get("type") != "NEUTRAL"]
        if not scored:
            return ""
        scored_text = render_signals(scored)
    else:
        if not learned_preferences.strip():
            return ""
        scored_text = "\n".join(
            line for line in learned_preferences.splitlines()
            if line.strip() and not line.strip().startswith("NEUTRAL[")
        )
        if not scored_text.strip():
            return ""
    return f"LEARNED PREFERENCE PROFILE:\n{scored_text}\n\n{_ANNOTATION_LEGEND}"


def _build_calibration_section(divergence_cases: list[dict]) -> str:
    # Cases where a past ranking diverged from the candidate's actual decision
    # (evaluation/harness.py::divergence_cases()): the model's own past
    # mistakes, kept separate from the generic examples above since these are
    # specifically patterns to stop repeating.
    if not divergence_cases:
        return ""
    lines = []
    for case in divergence_cases:
        if case.get("divergence_type") == "false_positive":
            reason = (case.get("rejection_reason") or "").strip()
            reason_str = f", reason: \"{reason}\"" if reason else ""
            lines.append(f'- Ranked #{case["listwise_rank"]} but candidate rejected: "{case["title"]}"{reason_str}')
        elif case.get("divergence_type") == "false_negative":
            lines.append(f'- Candidate applied despite rank #{case["listwise_rank"]}: "{case["title"]}", this fit better than the ranking showed')
    if not lines:
        return ""
    return "CALIBRATION, you got these wrong before, don't repeat the pattern:\n" + "\n".join(lines) + "\n\n"


def build_system_prompt(
    criteria: dict,
    positive_examples: list[dict],
    negative_examples: list[dict],
    candidate_profile: str = "",
    learned_preferences: list[dict] | str = "",
    divergence_cases: list[dict] | None = None,
    questionnaire: str = "",
) -> str:
    """Call once per batch and reuse across jobs."""
    questionnaire_section = f"{questionnaire}\n\n" if questionnaire else ""
    prefs_section = _build_preferences_section(learned_preferences)
    precedence_note = _build_precedence_note(has_questionnaire=bool(questionnaire), has_learned_profile=bool(prefs_section))
    examples_section = _build_examples_section(positive_examples, negative_examples)
    calibration_section = _build_calibration_section(divergence_cases or [])

    required = criteria.get("required", [])
    preferred = criteria.get("preferred", [])
    required_lines = "\n".join(f"- {r}" for r in required) if required else "- (none configured)"
    preferred_lines = "\n".join(f"- {p}" for p in preferred) if preferred else "- (none configured)"
    # Multiple entries are alternatives (candidate needs at least one), matching the
    # keyword pre-filter's OR semantics (collector/filters.py), not "needs every one".
    required_header = (
        "MUST HAVE, candidate needs AT LEAST ONE of these "
        "(heavy penalty ONLY if the job matches none, score ≤2):"
        if len(required) > 1
        else "MUST HAVE (heavy penalty if absent, score ≤2):"
    )

    return f"""You are evaluating job listings for a candidate.

{candidate_profile}

{questionnaire_section}{prefs_section}{examples_section}{calibration_section}{precedence_note}{required_header}
{required_lines}

PREFERRED (increases score):
{preferred_lines}

Score the job 0-10 based on overall fit with the candidate profile, must-have criteria, and preferences.
Use the full range, don't default to clustering scores in 6-8:
- 9-10: Exceptional, matches nearly everything the candidate wants, no real cons.
- 7-8: Strong, clearly worth applying to; only minor gaps or open questions.
- 5-6: Mixed, meets the must-haves, but several stated preferences go unmet; a genuine toss-up.
- 3-4: Weak, barely clears the must-haves, multiple real concerns.
- 0-2: Poor / near-dealbreaker, fails or barely meets what matters most.

Missing salary/compensation info is neutral, not a red flag, most postings simply don't disclose it. Never
list "no salary shown" or similar as a con, and never let it lower the score. Only treat compensation as a
signal when a number IS disclosed: a rate/range that meets or exceeds the candidate's expectations is a
genuine pro; one that falls short is a genuine con.

Use the submit_score tool to return your evaluation."""


def _build_user_message(job: dict) -> str:
    excerpt = build_excerpt(job.get("description"), job.get("source"))
    note = INCOMPLETE_DESCRIPTION_NOTE if excerpt_looks_incomplete(excerpt) else ""
    return (
        f"Evaluate this job:\n\n"
        f"Title: {job['title']}\n"
        f"Company: {job['company']}\n"
        f"Location: {job['location']}\n"
        f"Description: {excerpt}{note}"
    )


_SCORE_TOOL = {
    "name": "submit_score",
    "description": "Submit the evaluated score for a job listing.",
    "input_schema": {
        "type": "object",
        "properties": {
            "sub_scores": {
                "type": "object",
                "description": "Independent 0-10 ratings per dimension, for transparency, not averaged into the overall score.",
                "properties": {
                    "stack_fit":         {"type": "number", "description": "How well the tech stack matches, 0-10."},
                    "seniority_fit":     {"type": "number", "description": "How well the seniority level matches, 0-10."},
                    "company_fit":       {"type": "number", "description": "Fit with preferred company type/industry, 0-10."},
                    "compensation_fit":  {"type": "number", "description": "Fit with salary expectations, 0-10. Use 5 (neutral) when no salary is disclosed, absence of data is never a penalty."},
                },
                "required": ["stack_fit", "seniority_fit", "company_fit", "compensation_fit"],
            },
            "pros": {
                "type": "array", "items": {"type": "string"},
                "description": "Short phrases: concrete reasons this job fits well.",
            },
            "cons": {
                "type": "array", "items": {"type": "string"},
                "description": "Short phrases: concrete reasons this job doesn't fit, or open concerns. "
                               "Never include missing salary/compensation disclosure as a con.",
            },
            "overall_score": {"type": "number", "description": "Holistic score from 0 to 10, your own judgment, not a formula over sub_scores."},
            "score_reason": {"type": "string", "description": "One sentence summarizing the overall_score."},
        },
        "required": ["sub_scores", "pros", "cons", "overall_score", "score_reason"],
    },
}

_ERROR_RESULT = {
    "score": None,
    "score_reason": "",
    "breakdown": None,
}


def score_job(job: dict, system_prompt: str) -> ScoreResult:
    """Score a single job using a pre-built system prompt."""
    user_message = _build_user_message(job)
    client = _get_client()

    response = None
    for attempt in range(3):
        try:
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=700,
                system=[{
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": user_message}],
                tools=[_SCORE_TOOL],
                tool_choice={"type": "tool", "name": "submit_score"},
            )
            break
        except Exception as e:
            if "overloaded" in str(e).lower() and attempt < 2:
                wait = (attempt + 1) * 30
                logger.warning(f"API overloaded, retrying in {wait}s...")
                time.sleep(wait)
            else:
                return {**_ERROR_RESULT, "score_reason": f"API error: {e}"}

    if not response or not response.content:
        return {**_ERROR_RESULT, "score_reason": "Empty response from API"}

    log_anthropic(response, "scorer", CLAUDE_MODEL)

    if response.stop_reason == "max_tokens":
        # A truncated tool_use block can still parse as valid-but-incomplete
        # JSON, so without this check a missing overall_score would default to
        # 0.0 and permanently auto-reject the job instead of leaving it
        # unscored for evaluator/runner.py to retry.
        return {**_ERROR_RESULT, "score_reason": "Response truncated (max_tokens)"}

    tool_block = next((b for b in response.content if b.type == "tool_use"), None)
    if not tool_block:
        return {**_ERROR_RESULT, "score_reason": "No tool_use block in response"}

    result = tool_block.input
    return {
        "score":        float(result.get("overall_score", 0)),
        "score_reason": result.get("score_reason", ""),
        "breakdown": {
            "sub_scores": result.get("sub_scores", {}),
            "pros":       result.get("pros", []),
            "cons":       result.get("cons", []),
        },
    }
