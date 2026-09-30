import json
import logging

import anthropic
from flask import Blueprint, jsonify, request

from config import ANTHROPIC_API_KEY, CLAUDE_EXTRACT_MODEL
from db.repositories import candidate_preferences_repository, criteria_repository, cv_repository
from db.repositories.usage_repository import log_anthropic

logger = logging.getLogger(__name__)

bp = Blueprint("candidate_preferences", __name__)

_QUERY_DERIVE_PROMPT = """Based on this candidate's confirmed tech stack, role, and seniority,
generate a short list of search phrases to use across multiple job boards (LinkedIn,
justjoin.it, NoFluffJobs, and others).

Guidelines learned from real testing on LinkedIn:
- A bare technology/language name alone (e.g. "PHP", "Python", "Go") usually finds more
  relevant postings than a compound phrase, and also matches non-English postings
  (e.g. "PHP-Entwickler").
- Named frameworks that don't share their language's name need their own phrase with
  "Developer" (e.g. "Symfony Developer", "Django Developer", "Laravel Developer",
  "FastAPI Developer", "Flask Developer", "React Developer").
- Skip generic infrastructure/database/tooling names as standalone search terms
  (e.g. Docker, PostgreSQL, Redis, Git, CI/CD), they rarely appear as job titles.
- Keep the final list short: 4-10 phrases total, no duplicates.

Respond ONLY with a JSON array of strings, no other text.

Candidate tech: {tech}
Role types: {roles}
Seniority: {seniority}"""


def _derive_search_queries(tech: list[str], role_types: list[str], seniority_levels: list[str]) -> list[str]:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = _QUERY_DERIVE_PROMPT.format(
        tech=", ".join(tech) or "not specified",
        roles=", ".join(role_types) or "not specified",
        seniority=", ".join(seniority_levels) or "not specified",
    )
    response = client.messages.create(
        model=CLAUDE_EXTRACT_MODEL,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    log_anthropic(response, "derive_search_queries", CLAUDE_EXTRACT_MODEL)
    raw = response.content[0].text.strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    queries = json.loads(raw)
    return [q.strip() for q in queries if isinstance(q, str) and q.strip()][:10]


def _replace_criteria(type_: str, values: list[str]) -> None:
    # These types are system-managed from the questionnaire now, not
    # hand-curated, so stale entries from a previous save shouldn't linger.
    criteria_repository.delete_by_type(type_)
    for v in values:
        criteria_repository.insert(type_, v)


def _sync_criteria_from_preferences(fields: dict) -> dict:
    # Keeps the collector's search inputs (title/location criteria) in sync
    # with the questionnaire, since the user no longer edits these directly.
    warnings = []

    tech = fields.get("extra_tech") or []
    roles = fields.get("role_types") or []
    seniority = fields.get("seniority_levels") or []
    try:
        queries = _derive_search_queries(tech, roles, seniority)
    except Exception as e:
        logger.warning(f"Search-query derivation failed: {e}")
        queries = []
        warnings.append("Could not auto-generate search queries, try saving again.")
    if queries:
        _replace_criteria("title", queries)
        _replace_criteria("search_query", [])  # titles must win over any stale search_query rows

    # Locations are literal user-typed values, not an LLM derivation, replace
    # unconditionally (including clearing to empty) so switching work modes doesn't
    # leave stale entries from a previous save driving the search.
    # Remote postings are matched by country; hybrid/onsite postings are matched by
    # city only, both feed the same "location" criteria used across every source.
    work_mode = fields.get("work_mode") or []
    locations = []
    if "remote" in work_mode:
        if fields.get("work_country"):
            locations.append(fields["work_country"])
        else:
            locations += fields.get("remote_countries") or []
    if "hybrid" in work_mode or "onsite" in work_mode:
        locations += fields.get("hybrid_cities") or []
    _replace_criteria("location", locations)

    # Unconditional (unlike titles above): these are literal user-typed values,
    # not an LLM derivation that can silently fail, clearing all avoid-chips should clear
    # the rejected keywords too.
    _replace_criteria("rejected", fields.get("avoided_tech") or [])

    # Deliberately not touching "required" here: a populated required-tech
    # list could reject the entire pool for a candidate whose stack includes
    # something like C#/C++/.NET if extraction ever mismatches a single
    # entry, so required stays CV-derived-only, left for the user to set
    # explicitly.
    _replace_criteria("preferred", fields.get("extra_tech") or [])

    return {"warnings": warnings}


@bp.get("/api/candidate-preferences")
def get_preferences():
    return jsonify(candidate_preferences_repository.get_active() or {})


@bp.post("/api/candidate-preferences")
def save_preferences():
    fields = request.get_json(force=True) or {}
    active_cv = cv_repository.get_active()
    cv_profile_id = active_cv["id"] if active_cv else None

    try:
        id_ = candidate_preferences_repository.insert(cv_profile_id, fields)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    sync_result = _sync_criteria_from_preferences(fields)
    return jsonify({"ok": True, "id": id_, **sync_result})
