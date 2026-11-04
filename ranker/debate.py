import json
import logging
import random
import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from collector.utils import build_excerpt
from db.repositories.usage_repository import log_anthropic
from ranker.retry import call_with_retry

logger = logging.getLogger(__name__)

_VALID_FLAGS = {"dealbreaker_risk", "overrated", "underrated"}

# Written by this module itself on failure, never by the LLM (not in
# _VALID_FLAGS/the tool schema), so "debate ran and flagged nothing" and
# "debate didn't run at all" are distinguishable. Read by ranker/rank_cache.py
# so a failed debate run doesn't get silently reused as "unchanged" later.
DEBATE_UNAVAILABLE_FLAG = "review_unavailable"


def _mark_unavailable(ranked_jobs: list[dict], reason: str) -> list[dict]:
    return [{**job, "debate_flag": DEBATE_UNAVAILABLE_FLAG, "debate_note": reason} for job in ranked_jobs]

# How many positions an overrated/underrated flag shifts a job, modest by design:
# debate is a secondary check, not a second full ranking pass, so it nudges the
# primary listwise ranking rather than overriding its overall judgment. Naturally
# clamped by list bounds (a job can't move further than the list allows) since the
# reorder is a plain sort, not an explicit index swap.
_RANK_NUDGE = 3

_DEBATE_TOOL = {
    "name": "submit_debate_review",
    "description": "Submit a second-opinion critique of an existing job ranking.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reviews": {
                "type": "array",
                "description": "One entry per job that you want to flag, omit jobs you have no concern about.",
                "items": {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        "flag": {
                            "type": "string",
                            "enum": sorted(_VALID_FLAGS),
                            "description": (
                                "dealbreaker_risk: the primary ranking likely missed a real dealbreaker "
                                "(e.g. stack similarity masking a mismatch on seniority, company type, or "
                                "an explicit requirement the candidate can't meet). "
                                "overrated: ranked too high for a softer reason. "
                                "underrated: ranked too low, this job deserves more credit."
                            ),
                        },
                        "note": {"type": "string", "description": "One sentence explaining the flag."},
                    },
                    "required": ["job_id", "flag", "note"],
                },
            },
        },
        "required": ["reviews"],
    },
}


def _format_job_for_review(job: dict) -> str:
    # Rank comes from the job's own listwise_rank, not presentation position,
    # presentation is shuffled below (see debate_rank) so the rank number shown
    # here must stay tied to the real ranking, not to wherever this job landed
    # in the shuffled prompt order.
    parts = [f"[Rank #{job.get('listwise_rank')} | ID: {job['id']}]"]
    parts.append(f"Title: {job['title']} @ {job['company']}")
    reason = job.get("rank_reason") or ""
    if reason:
        parts.append(f"Primary ranking's reason: {reason}")

    breakdown = job.get("score_breakdown")
    if breakdown:
        try:
            b = json.loads(breakdown) if isinstance(breakdown, str) else breakdown
        except Exception:
            b = None
        if b:
            # Exactly the per-dimension signal this reviewer's own brief (below)
            # asks it to check for, seniority mismatch, wrong company type,
            # computed by the scorer but never previously shown here at all.
            sub_scores = b.get("sub_scores")
            if sub_scores:
                dims = ", ".join(f"{dim}={val}" for dim, val in sub_scores.items())
                parts.append(f"Scorer's sub-scores (0-10): {dims}")
            if b.get("pros"):
                parts.append(f"Pros: {'; '.join(b['pros'])}")
            if b.get("cons"):
                parts.append(f"Cons: {'; '.join(b['cons'])}")

    desc = build_excerpt(job.get("description"), job.get("source")).replace("\n", " ")
    parts.append(f"Description: {desc}")
    return "\n".join(parts)


def _parse_reviews(tool_input: dict) -> dict[str, dict]:
    reviews = {}
    for item in tool_input.get("reviews", []):
        if not isinstance(item, dict):
            logger.warning("Skipping malformed debate review item (not a dict): %r", item)
            continue
        job_id = item.get("job_id")
        flag = item.get("flag")
        if job_id and flag in _VALID_FLAGS:
            reviews[job_id] = {"flag": flag, "note": item.get("note", "")}
    return reviews


def debate_rank(ranked_jobs: list[dict], candidate_profile: str, preferences: list[dict] | None = None, questionnaire: str = "") -> list[dict]:
    # Second-opinion critique of an already-ranked shortlist, using a different
    # model than the primary ranker. Doesn't re-derive the ranking, only flags
    # disagreements: "dealbreaker_risk" demotes a job to the bottom;
    # "overrated"/"underrated" get a modest _RANK_NUDGE shift instead of a
    # full re-rank.
    if not ranked_jobs:
        return ranked_jobs

    from preference_agent.profile import render_signals
    prefs_text = ""
    if preferences:
        scored = [s for s in preferences if s.get("type") != "NEUTRAL"]
        if scored:
            prefs_text = f"PREFERENCE PROFILE:\n{render_signals(scored)}\n\n"

    # Shuffled presentation order, same reasoning as ranker/listwise.py: a
    # fixed best-to-worst order risks anchoring the reviewer toward confirming
    # whatever's presented first. Each job still carries its own [Rank #N]
    # label, so the reviewer always knows the real rank.
    presented = ranked_jobs[:]
    random.shuffle(presented)
    jobs_text = "\n\n---\n\n".join(_format_job_for_review(job) for job in presented)
    questionnaire_text = f"{questionnaire}\n\n" if questionnaire else ""

    system = f"""You are a second, independent reviewer checking another model's job ranking for this candidate.

{candidate_profile}

{questionnaire_text}{prefs_text}Each job below is labeled with its rank from the primary ranking (rank #1 = best). Jobs are listed in a
shuffled order, not by rank, go by the label, not position. Your job is NOT to re-rank, it's to catch
cases the primary ranking may have gotten wrong, especially where strong stack/keyword
similarity could mask a real dealbreaker (seniority mismatch, wrong company type, an explicit
requirement the candidate can't meet, etc.).

Only flag jobs you genuinely disagree with. Most jobs need no flag at all, do not flag a job just
to have something to say about it.

Use the submit_debate_review tool to report your findings."""

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    try:
        response = call_with_retry(
            lambda: client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=2000,
                system=system,
                messages=[{"role": "user", "content": f"Review this ranking of {len(ranked_jobs)} jobs:\n\n{jobs_text}"}],
                tools=[_DEBATE_TOOL],
                tool_choice={"type": "tool", "name": "submit_debate_review"},
            ),
            label="Debate review",
        )
    except Exception as e:
        logger.error(f"Debate review failed: {e}")
        return _mark_unavailable(ranked_jobs, f"Debate review unavailable this run: {e}")

    log_anthropic(response, "debate", CLAUDE_MODEL)

    if response.stop_reason == "max_tokens":
        # A truncated review list could still parse as valid-but-partial JSON
        # (missing the last few entries, or a malformed final one), treat it
        # the same as "no tool_use block" below: apply no flags rather than
        # risk acting on a partial critique.
        logger.error("Debate response truncated (max_tokens), no flags applied.")
        return _mark_unavailable(ranked_jobs, "Debate review unavailable this run: response truncated (max_tokens).")

    tool_block = next((b for b in response.content if b.type == "tool_use"), None)
    if not tool_block:
        logger.error("No tool_use block in debate response")
        return _mark_unavailable(ranked_jobs, "Debate review unavailable this run: no tool_use block in response.")

    reviews = _parse_reviews(tool_block.input)
    if not reviews:
        # A clean review must still clear any stale flag left over from a
        # previous run, same as the loop below.
        return [{**job, "debate_flag": None, "debate_note": None} for job in ranked_jobs]

    demoted = []
    kept = []
    for job in ranked_jobs:
        review = reviews.get(job["id"])
        # Always set debate_flag/debate_note explicitly this run rather than
        # leaving a stale value from the DB: a job not in `reviews` was
        # reviewed and found clean, so None is a fresh result, not "no opinion".
        job = {**job, "debate_flag": review["flag"] if review else None, "debate_note": review["note"] if review else None}
        if review and review["flag"] == "dealbreaker_risk":
            demoted.append(job)
        else:
            kept.append(job)

    # The +/-0.5 offset breaks ties strictly in the nudge's direction, otherwise
    # Python's stable sort would let an unflagged job at the landing index win
    # the tie and blunt the shift by up to one position.
    def _nudge_key(indexed_job: tuple[int, dict]) -> float:
        index, job = indexed_job
        flag = job.get("debate_flag")
        if flag == "underrated":
            return index - _RANK_NUDGE - 0.5
        if flag == "overrated":
            return index + _RANK_NUDGE + 0.5
        return float(index)

    nudged_count = sum(1 for job in kept if job.get("debate_flag") in ("overrated", "underrated"))
    kept = [job for _, job in sorted(enumerate(kept), key=_nudge_key)]

    reordered = kept + demoted
    for i, job in enumerate(reordered, 1):
        job["listwise_rank"] = i

    if demoted:
        logger.info(f"Debate review: demoted {len(demoted)} job(s) flagged dealbreaker_risk")
    if nudged_count:
        logger.info(f"Debate review: nudged {nudged_count} job(s) flagged overrated/underrated")

    return reordered
