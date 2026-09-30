from unittest.mock import MagicMock, patch

from evaluator.profile import build_hyde_query, build_retrieval_query, load_questionnaire_preferences


def _mock_claude_response(text: str):
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    return resp


@patch("evaluator.profile.candidate_preferences_repository.get_active", return_value=None)
def test_empty_when_no_active_prefs(mock_prefs):
    assert load_questionnaire_preferences() == ""


@patch("evaluator.profile.candidate_preferences_repository.get_active", return_value={})
def test_empty_when_all_fields_blank(mock_prefs):
    assert load_questionnaire_preferences() == ""


@patch("evaluator.profile.candidate_preferences_repository.get_active")
def test_includes_header_and_work_mode(mock_prefs):
    mock_prefs.return_value = {"work_mode": ["remote", "hybrid"]}
    section = load_questionnaire_preferences()
    assert "CANDIDATE QUESTIONNAIRE" in section
    assert "Work mode: remote, hybrid" in section


@patch("evaluator.profile.candidate_preferences_repository.get_active")
def test_remote_countries_and_hybrid_cities(mock_prefs):
    mock_prefs.return_value = {
        "remote_countries": ["Poland", "Germany"],
        "hybrid_cities": ["Warsaw"],
    }
    section = load_questionnaire_preferences()
    assert "Remote must be available in: Poland, Germany" in section
    assert "OK with hybrid in: Warsaw" in section


@patch("evaluator.profile.candidate_preferences_repository.get_active")
def test_seniority_levels(mock_prefs):
    mock_prefs.return_value = {"seniority_levels": ["senior", "lead"]}
    section = load_questionnaire_preferences()
    assert "Preferred seniority level(s): senior, lead" in section


@patch("evaluator.profile.candidate_preferences_repository.get_active")
def test_all_list_fields_rendered_with_labels(mock_prefs):
    mock_prefs.return_value = {
        "role_types": ["backend"],
        "preferred_company_types": ["product"],
        "extra_tech": ["Rust"],
        "avoided_tech": ["PHP"],
    }
    section = load_questionnaire_preferences()
    assert "Desired role type(s): backend" in section
    assert "Prefers company type(s): product" in section
    assert "Also interested in: Rust" in section
    assert "Wants to avoid working with: PHP" in section


@patch("evaluator.profile.candidate_preferences_repository.get_active")
def test_removed_dead_fields_are_ignored_even_if_present(mock_prefs):
    # Regression: excluded_company_types/preferred_industries/excluded_industries
    # were removed from _LIST_FIELD_LABELS (dead, no UI ever wrote them, nothing
    # downstream ever read them). Even if a row somehow still carries a value for
    # one (e.g. a pre-existing DB row from before the field was retired), it must
    # not render, unlike the fields above, which fail this same test if their
    # rendering ever regresses.
    mock_prefs.return_value = {
        "role_types": ["backend"],
        "excluded_company_types": ["agency"],
        "preferred_industries": ["fintech"],
        "excluded_industries": ["gambling"],
    }
    section = load_questionnaire_preferences()
    assert "Desired role type(s): backend" in section
    assert "agency" not in section
    assert "fintech" not in section
    assert "gambling" not in section


@patch("evaluator.profile.candidate_preferences_repository.get_active")
def test_salary_min_with_currency(mock_prefs):
    mock_prefs.return_value = {"salary_min": 20000, "salary_currency": "PLN"}
    section = load_questionnaire_preferences()
    assert "Minimum salary: 20000 PLN" in section


@patch("evaluator.profile.candidate_preferences_repository.get_active")
def test_salary_min_without_currency_has_no_trailing_space(mock_prefs):
    mock_prefs.return_value = {"salary_min": 20000, "salary_currency": None}
    section = load_questionnaire_preferences()
    assert "Minimum salary: 20000" in section
    assert "20000 \n" not in section


@patch("evaluator.profile.candidate_preferences_repository.get_active")
def test_languages_rendered_with_cefr_level(mock_prefs):
    mock_prefs.return_value = {
        "languages": [{"language": "English", "level": "C1"}, {"language": "German", "level": "B2"}]
    }
    section = load_questionnaire_preferences()
    assert "Languages: English (C1), German (B2)" in section


@patch("evaluator.profile.candidate_preferences_repository.get_active")
def test_open_notes_quoted(mock_prefs):
    mock_prefs.return_value = {"open_notes": "I really want to work on ML infra."}
    section = load_questionnaire_preferences()
    assert 'In their own words: "I really want to work on ML infra."' in section


@patch("evaluator.profile.candidate_preferences_repository.get_active")
def test_open_notes_blank_string_omitted(mock_prefs):
    mock_prefs.return_value = {"open_notes": "   "}
    assert load_questionnaire_preferences() == ""


# --- build_retrieval_query ---

@patch("evaluator.profile.candidate_preferences_repository.get_active", return_value=None)
def test_retrieval_query_falls_back_to_cv_when_no_prefs(mock_prefs):
    assert build_retrieval_query("CANDIDATE: Senior Python dev") == "CANDIDATE: Senior Python dev"


@patch("evaluator.profile.candidate_preferences_repository.get_active", return_value={})
def test_retrieval_query_falls_back_to_cv_when_prefs_empty(mock_prefs):
    assert build_retrieval_query("CANDIDATE: Senior Python dev") == "CANDIDATE: Senior Python dev"


@patch("evaluator.profile.candidate_preferences_repository.get_active")
def test_retrieval_query_appends_positive_terms(mock_prefs):
    mock_prefs.return_value = {
        "preferred_company_types": ["product"],
        "extra_tech": ["Rust", "Kubernetes"],
    }
    query = build_retrieval_query("CANDIDATE: Senior Python dev")
    assert query.startswith("CANDIDATE: Senior Python dev")
    assert "product" in query
    assert "Rust" in query
    assert "Kubernetes" in query


@patch("evaluator.profile.candidate_preferences_repository.get_active")
def test_removed_preferred_industries_does_not_reach_the_retrieval_query(mock_prefs):
    # Regression: preferred_industries was removed from _RETRIEVAL_LIST_FIELDS
    # (dead field, never had a UI writer). Even if a row somehow still carries a
    # value, it must not leak into the retrieval query.
    mock_prefs.return_value = {"preferred_company_types": ["product"], "preferred_industries": ["fintech"]}
    query = build_retrieval_query("CANDIDATE: Senior Python dev")
    assert "product" in query
    assert "fintech" not in query


@patch("evaluator.profile.candidate_preferences_repository.get_active")
def test_retrieval_query_excludes_negative_fields(mock_prefs):
    # Regression: embeddings/cross-encoder rerank have no way to represent
    # negation from a bare word, including e.g. "PHP" from avoided_tech
    # would pull MORE PHP jobs toward the top.
    mock_prefs.return_value = {"avoided_tech": ["PHP"]}
    query = build_retrieval_query("CANDIDATE: Senior Python dev")
    assert query == "CANDIDATE: Senior Python dev"


@patch("evaluator.profile.candidate_preferences_repository.get_active")
def test_retrieval_query_includes_open_notes_as_free_text(mock_prefs):
    mock_prefs.return_value = {"open_notes": "want to work on distributed systems"}
    query = build_retrieval_query("CANDIDATE: Senior Python dev")
    assert "want to work on distributed systems" in query


@patch("evaluator.profile.candidate_preferences_repository.get_active")
def test_retrieval_query_works_with_no_cv_profile(mock_prefs):
    mock_prefs.return_value = {"extra_tech": ["Rust"]}
    query = build_retrieval_query("")
    assert "Rust" in query
    assert not query.startswith("\n")


# --- build_hyde_query ---

@patch("evaluator.profile.anthropic.Anthropic")
def test_hyde_query_returns_generated_posting(mock_anthropic):
    mock_anthropic.return_value.messages.create.return_value = _mock_claude_response(
        "Senior Backend Engineer - Product SaaS company seeking a PHP/Symfony expert..."
    )
    query = build_hyde_query("CANDIDATE: Senior PHP dev", "CANDIDATE QUESTIONNAIRE:\n- Work mode: remote")
    assert "Senior Backend Engineer" in query


@patch("evaluator.profile.anthropic.Anthropic")
def test_hyde_query_prompt_includes_profile_and_questionnaire(mock_anthropic):
    mock_anthropic.return_value.messages.create.return_value = _mock_claude_response("A posting.")
    build_hyde_query("CANDIDATE: Senior PHP dev", "CANDIDATE QUESTIONNAIRE:\n- Work mode: remote")
    prompt = mock_anthropic.return_value.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "CANDIDATE: Senior PHP dev" in prompt
    assert "Work mode: remote" in prompt


@patch("evaluator.profile.log_anthropic")
@patch("evaluator.profile.anthropic.Anthropic")
def test_hyde_query_logs_usage(mock_anthropic, mock_log):
    response = _mock_claude_response("A posting.")
    mock_anthropic.return_value.messages.create.return_value = response
    build_hyde_query("CANDIDATE: Senior PHP dev")
    mock_log.assert_called_once()
    assert mock_log.call_args.args[0] is response


@patch("evaluator.profile.candidate_preferences_repository.get_active", return_value=None)
@patch("evaluator.profile.anthropic.Anthropic")
def test_hyde_query_falls_back_to_retrieval_query_on_api_error(mock_anthropic, mock_prefs):
    mock_anthropic.return_value.messages.create.side_effect = Exception("API down")
    query = build_hyde_query("CANDIDATE: Senior PHP dev")
    assert query == "CANDIDATE: Senior PHP dev"


@patch("evaluator.profile.candidate_preferences_repository.get_active", return_value=None)
@patch("evaluator.profile.anthropic.Anthropic")
def test_hyde_query_falls_back_on_empty_response(mock_anthropic, mock_prefs):
    mock_anthropic.return_value.messages.create.return_value = _mock_claude_response("   ")
    query = build_hyde_query("CANDIDATE: Senior PHP dev")
    assert query == "CANDIDATE: Senior PHP dev"


def test_hyde_query_empty_when_nothing_to_work_with():
    assert build_hyde_query("", "") == ""
