import json
import logging
import random
import re
from datetime import datetime, timezone
import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_RANK_MODEL
from collector.utils import build_excerpt, excerpt_looks_incomplete, INCOMPLETE_DESCRIPTION_NOTE
from db.repositories.usage_repository import log_anthropic
from ranker.retry import call_with_retry

logger = logging.getLogger(__name__)

_RANKING_TOOL = {
    "name": "submit_ranking",
    "description": "Submit the final ranked list of jobs from best to worst fit for the candidate.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ranking": {
                "type": "array",
                "description": "All jobs ordered from best (index 0) to worst fit.",
                "items": {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        "reason": {"type": "string", "description": "One sentence: key reason for this position."},
                    },
                    "required": ["job_id", "reason"],
                },
            },
            "summary": {"type": "string", "description": "2-3 sentence overall analysis of this job batch."},
        },
        "required": ["ranking"],
    },
}


def _posting_age_days(posted_at: str | None) -> int | None:
    """None (not "unknown"/0) when posted_at is absent, LinkedIn and pre-migration
    postings have no reliable per-posting date, and treating that as "just posted"
    would be worse than not mentioning age at all."""
    if not posted_at:
        return None
    try:
        posted_dt = datetime.fromisoformat(posted_at)
    except ValueError:
        return None
    if posted_dt.tzinfo is None:
        posted_dt = posted_dt.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - posted_dt).days)


def _format_job(job: dict) -> str:
    structured = {}
    raw = job.get("structured_data")
    if raw:
        try:
            structured = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            pass

    parts = [f"[ID: {job['id']}]"]
    parts.append(f"Title: {job['title']} @ {job['company']}")
    if job.get("location"):
        parts.append(f"Location: {job['location']}")

    # Posting age was parsed by every collector source (to apply --days), then
    # discarded, nothing downstream could tell a 5-week-old posting from one
    # collected this morning. None (not "unknown") for sources with no reliable
    # per-posting date (LinkedIn), never state an age we don't actually have.
    age_days = _posting_age_days(job.get("posted_at"))
    if age_days is not None:
        parts.append(f"Posted: {age_days} day{'s' if age_days != 1 else ''} ago")

    # The scorer's rating never reached this prompt before, RRF (ranker/fusion.py)
    # uses it to decide which jobs make the listwise pool at all, but Opus itself had
    # no visibility into the most expensive signal in the pipeline once a job got here.
    if job.get("score") is not None:
        reason = f", {job['score_reason']}" if job.get("score_reason") else ""
        parts.append(f"Scorer's rating: {job['score']}/10{reason}")

    # sub_scores (stack_fit/seniority_fit/company_fit/compensation_fit) are
    # computed by evaluator/scorer.py and stored on every scored job, but were
    # never surfaced to any downstream prompt, the per-dimension breakdown
    # behind the single overall_score above, silently discarded.
    score_breakdown = job.get("score_breakdown")
    if score_breakdown:
        try:
            breakdown = json.loads(score_breakdown) if isinstance(score_breakdown, str) else score_breakdown
        except Exception:
            breakdown = None
        sub_scores = breakdown.get("sub_scores") if breakdown else None
        if sub_scores:
            dims = ", ".join(f"{dim}={val}" for dim, val in sub_scores.items())
            parts.append(f"Scorer's sub-scores (0-10): {dims}")

    if structured:
        tags = []
        if structured.get("remote"):
            tags.append("remote")
        elif structured.get("hybrid"):
            tags.append("hybrid")
        if structured.get("seniority"):
            tags.append(structured["seniority"])
        if structured.get("salary_min") and structured.get("salary_max"):
            period = structured.get("salary_period")
            period_suffix = f"/{period}" if period else ""
            tags.append(f"{structured['salary_min']}-{structured['salary_max']} {structured.get('salary_currency', '')}{period_suffix}")
        if structured.get("company_type") and structured["company_type"] != "unknown":
            tags.append(structured["company_type"])
        if structured.get("product_vs_outsourcing") and structured["product_vs_outsourcing"] != "unknown":
            tags.append(structured["product_vs_outsourcing"])
        if structured.get("stack"):
            tags.append(", ".join(structured["stack"][:5]))
        if tags:
            parts.append(f"Tags: {' | '.join(tags)}")

    desc = build_excerpt(job.get("description"), job.get("source")).replace("\n", " ")
    note = INCOMPLETE_DESCRIPTION_NOTE if excerpt_looks_incomplete(desc) else ""
    parts.append(f"Description: {desc}{note}")
    return "\n".join(parts)


# Sentinel rank_reason for the fallback paths below, so a batch that just
# echoes rerank order back stays distinguishable from a genuine Opus ranking
# wherever listwise_rank later gets read as a real signal.
FALLBACK_RANK_REASON = "[unranked, Opus ranking unavailable this run, showing rerank order]"

# Distinct from FALLBACK_RANK_REASON: Opus responded and ranked most jobs for
# real, but silently left some out of its <ranking> array. Those get appended
# at the end by the safety net below.
OMITTED_RANK_REASON = "[omitted by Opus, not in its ranking response, appended at the end]"


def _fallback_ranking(jobs: list[dict]) -> list[dict]:
    return [{**job, "listwise_rank": i + 1, "rank_reason": FALLBACK_RANK_REASON} for i, job in enumerate(jobs)]


def _parse_ranking_json(text: str) -> list[dict]:
    m = re.search(r"<ranking>(.*?)</ranking>", text, re.DOTALL)
    if m:
        return json.loads(m.group(1).strip())
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        return json.loads(m.group(0))
    return []


def listwise_rank(jobs: list[dict], candidate_profile: str, preferences: list[dict], questionnaire: str = "") -> list[dict]:
    """Extended thinking is incompatible with forced tool_choice, so we use JSON in text instead."""
    if not jobs:
        return []

    from preference_agent.profile import render_signals
    prefs_text = ""
    if preferences:
        scored = [s for s in preferences if s.get("type") != "NEUTRAL"]
        if scored:
            prefs_text = f"PREFERENCE PROFILE:\n{render_signals(scored)}\n\n"

    questionnaire_text = f"{questionnaire}\n\n" if questionnaire else ""

    # Shuffled presentation order: a listwise ranker shown jobs best-first (the
    # reranker's own order) with sequential "[Job #N]" labels tends to mostly
    # echo that input order back, position becomes a stronger signal than the
    # actual content, paying full Opus cost for little new information. Only
    # the prompt's presentation order changes here; job_by_id/seen-based
    # result assembly below is keyed by job_id, not position, so this has no
    # other effect. `jobs` itself (and its original order) is left untouched
    # for the safety-net loop and every caller.
    presented = jobs[:]
    random.shuffle(presented)
    jobs_text = "\n\n---\n\n".join(_format_job(job) for job in presented)

    system = f"""You are ranking job listings for a candidate. Analyze every job carefully and produce a definitive ranking.

{candidate_profile}

{questionnaire_text}{prefs_text}Your task: rank ALL {len(jobs)} jobs from best (rank 1) to worst fit. Consider:
- Overall match with candidate profile and experience
- Each job's "Scorer's rating" (if shown) is a per-job first pass made WITHOUT seeing the other jobs in this batch, treat it as one data point to weigh against everything else below, not a target ordering to reproduce. Your job is the comparative judgment the scorer couldn't make.
- The candidate's own stated questionnaire preferences (if given above), direct and current, outranks the inferred preference profile when they conflict
- Preference signals (strong signals = heavy weight)
- Role quality, growth potential, company type
- Dealbreakers (REJECT[conf=ABSOLUTE/HIGH] signals)
- Posting age ("Posted: N days ago", if shown), an older posting is more likely to already be filled or to have gone quiet; let it count moderately against an otherwise-similar fresher posting, not as a hard cutoff

Every job must appear exactly once in the ranking.

Do all your reordering and self-correction before you start writing the JSON, once you begin the <ranking> block, each "reason" must already be your settled, final answer for that job. Never write deliberation, corrections, or meta-commentary about the ranking process itself (e.g. "wait, correcting...", "actually X is better", "placing here by ID") into a "reason" value, it is shown directly to the candidate and must read as a clean, final one-sentence explanation of that job's fit.

After your analysis, output ONLY the final ranking as a JSON array inside <ranking> tags, like this:
<ranking>
[
  {{"job_id": "abc123", "reason": "Best match: senior Python role at product company, remote"}},
  {{"job_id": "def456", "reason": "Good but agency work"}}
]
</ranking>

Include ALL {len(jobs)} jobs. No text after the closing </ranking> tag."""

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    try:
        response = call_with_retry(
            lambda: client.messages.create(
                model=CLAUDE_RANK_MODEL,
                max_tokens=16000,
                thinking={"type": "adaptive"},
                output_config={"effort": "high"},
                system=system,
                messages=[{"role": "user", "content": f"Rank these {len(jobs)} jobs from best to worst:\n\n{jobs_text}"}],
            ),
            label="Listwise ranking",
        )
    except Exception as e:
        logger.error(f"Listwise ranking failed: {e}")
        return _fallback_ranking(jobs)

    log_anthropic(response, "ranker", CLAUDE_RANK_MODEL)

    if response.stop_reason == "max_tokens":
        logger.error("Opus listwise response truncated (max_tokens)")
        return _fallback_ranking(jobs)

    text_block = next((b for b in response.content if b.type == "text"), None)
    if not text_block:
        logger.error("No text block in Opus listwise response")
        return _fallback_ranking(jobs)

    try:
        ranking = _parse_ranking_json(text_block.text)
    except Exception as e:
        logger.error(f"Failed to parse ranking JSON: {e}\nResponse: {text_block.text[:500]}")
        return _fallback_ranking(jobs)

    job_by_id = {job["id"]: job for job in jobs}
    result = []
    seen = set()

    # listwise_rank is always len(result) + 1 at append time, a compacted,
    # gap-free sequence. Using enumerate(ranking)'s raw index instead used to
    # leave a gap whenever a hallucinated/invalid job_id was skipped, which
    # the safety-net loop below (also numbering from len(result) + 1) could
    # then collide with, producing two jobs sharing the same listwise_rank.
    for item in ranking:
        job_id = item.get("job_id")
        if job_id in job_by_id and job_id not in seen:
            result.append({**job_by_id[job_id], "listwise_rank": len(result) + 1, "rank_reason": item.get("reason", "")})
            seen.add(job_id)

    # Safety net: add any jobs Opus missed
    for job in jobs:
        if job["id"] not in seen:
            result.append({**job, "listwise_rank": len(result) + 1, "rank_reason": OMITTED_RANK_REASON})

    logger.info(f"Listwise ranking complete: {len(result)} jobs ranked")
    return result
