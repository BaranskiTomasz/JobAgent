import sys
import logging

logger = logging.getLogger(__name__)

import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_DISTILL_MODEL
from collector.utils import build_excerpt
from db.repositories import job_repository, preference_repository, dismissed_item_repository
from db.repositories.usage_repository import log_anthropic
from evaluator.profile import load_questionnaire_preferences
from preference_agent.profile import _DISTILL_TOOL, render_signals

_SYSTEM = """\
You distill job application history into a structured preference profile consumed by another AI evaluator.
Optimize for: maximum information density, unambiguous signals, evidence-backed confidence.

Analyze ALL dimensions relevant to job-candidate fit, including but not limited to:
- Compensation/rate patterns (explicit amounts, "competitive" language, missing rate)
- Company type (product vs agency/staffing/outsourcing vs enterprise)
- Contract form (B2B, employment, contract-to-hire)
- Work culture (async, startup pace, corporate, team size, meeting culture)
- Tech stack and seniority match
- Domain/industry fit
- Role responsibilities and scope
- Any other patterns consistently visible in the feedback

Remote-country matching is now enforced deterministically upstream whenever the candidate
has set remote_countries in their questionnaire (evaluator/dealbreakers.py's geo dealbreaker
rejects a remote job restricted to the wrong countries before it ever reaches feedback), do
NOT re-derive that narrow signal. Other location/geography nuance is NOT filtered upstream and
remains fair game: timezone-overlap preferences, visa/relocation requirements, hybrid/onsite
city fit, or any pattern in rejection reasons the deterministic filter can't see.

If a CANDIDATE QUESTIONNAIRE section appears below, treat it as ground truth already known to
the evaluator, do not spend a signal restating something the candidate stated there directly
(e.g. a company type or salary floor already given). Only emit a signal where behavior reveals
something the questionnaire doesn't already say, or where it genuinely diverges from what was
stated (e.g. they say they want product companies but keep applying to agencies), that
divergence is itself a high-value signal, worth calling out explicitly.

Each APPLIED/REJECTED example is dated ("decided YYYY-MM-DD"), weight recent decisions more
heavily than old ones, same evidence otherwise. If a dimension shows a clear split by time
(e.g. rejected every agency 6 months ago, but applied to one last week), that's a genuine
reversal, not conflicting evidence to average away, call it out and prefer the more recent
pattern in the signal you emit, at a lower conf than you'd otherwise use given the small
recent sample.

If a YOUR PREVIOUS PROFILE section appears below, reconcile with it, it's your own prior
conclusion from the last distillation, not a new candidate to weigh against this one. Keep a
previous signal (updated n_match/n_total to the current totals) if it's still supported by the
evidence; only change or drop it when new evidence genuinely contradicts it, and prefer to state
that as a reversal (see the recency guidance above) rather than silently emitting a different
value for the same dimension. Don't let the same underlying evidence flip a signal's value or
confidence run-to-run on nothing but chance.

You may also see a DISMISSED SCORE FACTORS section: cases where the candidate looked at a specific
pro/con from a past AI evaluation and explicitly said it doesn't apply to them, with a reason. This is
the strongest signal available, direct correction, not inference. Fold each into whichever dimension it
belongs to (e.g. dismissing a timezone/right-to-work con → work_culture; dismissing a company-size con →
company_type) rather than repeating the dismissed text verbatim, and weight it at least conf=HIGH.

Use ONLY these dimension names (sub-values are free-form):
  compensation, company_type, contract_form, work_culture, tech_stack, seniority, domain, role_scope

Signal types and required fields:
- ACCEPT/REJECT: type, dim, value, conf (ABSOLUTE/HIGH/MEDIUM/LOW), n_match/n_total (X matching / total in class)
- INFER: type, dim, value (inferred value), n_total (evidence count), for patterns not directly stated by user
- NEUTRAL: type, dim, dimension with no clear preference pattern

Rules:
- One signal per dimension (or per distinct value if same dim splits ACCEPT/REJECT)
- Infer dealbreakers from consistent rejections + repeated user reasons
- Emit INFER with numeric values ONLY when the number appears explicitly in the evidence
- Emit NEUTRAL for every dimension with no clear pattern, do not omit any
- LEVEL values: ABSOLUTE(100% consistent), HIGH(>80%), MEDIUM(60-80%), LOW(<60%)

Calibration example (fictional data, do not copy these values):
Input:
  APPLIED (3): all product SaaS companies, B2B contracts
  REJECTED (5): 4 outsourcing agencies ("body shop, bad rates"), 1 large enterprise ("too bureaucratic")
Expected signals:
  {type=ACCEPT, dim=company_type, value=product_saas, conf=HIGH, n_match=3, n_total=3}
  {type=REJECT, dim=company_type, value=agency_outsourcing, conf=HIGH, n_match=4, n_total=5, note="body shop, bad rates"}
  {type=REJECT, dim=company_type, value=enterprise, conf=LOW, n_match=1, n_total=5, note="too bureaucratic"}
  {type=INFER, dim=work_culture, value=async_preferred, n_total=1}
  {type=NEUTRAL, dim=compensation}
  {type=NEUTRAL, dim=contract_form}
  {type=NEUTRAL, dim=tech_stack}
  {type=NEUTRAL, dim=seniority}
  {type=NEUTRAL, dim=domain}
  {type=NEUTRAL, dim=role_scope}

Submit your analysis using the submit_profile tool.\
"""


_DESC_LIMIT = 1500
# The rejection reason is the candidate's own direct explanation, the richest
# signal in the whole corpus, so it gets the same generous limit as the
# description it explains.
_REASON_LIMIT = 400
_MAX_APPLIED = 50
_MAX_REJECTED = 50


def _job_line(job: dict, include_reason: bool = False) -> str:
    title = job.get("title", "?")
    company = job.get("company", "?")
    location = job.get("location", "")
    description = build_excerpt(job.get("description"), job.get("source")).replace("\n", " ").strip()
    job_parts = [f'"{title}" @ {company}']
    if location:
        job_parts.append(f"[{location}]")
    # JobAgentWeb's /api/jobs/feedback now includes decided_at (ujs.updated_at),
    # date-only slice is plenty of recency signal without a full datetime parse.
    decided_at = (job.get("decided_at") or "")[:10]
    if decided_at:
        job_parts.append(f"(decided {decided_at})")
    if description:
        job_parts.append(f"| {description[:_DESC_LIMIT]}")
    if include_reason:
        reason = (job.get("rejection_reason") or job.get("score_reason") or "").strip()
        if reason:
            job_parts.append(f"| reason: {reason[:_REASON_LIMIT]}")
    return " ".join(job_parts)


def _build_dismissed_section(items: list[dict]) -> str:
    if not items:
        return ""
    lines = ["DISMISSED SCORE FACTORS (candidate says these specific pros/cons don't apply to them):"]
    for it in items:
        kind = "CON" if it["item_type"] == "con" else "PRO"
        title = it.get("title", "?")
        company = it.get("company", "?")
        lines.append(
            f'  - {kind} on "{title}" @ {company}: "{it["item_text"]}", '
            f'dismissed because: "{it["reason"]}"'
        )
    return "\n\n" + "\n".join(lines)


def _build_previous_profile_section(previous_signals: list[dict] | None) -> str:
    # Feeds the previous profile back in so the model reconciles against its
    # own prior conclusions instead of re-deriving blind and drifting on
    # pure variance run-to-run.
    if not previous_signals:
        return ""
    rendered = render_signals(previous_signals)
    if not rendered.strip():
        return ""
    return (
        "\n\nYOUR PREVIOUS PROFILE (from the last distillation, reconcile with it, "
        "don't re-derive from scratch):\n" + rendered
    )


def _divergence_line(case: dict) -> str:
    if case["divergence_type"] == "false_positive":
        label = f"Ranked #{case['listwise_rank']} but rejected"
    else:
        label = f"Applied despite rank #{case['listwise_rank']}"
    return f"  {label}: {case['title']} @ {case['company']}"


def _build_prompt(
    applied: list[dict], rejected: list[dict], applied_total: int, rejected_total: int, questionnaire: str = ""
) -> str:
    # applied/rejected arrive already capped and truncated server-side, see
    # job_repository.get_all_feedback(); the _total counts are the true,
    # uncapped counts, only for the "N older omitted" messaging below.
    sections = []
    if questionnaire:
        sections.append(questionnaire)
    if applied:
        omitted = applied_total - len(applied)
        header = f"APPLIED ({applied_total} jobs, showing {len(applied)} most recent" + (f", {omitted} older omitted" if omitted else "") + "):"
        lines = [header]
        for job in applied:
            lines.append(f"  {_job_line(job)}")
        sections.append("\n".join(lines))
    else:
        sections.append("APPLIED (0 jobs): none yet")

    if rejected:
        omitted = rejected_total - len(rejected)
        header = f"REJECTED ({rejected_total} jobs, showing {len(rejected)} most recent" + (f", {omitted} older omitted" if omitted else "") + "):"
        lines = [header]
        for job in rejected:
            lines.append(f"  {_job_line(job, include_reason=True)}")
        sections.append("\n".join(lines))
    else:
        sections.append("REJECTED (0 jobs): none yet")

    return "\n\n".join(sections)


def run() -> dict:
    # applied_total/rejected_total (not the capped sample's length) drive the
    # "did anything change since last distillation" check below, since the
    # sample stays pinned at the cap forever past _MAX_APPLIED/_MAX_REJECTED.
    applied, rejected = job_repository.get_all_feedback(limit_applied=_MAX_APPLIED, limit_rejected=_MAX_REJECTED)
    stats = job_repository.get_stats()
    applied_total = stats["applied"]
    rejected_total = stats["rejected"]
    dismissed_total = dismissed_item_repository.count_all()
    if not applied_total and not rejected_total and not dismissed_total:
        logger.info("No feedback data yet, apply, reject, or dismiss a score factor first.")
        return {"ok": False, "reason": "no_data"}

    previous_profile = preference_repository.get_latest()
    if previous_profile:
        if (applied_total == previous_profile["applied_count"] and
                rejected_total == previous_profile["rejected_count"] and
                dismissed_total == (previous_profile.get("dismissed_count") or 0)):
            logger.info("No new feedback since last distillation, profile is up to date.")
            signals = previous_profile.get("signals", [])
            return {
                "ok": True,
                "reason": "no_new_data",
                "signals": signals,
                "content": render_signals(signals),
            }

    logger.info(
        f"Distilling from {applied_total} applied + {rejected_total} rejected "
        f"+ {dismissed_total} dismissed score factor(s)..."
    )
    questionnaire = load_questionnaire_preferences()
    prompt = _build_prompt(applied, rejected, applied_total, rejected_total, questionnaire)
    prompt += _build_previous_profile_section(previous_profile.get("signals") if previous_profile else None)
    prompt += _build_dismissed_section(dismissed_item_repository.get_recent(50))

    from evaluation.harness import divergence_cases
    divergences = divergence_cases()
    if divergences:
        div_lines = [_divergence_line(d) for d in divergences[:10]]
        prompt += f"\n\nRANKING DIVERGENCE (high-value signals, model ranking vs user decision):\n" + "\n".join(div_lines)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    try:
        response = client.messages.create(
            model=CLAUDE_DISTILL_MODEL,
            max_tokens=4000,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            tools=[_DISTILL_TOOL],
            tool_choice={"type": "tool", "name": "submit_profile"},
        )
    except Exception as e:
        logger.error(f"API error: {e}")
        return {"ok": False, "reason": str(e)}

    log_anthropic(response, "distiller", CLAUDE_DISTILL_MODEL)

    if response.stop_reason == "max_tokens":
        logger.error("Profile output was truncated, not saved.")
        return {"ok": False, "reason": "truncated"}

    tool_block = next((b for b in response.content if b.type == "tool_use"), None)
    if not tool_block:
        logger.error("No tool_use block in distiller response.")
        return {"ok": False, "reason": "no_tool_block"}

    signals = tool_block.input.get("signals", [])
    if not signals:
        logger.error("Distiller returned empty signals list.")
        return {"ok": False, "reason": "empty_signals"}

    # Filter out just the bad entries instead of discarding the whole (expensive)
    # response over one malformed signal out of potentially dozens.
    _VALID_TYPES = {"ACCEPT", "REJECT", "INFER", "NEUTRAL"}
    valid_signals = []
    for sig in signals:
        if sig.get("type") not in _VALID_TYPES or not sig.get("dim"):
            logger.warning(f"Dropping invalid signal in distiller response: {sig}")
        else:
            valid_signals.append(sig)

    if not valid_signals:
        logger.error("No valid signals left after filtering the distiller response.")
        return {"ok": False, "reason": "invalid_signal"}

    signals = valid_signals
    rendered = render_signals(signals)
    preference_repository.save(signals, applied_total, rejected_total, dismissed_total)
    logger.info(f"Preference profile updated ({len(signals)} signals).")
    logger.info(f"Profile:\n{rendered}")

    return {
        "ok": True,
        "signals": signals,
        "content": rendered,
        "applied_count": applied_total,
        "rejected_count": rejected_total,
    }


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    run()
