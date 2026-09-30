"""
End-to-end pipeline tests: E2 keyword filter + E3 Claude scoring.

Run with:  pytest -m e2e
Skipped unless a real ANTHROPIC_API_KEY is set in the environment.

Test scenarios:
  E2-1: LinkedIn job with no required keywords (PHP/remote absent) → auto_rejected
  E2-2: RemoteOK job containing a rejected keyword (intern)        → auto_rejected
  E3-1: LinkedIn perfect match (Senior PHP/Symfony, remote, B2B)   → score >= 7
  E3-2: LinkedIn poor match (agency, on-site, legacy, low pay)     → score <= 4
  E3-3: RemoteOK perfect match (Senior PHP/Symfony, EU remote, B2B)→ score >= 7
  E3-4: RemoteOK poor match (staffing agency, outstaffing model)   → score <= 4
"""
import os
import uuid

import pytest

from db.repositories import job_repository, criteria_repository, cv_repository
from collector.filters import apply_keyword_filter
from evaluator.runner import run as run_evaluator


# ---------------------------------------------------------------------------
# Skip the entire module when running without a real API key
# ---------------------------------------------------------------------------
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("ANTHROPIC_API_KEY", "test-").startswith("test-"),
        reason="Requires real ANTHROPIC_API_KEY",
    ),
]

# job_postings is shared and never truncated, keeps urls from colliding across runs.
_RUN_ID = uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------
_CV_PARSED = {
    "seniority": "Senior",
    "years_experience": 8,
    "stack": ["PHP", "Symfony", "Docker", "PostgreSQL", "Redis"],
    "location": "Poland",
    "remote_preference": "fully remote",
    "raw_summary": (
        "Backend engineer with 8 years in PHP/Symfony, building scalable SaaS platforms. "
        "Prefers product companies, async remote culture, B2B contracts. "
        "Strong with Docker, PostgreSQL, clean architecture. "
        "Avoids agencies, outstaffing, on-site roles, and legacy WordPress/WooCommerce work."
    ),
}

_CRITERIA = {
    "required": ["PHP", "remote"],
    "preferred": ["Symfony", "Docker", "PostgreSQL", "B2B", "startup"],
    "rejected": ["junior", "stażysta", "intern", "internship"],
    "location": ["Europe", "Poland", "Remote"],
    "search_query": ["PHP Developer"],
}

# E2-1: no "php" or "remote" anywhere in title or description → fails required check
_JOB_E2_NO_REQUIRED_KEYWORDS = dict(
    title="Python Django Backend Developer",
    company="DataSoft Ltd",
    location="Europe",
    url=f"https://test.example/e2-no-required-{_RUN_ID}",
    source="linkedin",
    description="""\
DataSoft builds data processing pipelines for fintech clients across Europe.

Stack: Python 3.11, Django 4.2, PostgreSQL, Redis, Celery
Team: 12 people, distributed async culture, work from anywhere in Europe

Requirements:
- 3+ years Django
- PostgreSQL, data modeling, REST APIs
- Celery task queues, AWS or GCP experience a plus

Compensation: 8,000–12,000 EUR/month B2B
""",
)

# E2-2: contains "intern" (rejected keyword); "PHP" and "remote" also present so only
# the rejected-keyword check triggers (runs before required check)
_JOB_E2_REJECTED_KEYWORD = dict(
    title="PHP Developer Intern",
    company="CodeLearn Academy",
    location="Remote (Europe)",
    url=f"https://test.example/e2-rejected-keyword-{_RUN_ID}",
    source="remoteok",
    description="""\
CodeLearn Academy offers a 3-month PHP internship program for students and fresh graduates.

This intern position is designed to help you start your career in web development.
- Learn PHP and Symfony basics under senior supervision
- Build small features, remote work, flexible hours
- Internship certificate upon completion

Unpaid. Perfect for building your portfolio. Open to students and recent graduates.
""",
)

# E3-1: Senior PHP/Symfony, product company, fully remote, B2B, async, competitive pay
_JOB_E3_LINKEDIN_GREAT = dict(
    title="Senior PHP/Symfony Backend Developer",
    company="TechFlow Software",
    location="Poland / Europe (fully remote)",
    url=f"https://test.example/e3-linkedin-great-{_RUN_ID}",
    source="linkedin",
    description="""\
TechFlow is a Warsaw-based product company building a SaaS invoicing platform
used by 5,000+ SMEs across Europe. We are 100% remote, async-first, no mandatory meetings.

Role:
- Design and build new features in PHP 8.3 / Symfony 7
- Own microservice components end-to-end, from DB schema to API
- Code reviews, architecture discussions with a 5-person backend team

Requirements:
- 5+ years PHP with solid Symfony experience
- Docker, PostgreSQL, Redis
- English fluent, comfortable working async

We offer:
- Fully remote position (European timezone)
- 18,000–22,000 PLN net B2B
- Truly async culture, no daily standups, no tracking
- Startup environment, direct product ownership
""",
)

# E3-2: Web agency, WordPress, on-site Warsaw, legacy code, low pay
# Note: "no remote option" contains "remote" so E2 passes (OR: "PHP" also present)
_JOB_E3_LINKEDIN_POOR = dict(
    title="PHP Developer",
    company="DevAgency Warsaw",
    location="Warsaw, Poland (on-site)",
    url=f"https://test.example/e3-linkedin-poor-{_RUN_ID}",
    source="linkedin",
    description="""\
We are a busy web agency looking for a PHP developer to join our team.
You will build WordPress and WooCommerce sites for our clients.

Daily work:
- Build WordPress themes and plugins for various clients
- Maintain legacy PHP 5.6/7.0 codebases
- On-site at Warsaw office Monday–Friday, overtime expected at deadlines

Requirements:
- 1–2 years PHP experience
- WordPress, WooCommerce knowledge
- Must be available full-time on-site, no remote option

Salary: 4,500–6,000 PLN gross, employment contract only (no B2B).
""",
)

# E3-3: Senior PHP/Symfony, product company, worldwide remote, B2B, async
_JOB_E3_REMOTEOK_GREAT = dict(
    title="Senior PHP Developer (Symfony, remote)",
    company="CloudBase GmbH",
    location="Worldwide Remote",
    url=f"https://test.example/e3-remoteok-great-{_RUN_ID}",
    source="remoteok",
    description="""\
CloudBase is a German product company building cloud infrastructure tooling.
100% remote, async-first team of 8 backend engineers.

You will:
- Own and extend our PHP 8.2 / Symfony 6 REST API
- Design PostgreSQL schemas and write migrations
- Deploy via Docker and Kubernetes
- Work independently with minimal supervision

Stack: PHP, Symfony, PostgreSQL, Docker, Redis, RabbitMQ
Contract: B2B only, 100% remote worldwide
Compensation: 5,000–7,000 EUR/month

No daily standups, no micromanagement. You own your work.
""",
)

# E3-4: IT staffing/outstaffing agency, legacy PHP, rotated to client sites, unclear pay
_JOB_E3_REMOTEOK_POOR = dict(
    title="PHP Developer - Remote",
    company="StaffingGroup International",
    location="Remote",
    url=f"https://test.example/e3-remoteok-poor-{_RUN_ID}",
    source="remoteok",
    description="""\
StaffingGroup is a global IT staffing and outstaffing provider.
We place PHP developers at client sites worldwide.

Current opening: PHP developer to be assigned to various client projects
(primarily legacy system maintenance and small feature work for clients).

- PHP, any version, basic to advanced OOP
- All experience levels considered
- Rotated across multiple client projects throughout the year
- On-site at client location may be required when client requests it
- Employment (UoP) or B2B available

Salary: 7,000–9,000 PLN gross, depends on client and project.
Outstaffing model, you will work for our clients, not for us directly.
""",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def pipeline_fixtures():
    cv_repository.insert("test_cv.pdf", "Senior PHP developer CV", _CV_PARSED)
    for criteria_type, values in _CRITERIA.items():
        for value in values:
            criteria_repository.insert(criteria_type, value)


def _get_job_score(job_id: str) -> float:
    jobs = {j["id"]: j for j in job_repository.search()}
    return jobs[job_id]["score"]


def _get_job_status(job_id: str) -> str:
    jobs = {j["id"]: j for j in job_repository.search()}
    return jobs[job_id]["status"]


# ---------------------------------------------------------------------------
# E2 filter tests
# ---------------------------------------------------------------------------
class TestE2Filter:
    def test_job_without_required_keywords_gets_auto_rejected(self):
        # Missing every 'required' keyword (php, remote) in both title and description
        # is a hard rejection, see collector/filters.py::apply_keyword_filter.
        job_id = job_repository.insert(**_JOB_E2_NO_REQUIRED_KEYWORDS)
        result = apply_keyword_filter()
        assert result["auto_rejected"] == 1
        assert _get_job_status(job_id) == "auto_rejected"

    def test_rejects_job_with_rejected_keyword(self):
        job_id = job_repository.insert(**_JOB_E2_REJECTED_KEYWORD)
        result = apply_keyword_filter()
        assert result["auto_rejected"] == 1
        assert _get_job_status(job_id) == "auto_rejected"


# ---------------------------------------------------------------------------
# E3 scoring tests
# ---------------------------------------------------------------------------
class TestE3Scoring:
    def test_great_linkedin_job_scores_high(self):
        job_id = job_repository.insert(**_JOB_E3_LINKEDIN_GREAT)
        run_evaluator()
        score = _get_job_score(job_id)
        assert score >= 7, f"Expected score >= 7 for great LinkedIn job, got {score}"

    def test_poor_linkedin_job_scores_low(self):
        job_id = job_repository.insert(**_JOB_E3_LINKEDIN_POOR)
        run_evaluator()
        score = _get_job_score(job_id)
        assert score <= 4, f"Expected score <= 4 for poor LinkedIn job, got {score}"

    def test_great_remoteok_job_scores_high(self):
        job_id = job_repository.insert(**_JOB_E3_REMOTEOK_GREAT)
        run_evaluator()
        score = _get_job_score(job_id)
        assert score >= 7, f"Expected score >= 7 for great RemoteOK job, got {score}"

    def test_poor_remoteok_job_scores_low(self):
        job_id = job_repository.insert(**_JOB_E3_REMOTEOK_POOR)
        run_evaluator()
        score = _get_job_score(job_id)
        assert score <= 4, f"Expected score <= 4 for poor RemoteOK job, got {score}"
