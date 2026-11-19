import io
import json

import PyPDF2
import anthropic
from flask import Blueprint, jsonify, request

import api_client
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from db.repositories import cv_repository, criteria_repository
from db.repositories.usage_repository import log_anthropic

bp = Blueprint("cv", __name__)

_PARSE_PROMPT = """Extract structured information from this CV/resume.
Respond ONLY with JSON, no other text:
{
    "stack": ["list of technologies, languages, frameworks"],
    "years_experience": <integer or null>,
    "seniority": "Junior|Mid|Senior|Lead|Principal",
    "location": "city, country",
    "remote_preference": "fully remote|hybrid|on-site|flexible",
    "raw_summary": "2-3 sentence summary of the candidate"
}"""


def _extract_text(file_bytes: bytes) -> str:
    reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


MAX_CV_TEXT_CHARS = 60_000  # ~15k tokens; no real CV (even multi-page) comes close to this —
# it's a backstop against a degenerate PDF extraction or a mistakenly-uploaded large
# document, not a real content limit.


def _parse_with_claude(raw_text: str) -> dict:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,  # raised from 600: a full (untruncated) CV can yield a longer
        # stack list and summary, and a truncated reply breaks json.loads() downstream
        messages=[{"role": "user", "content": f"{_PARSE_PROMPT}\n\nCV text:\n{raw_text[:MAX_CV_TEXT_CHARS]}"}],
    )
    log_anthropic(response, "cv_parse", CLAUDE_MODEL)
    raw = response.content[0].text.strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)


@bp.get("/api/cv")
def list_profiles():
    return jsonify(cv_repository.list_all())


@bp.get("/api/cv/active")
def active_profile():
    return jsonify(cv_repository.get_active())


@bp.post("/api/cv/upload")
def upload_cv():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files["file"]
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF files are supported"}), 400
    try:
        raw_text = _extract_text(file.read())
    except Exception as e:
        return jsonify({"error": f"Failed to read PDF: {e}"}), 400
    if not raw_text.strip():
        return jsonify({"error": "Could not extract text from PDF (may be a scanned image, use a text-based PDF)"}), 400
    try:
        parsed = _parse_with_claude(raw_text)
    except Exception as e:
        return jsonify({"error": f"Failed to parse CV with Claude: {e}"}), 500
    id_ = cv_repository.insert(file.filename, raw_text, parsed)
    return jsonify({"ok": True, "id": id_, "parsed": parsed})


@bp.post("/api/cv/<int:id>/activate")
def activate_profile(id):
    try:
        cv_repository.set_active(id)
    except api_client.ApiError as e:
        return jsonify({"error": e.detail}), e.status_code
    return jsonify({"ok": True})


_SUGGEST_PROMPT = """Based on this candidate profile, suggest LinkedIn job search criteria.
Respond ONLY with JSON, no other text:
{{
    "search_queries": ["1-3 broad search terms used as LinkedIn search queries, wide enough to catch relevant jobs, narrow enough to avoid noise"],
    "titles": ["5-8 specific job titles used for scoring fit, what the candidate actually wants"],
    "locations": ["2-4 locations or regions to search in"],
    "required": ["3-6 must-have keywords, core technologies the candidate knows well and won't compromise on"],
    "preferred": ["4-8 nice-to-have keywords, technologies, methodologies, or traits the candidate likes but aren't dealbreakers"]
}}

Rules:
- search_queries: broad LinkedIn search terms, e.g. "PHP developer", "Backend developer", "Software Engineer". These are used to query LinkedIn, NOT for scoring. 1-3 terms max.
- titles: specific job titles for scoring fit (e.g. "Senior PHP Developer", "Symfony Engineer"). These are NOT used as search queries.
- For remote-friendly candidates, include "Remote" as a location; for on-site/hybrid use their city and country
- Required: pick the candidate's strongest, most central technologies (language, main framework)
- Preferred: pick secondary tools, methodologies (e.g. Docker, CI/CD, microservices, Agile), or domain keywords

Candidate profile:
{profile}"""


def _suggest_with_claude(parsed: dict) -> dict:
    profile_lines = [
        f"Seniority: {parsed.get('seniority', 'not specified')}",
        f"Years of experience: {parsed.get('years_experience', 'not specified')}",
        f"Stack: {', '.join(parsed.get('stack') or []) or 'not specified'}",
        f"Location: {parsed.get('location', 'not specified')}",
        f"Remote preference: {parsed.get('remote_preference', 'not specified')}",
    ]
    summary = (parsed.get("raw_summary") or "").strip()
    if summary:
        profile_lines.append(f"Summary: {summary}")
    profile_text = "\n".join(profile_lines)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content": _SUGGEST_PROMPT.format(profile=profile_text)}],
    )
    log_anthropic(response, "cv_suggest_criteria", CLAUDE_MODEL)
    raw = response.content[0].text.strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)


@bp.post("/api/cv/<int:id>/suggest-criteria")
def suggest_criteria(id):
    profile = cv_repository.get_active() if id == 0 else next(
        (p for p in cv_repository.list_all() if p["id"] == id), None
    )
    if not profile:
        return jsonify({"error": "CV profile not found"}), 404
    try:
        suggestions = _suggest_with_claude(profile["parsed"])
    except Exception as e:
        return jsonify({"error": f"Failed to generate suggestions: {e}"}), 500
    return jsonify(suggestions)


@bp.post("/api/cv/<int:id>/apply-criteria")
def apply_criteria(id):
    data = request.get_json(force=True)
    search_queries = data.get("search_queries") or []
    titles         = data.get("titles") or []
    locations      = data.get("locations") or []
    required       = data.get("required") or []
    preferred      = data.get("preferred") or []
    if not any([search_queries, titles, locations, required, preferred]):
        return jsonify({"error": "No criteria provided"}), 400
    for v in search_queries: criteria_repository.insert("search_query", v)
    for v in titles:         criteria_repository.insert("title",        v)
    for v in locations:      criteria_repository.insert("location",     v)
    for v in required:       criteria_repository.insert("required",     v)
    for v in preferred:      criteria_repository.insert("preferred",    v)
    return jsonify({
        "ok": True,
        "added_search_queries": len(search_queries),
        "added_titles": len(titles), "added_locations": len(locations),
        "added_required": len(required), "added_preferred": len(preferred),
    })
