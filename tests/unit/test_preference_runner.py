from preference_agent.runner import (
    _REASON_LIMIT, _build_prompt, _build_previous_profile_section, _job_line,
    _build_dismissed_section, _divergence_line,
)


def _job(title="Dev", company="Corp", location="Remote", description="PHP Symfony", rejection_reason=None, score_reason=None, source=None, decided_at=None):
    return {
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "rejection_reason": rejection_reason,
        "score_reason": score_reason,
        "source": source,
        "decided_at": decided_at,
    }


def test_job_line_basic():
    line = _job_line(_job("Senior PHP Dev", "AcmeCo", "Poland (Remote)"))
    assert "Senior PHP Dev" in line
    assert "AcmeCo" in line
    assert "Poland (Remote)" in line


def test_job_line_includes_reason_when_requested():
    j = _job(rejection_reason="stawka za niska")
    line = _job_line(j, include_reason=True)
    assert "stawka za niska" in line


def test_job_line_no_reason_without_flag():
    j = _job(rejection_reason="stawka za niska")
    line = _job_line(j, include_reason=False)
    assert "stawka za niska" not in line


def test_job_line_does_not_treat_score_reason_as_user_feedback():
    j = _job(rejection_reason=None, score_reason="too junior")
    line = _job_line(j, include_reason=True)
    assert "too junior" not in line


def test_job_line_reason_not_truncated_below_400_chars():
    # Regression: the rejection reason, the candidate's own direct, human-written
    # explanation, the richest signal in the whole corpus, used to be cut to 120
    # chars, more aggressively than the job description it explains (1500 chars).
    long_reason = "x" * 300
    j = _job(rejection_reason=long_reason)
    line = _job_line(j, include_reason=True)
    assert long_reason in line


def test_job_line_reason_still_truncated_at_reason_limit():
    long_reason = "x" * (_REASON_LIMIT + 100)
    j = _job(rejection_reason=long_reason)
    line = _job_line(j, include_reason=True)
    assert "x" * _REASON_LIMIT in line
    assert "x" * (_REASON_LIMIT + 1) not in line


def test_job_line_includes_decided_at_date():
    # Regression: examples had no date at all, so a 6-month-old decision counted
    # identically to yesterday's, no way to express "this pattern reversed".
    j = _job(decided_at="2026-07-15T10:23:45.123456")
    line = _job_line(j)
    assert "decided 2026-07-15" in line


def test_job_line_omits_decided_at_when_missing():
    j = _job(decided_at=None)
    line = _job_line(j)
    assert "decided" not in line


def test_system_prompt_explains_recency_weighting():
    from preference_agent.runner import _SYSTEM
    assert "decided" in _SYSTEM.lower()
    assert "recent" in _SYSTEM.lower()


# --- _build_previous_profile_section (task #53) ---

def test_previous_profile_section_empty_when_none():
    assert _build_previous_profile_section(None) == ""


def test_previous_profile_section_empty_when_empty_list():
    assert _build_previous_profile_section([]) == ""


def test_previous_profile_section_includes_neutral_signals():
    # Unlike evaluator/scorer.py's LEARNED PREFERENCE PROFILE section (which drops
    # NEUTRAL, irrelevant to scoring), the distiller needs its own prior NEUTRAL
    # conclusions too: "I already checked this dimension, found no pattern" is
    # exactly the kind of prior state reconciliation is meant to preserve or revise.
    signals = [{"type": "NEUTRAL", "dim": "contract_form"}, {"type": "NEUTRAL", "dim": "domain"}]
    section = _build_previous_profile_section(signals)
    assert "NEUTRAL[contract_form" in section
    assert "NEUTRAL[domain" in section


def test_previous_profile_section_renders_signals():
    signals = [{"type": "ACCEPT", "dim": "company_type", "value": "product", "conf": "HIGH", "n_match": 3, "n_total": 3}]
    section = _build_previous_profile_section(signals)
    assert "YOUR PREVIOUS PROFILE" in section
    assert "ACCEPT[company_type=product" in section


def test_system_prompt_instructs_reconciliation_not_re_derivation():
    from preference_agent.runner import _SYSTEM
    assert "PREVIOUS PROFILE" in _SYSTEM
    assert "reconcile" in _SYSTEM.lower()


def test_build_prompt_applied_section():
    applied = [_job("SWE", "BigCo", "USA (Remote)")]
    prompt = _build_prompt(applied, [], 1, 0)
    assert "APPLIED (1 jobs, showing 1 most recent)" in prompt
    assert "SWE" in prompt
    assert "BigCo" in prompt


def test_build_prompt_rejected_section():
    rejected = [_job("Junior Dev", "AgencyCo", rejection_reason="stawka za niska")]
    prompt = _build_prompt([], rejected, 0, 1)
    assert "REJECTED (1 jobs" in prompt
    assert "Junior Dev" in prompt
    assert "stawka za niska" in prompt


def test_build_prompt_applied_empty():
    prompt = _build_prompt([], [_job("X", "Y")], 0, 1)
    assert "APPLIED (0 jobs)" in prompt


def test_build_prompt_rejected_empty():
    prompt = _build_prompt([_job("X", "Y")], [], 1, 0)
    assert "REJECTED (0 jobs)" in prompt


def test_job_line_strips_linkedin_junk():
    # Regression guard: _job_line used to slice job["description"] raw, letting
    # LinkedIn's page-chrome junk pollute the preference-distillation prompt.
    j = _job(description="Real content about the role.\nSet alert for similar jobs\nUnrelated junk.", source="linkedin")
    line = _job_line(j)
    assert "Real content about the role." in line
    assert "Unrelated junk" not in line


def test_build_prompt_description_not_truncated():
    long_desc = "PHP " * 200  # 800 chars
    prompt = _build_prompt([_job(description=long_desc)], [], 1, 0)
    assert long_desc.strip() in prompt


def test_build_prompt_shows_older_omitted_count_when_total_exceeds_sample():
    # Capping now happens upstream (job_repository.get_all_feedback's limit_*
    # params, enforced server-side), _build_prompt just renders whatever sample
    # it's given plus the true total, so the "N older omitted" messaging is a
    # function of (total - len(sample)), not of any cap _build_prompt applies itself.
    rejected = [_job(title=f"Job {i}") for i in range(50)]
    prompt = _build_prompt([], rejected, 0, rejected_total=55)
    assert "Job 0" in prompt
    assert "showing 50 most recent" in prompt
    assert "5 older omitted" in prompt


def test_build_prompt_no_omitted_message_when_sample_covers_everything():
    rejected = [_job(title="Job 0")]
    prompt = _build_prompt([], rejected, 0, rejected_total=1)
    assert "older omitted" not in prompt


def test_build_prompt_applied_also_shows_older_omitted():
    # Regression: only REJECTED used to get the "showing N most recent, M older
    # omitted" treatment, APPLIED had no cap at all before this fix.
    applied = [_job(title=f"Job {i}") for i in range(50)]
    prompt = _build_prompt(applied, [], applied_total=53, rejected_total=0)
    assert "APPLIED (53 jobs, showing 50 most recent, 3 older omitted)" in prompt


def test_build_dismissed_section_empty_returns_empty_string():
    assert _build_dismissed_section([]) == ""


def test_build_dismissed_section_formats_con():
    items = [{"item_type": "con", "item_text": "UK-based, timezone concern", "reason": "not an issue for me",
              "title": "Backend Dev", "company": "AcmeCo"}]
    section = _build_dismissed_section(items)
    assert "CON" in section
    assert "UK-based, timezone concern" in section
    assert "not an issue for me" in section
    assert "Backend Dev" in section
    assert "AcmeCo" in section


def test_build_dismissed_section_formats_pro():
    items = [{"item_type": "pro", "item_text": "Fully remote", "reason": "actually I prefer hybrid",
              "title": "Dev", "company": "Corp"}]
    section = _build_dismissed_section(items)
    assert "PRO" in section


def test_divergence_line_false_positive():
    case = {"divergence_type": "false_positive", "listwise_rank": 2, "title": "Overrated Job", "company": "AcmeCo"}
    assert _divergence_line(case) == "  Ranked #2 but rejected: Overrated Job @ AcmeCo"


def test_divergence_line_false_negative():
    case = {"divergence_type": "false_negative", "listwise_rank": 18, "title": "Hidden Gem", "company": "Corp"}
    assert _divergence_line(case) == "  Applied despite rank #18: Hidden Gem @ Corp"


def test_build_prompt_includes_questionnaire_when_given():
    prompt = _build_prompt([], [], 0, 0, questionnaire="CANDIDATE QUESTIONNAIRE:\n- Work mode: remote")
    assert "CANDIDATE QUESTIONNAIRE" in prompt
    assert "Work mode: remote" in prompt


def test_build_prompt_no_questionnaire_section_when_omitted():
    prompt = _build_prompt([], [], 0, 0)
    assert "CANDIDATE QUESTIONNAIRE" not in prompt


def test_build_prompt_questionnaire_appears_before_applied_section():
    applied = [_job("SWE", "BigCo")]
    prompt = _build_prompt(applied, [], 1, 0, questionnaire="CANDIDATE QUESTIONNAIRE:\n- Work mode: remote")
    assert prompt.index("CANDIDATE QUESTIONNAIRE") < prompt.index("APPLIED")


def test_system_prompt_no_longer_bans_all_geo_signal():
    # Regression: the distiller used to unconditionally forbid learning any
    # location/remote/geography/visa signal, claiming it was "filtered
    # upstream", that wasn't true until the P3 geo dealbreaker, and even now
    # only covers the narrow remote+country case, not timezone/visa/hybrid-city
    # nuance.
    from preference_agent.runner import _SYSTEM
    assert "Do NOT include: location, remote/on-site, geography, visa" not in _SYSTEM
    assert "timezone" in _SYSTEM.lower()
    assert "visa" in _SYSTEM.lower()


def test_system_prompt_instructs_treating_questionnaire_as_ground_truth():
    from preference_agent.runner import _SYSTEM
    assert "QUESTIONNAIRE" in _SYSTEM
    assert "ground truth" in _SYSTEM.lower()
