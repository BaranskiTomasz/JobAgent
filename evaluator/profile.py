import logging

import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_EXTRACT_MODEL
from db.repositories import candidate_preferences_repository, cv_repository
from db.repositories.usage_repository import log_anthropic

logger = logging.getLogger(__name__)

# Fields worth folding into a semantic retrieval query, the ones with real
# positive lexical/semantic overlap against job description text. Deliberately
# excludes avoided_tech: embeddings and cross-encoder rerank have no way to
# represent negation from a bare word in a query, including it would just
# pull MORE of what it's meant to avoid toward the top, the opposite of intent.
# open_notes is included as full free text (not list terms) since a real
# sentence like "not interested in gambling companies" gives the embedding
# model actual negation context that a bare listed word can't.
_RETRIEVAL_LIST_FIELDS = ["role_types", "preferred_company_types", "employer_countries", "extra_tech"]

# Maps a candidate_preferences list field to its rendered label, order here is
# the order they appear in the prompt.
_LIST_FIELD_LABELS = [
    ("role_types", "Desired role type(s)"),
    ("preferred_company_types", "Prefers company type(s)"),
    ("employer_countries", "Prefers employers from"),
    ("extra_tech", "Also interested in"),
    ("avoided_tech", "Wants to avoid working with"),
]


def load_active_profile() -> str:
    """Return the active CV profile as a formatted prompt string.
    Raises ValueError if no profile has been uploaded yet."""
    profile = cv_repository.get_active()
    if not profile:
        raise ValueError(
            "No active CV profile found. Upload your CV via the dashboard → CV tab."
        )
    parsed = profile["parsed"]
    stack = ", ".join(parsed.get("stack") or []) or "(not specified)"
    lines = [
        "CANDIDATE:",
        f"- {parsed.get('seniority', 'Engineer')}, {parsed.get('years_experience', '?')}+ years experience",
        f"- Stack: {stack}",
        f"- Location: {parsed.get('location', 'not specified')}",
        f"- Remote preference: {parsed.get('remote_preference', 'fully remote')}",
    ]
    summary = (parsed.get("raw_summary") or "").strip()
    if summary:
        lines.append(f"- {summary}")
    return "\n".join(lines)


def load_questionnaire_preferences() -> str:
    # Distinct from load_active_profile() (parsed CV facts) and the learned
    # preference profile (inferred from applied/rejected history): the
    # candidate's own direct, current answers. Restates fields already
    # enforced as hard dealbreakers too, since that filter skips on
    # missing/unclear structured data and a job it let through can still
    # carry the relevant info in free text only the LLM will catch.
    prefs = candidate_preferences_repository.get_active()
    if not prefs:
        return ""

    lines = []

    work_mode = prefs.get("work_mode") or []
    if work_mode:
        lines.append(f"- Work mode: {', '.join(work_mode)}")
    work_country = prefs.get("work_country")
    if work_country:
        lines.append(f"- Will perform remote work from: {work_country}")
    elif prefs.get("remote_countries"):
        lines.append(f"- Remote must be available in: {', '.join(prefs['remote_countries'])}")
    if prefs.get("hybrid_cities"):
        lines.append(f"- OK with hybrid in: {', '.join(prefs['hybrid_cities'])}")
    if prefs.get("seniority_levels"):
        lines.append(f"- Preferred seniority level(s): {', '.join(prefs['seniority_levels'])}")
    if prefs.get("required_seniority_levels"):
        lines.append(f"- Required seniority level(s): {', '.join(prefs['required_seniority_levels'])}")
    if prefs.get("required_company_types"):
        lines.append(f"- Required company type(s): {', '.join(prefs['required_company_types'])}")

    for field, label in _LIST_FIELD_LABELS:
        if prefs.get(field):
            lines.append(f"- {label}: {', '.join(prefs[field])}")

    salary_min = prefs.get("salary_min")
    if salary_min:
        currency = prefs.get("salary_currency") or ""
        lines.append(f"- Minimum salary: {salary_min} {currency}".rstrip())

    languages = prefs.get("languages") or []
    lang_str = ", ".join(
        f"{lang.get('language')} ({lang.get('level')})" for lang in languages if lang.get("language")
    )
    if lang_str:
        lines.append(f"- Languages: {lang_str}")

    open_notes = (prefs.get("open_notes") or "").strip()
    if open_notes:
        lines.append(f'- In their own words: "{open_notes}"')

    if not lines:
        return ""
    return "CANDIDATE QUESTIONNAIRE (their own stated preferences):\n" + "\n".join(lines)


def build_retrieval_query(candidate_profile: str) -> str:
    # Combines the CV profile with a compact questionnaire summary, dense and
    # unlabeled unlike load_questionnaire_preferences() above: rerank_jobs
    # truncates its query to _MAX_QUERY_CHARS, so every character here should
    # be a real retrieval term, not prose structure.
    prefs = candidate_preferences_repository.get_active()
    if not prefs:
        return candidate_profile

    terms = []
    for field in _RETRIEVAL_LIST_FIELDS:
        terms.extend(prefs.get(field) or [])
    open_notes = (prefs.get("open_notes") or "").strip()
    if open_notes:
        terms.append(open_notes)

    if not terms:
        return candidate_profile

    suffix = "Preferences: " + ", ".join(terms)
    return f"{candidate_profile}\n{suffix}" if candidate_profile else suffix


_HYDE_PROMPT = """Based on this candidate's profile and stated preferences, write a single
realistic job posting describing their IDEAL job, as if it were a real ad on a job board,
not a description of the candidate. Write it in job-posting genre and voice: a job title,
a short company blurb, a few bullet points of responsibilities, a few bullet points of
requirements (matching their actual stack/seniority), and any preferences they stated
(company type, industry, salary floor, work mode) folded in naturally as if the posting
itself offers them. Keep it under 200 words. Output ONLY the posting text, no preamble,
no markdown headers.

{candidate_profile}

{questionnaire}"""


def build_hyde_query(candidate_profile: str, questionnaire: str = "") -> str:
    # Generates a synthetic "ideal job posting" via a cheap LLM call and uses
    # it as the retrieval query instead of raw CV text: a CV is written in
    # resume voice (what the candidate has done), while postings are written
    # in ad voice (what's being offered), so embedding a CV to retrieve
    # postings systematically under-weights good-fit postings phrased nothing
    # like a CV (HyDE, Gao et al. 2022). Falls back to build_retrieval_query()
    # on any API failure or empty response.
    if not candidate_profile and not questionnaire:
        return ""

    prompt = _HYDE_PROMPT.format(candidate_profile=candidate_profile, questionnaire=questionnaire)
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=CLAUDE_EXTRACT_MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        log_anthropic(response, "hyde_query", CLAUDE_EXTRACT_MODEL)
        text = response.content[0].text.strip()
        if not text:
            raise ValueError("empty HyDE response")
        return text
    except Exception as e:
        logger.warning(f"HyDE query generation failed: {e}, falling back to CV/preferences text")
        return build_retrieval_query(candidate_profile)
