import json
import logging

from db.repositories import candidate_preferences_repository, job_repository
# Reused rather than duplicated: this is exactly the candidate's working-level
# language codes (>=B2), the same computation collector/language_filter.py already
# does to filter by *posting* language. _working_language_reason below needs the
# same set to check the *company's* working language instead.
from collector.language_filter import _candidate_language_codes

logger = logging.getLogger(__name__)


def _structured_data(job: dict) -> dict:
    raw = job.get("structured_data")
    if not raw:
        return {}
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return {}


def _trusted_fields(data: dict) -> dict:
    confidence = data.get("_field_confidence") or {}
    return {key: value for key, value in data.items() if confidence.get(key) == "high"}


# Standard full-time B2B assumption for converting an hourly rate to its annual
# equivalent: 168 hours/month (21 working days x 8h) x 12 months.
_HOURS_PER_YEAR = 168 * 12
_MONTHS_PER_YEAR = 12


def _annualize(amount: int, period: str | None) -> int | None:
    # The candidate's own salary_min is always annual, so job pay must be
    # normalized to the same basis before comparing. Returns None when the
    # period is unknown rather than guessing.
    if period == "yearly":
        return amount
    if period == "monthly":
        return amount * _MONTHS_PER_YEAR
    if period == "hourly":
        return amount * _HOURS_PER_YEAR
    return None


# Static, approximate mid-rates to PLN, not live-fetched: this filter only
# needs to be directionally right, not payroll-accurate.
_TO_PLN_RATE = {"PLN": 1.0, "EUR": 4.3, "USD": 4.0, "GBP": 5.0}


def _to_pln(amount: int, currency: str | None) -> int | None:
    rate = _TO_PLN_RATE.get(currency)
    return round(amount * rate) if rate is not None else None


def _salary_floor_reason(job_structured: dict, salary_min: int | None, salary_currency: str | None) -> str | None:
    if not salary_min:
        return None
    job_max = job_structured.get("salary_max")
    job_currency = job_structured.get("salary_currency")
    if not job_max or not job_currency or not salary_currency:
        return None
    annual_job_max = _annualize(job_max, job_structured.get("salary_period"))
    if annual_job_max is None:
        return None  # unknown pay period, never guess a basis, skip rather than false-reject

    # Always compare in PLN, even when both currencies already match (rate=1.0,
    # a no-op), one path instead of a same-currency/converted branch split.
    job_max_pln = _to_pln(annual_job_max, job_currency)
    candidate_min_pln = _to_pln(salary_min, salary_currency)
    if job_max_pln is None or candidate_min_pln is None:
        return None  # unsupported currency, skip rather than guess

    if job_max_pln < candidate_min_pln:
        period = job_structured.get("salary_period")
        conversion_note = "" if job_currency == "PLN" else f" (~{job_max_pln} PLN)"
        return (
            f"Dealbreaker: salary_max {job_max} {job_currency}/{period} "
            f"(~{annual_job_max} {job_currency}/year{conversion_note}) below your minimum {salary_min} {salary_currency}/year"
        )
    return None


def _remote_only_reason(job_structured: dict, work_mode: list[str]) -> str | None:
    if work_mode != ["remote"]:
        return None
    remote = job_structured.get("remote")
    hybrid = job_structured.get("hybrid")
    if remote is None and hybrid is None:
        return None  # no data, never treat absence as a violation
    # hybrid=True always disqualifies a remote-only candidate, even if remote=True is
    # also set on the same posting, confirmed with the candidate directly: hybrid
    # means occasional required office days, which breaks a remote-only requirement
    # regardless of whether the posting also advertises a remote option/policy.
    if hybrid is True or remote is False:
        return "Dealbreaker: requires on-site/hybrid presence, but you selected remote-only"
    return None


def _geo_reason(job_structured: dict, work_mode: list[str], work_countries: list[str]) -> str | None:
    # A job can be genuinely remote but still restricted to countries that
    # don't include the candidate's (e.g. "Remote, US only" reaching a
    # candidate in Poland). Conservative like the checks below: any
    # missing/unclear signal skips rather than rejects, since remote_regions
    # is a new field and older jobs have no such key at all.
    if "remote" not in work_mode:
        return None
    if not work_countries:
        return None  # candidate didn't say which countries matter, nothing to check
    if job_structured.get("remote") is not True:
        return None  # not a remote posting at all, a different dealbreaker's job
    job_regions = job_structured.get("remote_regions")
    if not job_regions:
        return None  # unstated (or pre-migration row), never treat as a restriction

    from collector.location import location_matches
    # Trailing space lets a bare "EU" match location.py's "eu " Europe token the
    # same way real free-text job locations do, this list is otherwise just
    # joined country/region names, not a sentence.
    job_location_text = ", ".join(job_regions) + " "
    if any(location_matches(job_location_text, country) for country in work_countries):
        return None
    return (
        f"Dealbreaker: remote work restricted to {', '.join(job_regions)}, "
        f"not available from where you work ({', '.join(work_countries)})"
    )


def _seniority_reason(job_structured: dict, seniority_levels: list[str]) -> str | None:
    if not seniority_levels:
        return None  # candidate didn't restrict, nothing to check
    job_seniority = job_structured.get("seniority")
    if not job_seniority:
        return None  # unknown, never treat absence as a violation
    if job_seniority in seniority_levels:
        return None
    return (
        f"Dealbreaker: seniority level '{job_seniority}' not in your selected levels "
        f"({', '.join(seniority_levels)})"
    )


# Maps a questionnaire preferred_company_types chip value (web/templates/questionnaire.html's
# "company-group") to the job-side structured-data axis that can confirm/deny it. The two
# vocabularies don't line up 1:1: the questionnaire's "product" and "consulting" chips are
# really asking about extractor/runner.py's product_vs_outsourcing axis, not its separate
# company_type enum (startup/scaleup/enterprise/agency/unknown), "consulting" has no exact
# company_type match either, so it's treated as either an agency or an outsourcing engagement.
def _tri_or(*values: bool | None) -> bool | None:
    # OR over tri-state values: True if any is confirmed True, False only if
    # every value is confirmed False, None if nothing is True but at least
    # one is unknown.
    if any(v is True for v in values):
        return True
    if any(v is None for v in values):
        return None
    return False


def _matches_preferred_company_type(preferred_type: str, company_type: str | None, outsourcing: str | None) -> bool | None:
    # None if unknown or inconclusive: product_vs_outsourcing's "mixed" is a
    # genuine partial match, not evidence either way, so it fails open too.
    if preferred_type == "product":
        if not outsourcing or outsourcing in ("unknown", "mixed"):
            return None
        return outsourcing == "product"
    if preferred_type == "consulting":
        is_agency = None if not company_type or company_type == "unknown" else company_type == "agency"
        is_outsourcing = None
        if outsourcing and outsourcing not in ("unknown", "mixed"):
            is_outsourcing = outsourcing == "outsourcing"
        return _tri_or(is_agency, is_outsourcing)
    # startup / scaleup / enterprise map directly onto company_type
    if not company_type or company_type == "unknown":
        return None
    return company_type == preferred_type


def _company_type_reason(job_structured: dict, preferred_company_types: list[str]) -> str | None:
    # A real hard filter, per questionnaire.html's own copy. Rejects only when
    # at least one selected type is positively known not to match; a job
    # matching ANY selected type survives (chip selection is OR, not AND).
    if not preferred_company_types:
        return None
    company_type = job_structured.get("company_type")
    outsourcing = job_structured.get("product_vs_outsourcing")

    results = [_matches_preferred_company_type(t, company_type, outsourcing) for t in preferred_company_types]
    if True in results:
        return None
    if all(r is None for r in results):
        return None  # no axis had enough info to confirm a mismatch for any selected type
    return (
        f"Dealbreaker: company type doesn't match your selected preference(s) "
        f"({', '.join(preferred_company_types)})"
    )


# extractor/runner.py's working_language enum -> collector/language_filter.py's
# language codes. Only the two unambiguous single-language values are enforced;
# "both" and "unknown" skip, same fail-open rule as everywhere else in this file.
_WORKING_LANGUAGE_CODES = {"polish": "pl", "english": "en"}


def _working_language_reason(job_structured: dict, candidate_codes: set[str]) -> str | None:
    # collector/language_filter.py only detects the posting text's language,
    # so an English-language posting at a company that works in Polish sails
    # through untouched; working_language closes that gap.
    if not candidate_codes:
        return None
    working_language = job_structured.get("working_language")
    code = _WORKING_LANGUAGE_CODES.get(working_language)
    if not code:
        return None  # "both", "unknown", or missing, nothing to enforce
    if code in candidate_codes:
        return None
    return f"Dealbreaker: company works in {working_language}, which isn't among your working-level languages"


def _no_salary_disclosed_reason(job_structured: dict, show_jobs_without_salary: bool) -> str | None:
    if show_jobs_without_salary:
        return None
    if job_structured.get("salary_max") or job_structured.get("salary_min"):
        return None  # salary IS disclosed, not this check's concern
    return "Dealbreaker: no salary disclosed, and you chose to hide postings without salary listed"


def apply_dealbreaker_filter(jobs: list[dict]) -> tuple[list[dict], dict]:
    # Deterministic, pre-LLM hard filter, zero LLM cost for the jobs it catches.
    prefs = candidate_preferences_repository.get_active()
    if not prefs:
        return jobs, {"checked": len(jobs), "auto_rejected": 0}

    salary_min = prefs.get("salary_min")
    salary_currency = prefs.get("salary_currency")
    work_mode = prefs.get("work_mode") or []
    work_country = (prefs.get("work_country") or "").strip()
    remote_countries = [work_country] if work_country else (prefs.get("remote_countries") or [])
    required_seniority = prefs.get("required_seniority_levels")
    seniority_levels = (
        prefs.get("seniority_levels") or []
        if required_seniority is None
        else required_seniority
    )
    required_company_types = prefs.get("required_company_types")
    preferred_company_types = (
        prefs.get("preferred_company_types") or []
        if required_company_types is None
        else required_company_types
    )
    # Unset (predates this field) defaults to True, matching the questionnaire
    # checkbox's own default (checked), never surprise an existing candidate
    # with newly-hidden postings just because this key doesn't exist yet.
    raw_show_no_salary = prefs.get("show_jobs_without_salary")
    show_jobs_without_salary = True if raw_show_no_salary is None else bool(raw_show_no_salary)
    candidate_language_codes = _candidate_language_codes()

    if (
        not salary_min and "remote" not in work_mode and not seniority_levels
        and not preferred_company_types and show_jobs_without_salary and not candidate_language_codes
    ):
        return jobs, {"checked": len(jobs), "auto_rejected": 0}

    surviving = []
    auto_rejected = 0

    for job in jobs:
        structured = _structured_data(job)
        trusted = _trusted_fields(structured)
        reason = _salary_floor_reason(trusted, salary_min, salary_currency)
        if not reason:
            reason = _remote_only_reason(trusted, work_mode)
        if not reason:
            reason = _geo_reason(trusted, work_mode, remote_countries)
        if not reason:
            reason = _seniority_reason(trusted, seniority_levels)
        if not reason:
            reason = _company_type_reason(trusted, preferred_company_types)
        if not reason:
            reason = _working_language_reason(trusted, candidate_language_codes)
        if not reason and "_salary_disclosed" in trusted:
            reason = _no_salary_disclosed_reason(trusted, show_jobs_without_salary)

        if reason:
            job_repository.update_score_and_status(job["id"], 0.0, reason, "auto_rejected")
            auto_rejected += 1
            logger.info(f"  [dealbreaker] {job['title']} @ {job['company']}, {reason}")
        else:
            surviving.append(job)

    return surviving, {"checked": len(jobs), "auto_rejected": auto_rejected}
