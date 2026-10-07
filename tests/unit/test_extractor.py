import json
from unittest.mock import MagicMock, patch

from extractor.runner import (
    _EXTRACT_TOOL,
    _catalog_gate_rejects,
    _merge_source_structured_data,
    _normalize_facts,
    extract_catalog_gate,
    extract_job,
    run_extraction,
)


def _make_tool_response(data: dict, stop_reason="tool_use"):
    block = MagicMock()
    block.type = "tool_use"
    block.input = data
    response = MagicMock()
    response.content = [block]
    response.stop_reason = stop_reason
    return response


def _make_empty_response():
    response = MagicMock()
    response.content = []
    response.stop_reason = "end_turn"
    return response


@patch("extractor.runner._get_client")
def test_extract_job_returns_parsed_dict(mock_get_client):
    payload = {
        "remote": True, "hybrid": False, "seniority": "senior",
        "salary_min": 15000, "salary_max": 20000, "salary_currency": "PLN",
        "stack": ["Python", "Django"], "company_type": "startup",
        "product_vs_outsourcing": "product", "working_language": "english",
    }
    mock_get_client.return_value.messages.create.return_value = _make_tool_response(payload)

    result = extract_job("Senior Python Developer, remote, 15-20k PLN")
    assert result["remote"] is True
    assert result["seniority"] == "senior"
    assert "Python" in result["stack"]


@patch("extractor.runner._get_client")
def test_extract_job_caches_static_tool_schema(mock_get_client):
    mock_get_client.return_value.messages.create.return_value = _make_tool_response({})

    extract_job("Senior Python Developer, remote")

    request = mock_get_client.return_value.messages.create.call_args.kwargs
    assert request["tools"][0]["cache_control"] == {"type": "ephemeral"}
    assert request["max_tokens"] == 6000


class TestExtractionSchema:
    # Regression coverage for the audit's biggest single schema gap: `remote`
    # was a bare boolean with no geo information, so nothing in the pipeline
    # could ever tell "remote, Poland only" from "remote, US only" apart.
    _NEW_FIELDS = {
        "remote_regions", "timezone_requirement", "contract_types",
        "stack_required", "stack_preferred",
    }

    def test_new_fields_present_in_schema(self):
        props = _EXTRACT_TOOL["input_schema"]["properties"]
        assert self._NEW_FIELDS <= props.keys()

    def test_new_fields_are_required_so_the_model_always_considers_them(self):
        # "required" here means "must appear in the tool call", nullable/empty-
        # array fields still satisfy it, this just stops the model from silently
        # omitting the field rather than explicitly saying "unstated".
        required = set(_EXTRACT_TOOL["input_schema"]["required"])
        assert self._NEW_FIELDS <= required

    def test_remote_regions_and_stack_tiers_are_plain_string_arrays(self):
        props = _EXTRACT_TOOL["input_schema"]["properties"]
        for field in ("remote_regions", "stack_required", "stack_preferred"):
            assert props[field]["type"] == "array"

    def test_timezone_requirement_is_nullable_string(self):
        assert _EXTRACT_TOOL["input_schema"]["properties"]["timezone_requirement"]["type"] == ["string", "null"]

    @patch("extractor.runner._get_client")
    def test_new_fields_round_trip_through_extract_job(self, mock_get_client):
        payload = {
            "remote": True, "hybrid": False, "seniority": "senior",
            "salary_min": None, "salary_max": None, "salary_period": None, "salary_currency": None,
            "stack": ["Kubernetes", "Docker"], "stack_required": ["Kubernetes"], "stack_preferred": ["Docker"],
            "company_type": "startup", "product_vs_outsourcing": "product", "working_language": "english",
            "remote_regions": ["Poland", "Ukraine"], "timezone_requirement": "CET ±2", "contract_types": ["b2b"],
        }
        mock_get_client.return_value.messages.create.return_value = _make_tool_response(payload)

        result = extract_job("Senior Kubernetes role, remote from Poland/Ukraine, CET +-2h, B2B")
        assert result["remote_regions"] == ["Poland", "Ukraine"]
        assert result["timezone_requirement"] == "CET ±2"
        assert result["contract_types"] == ["b2b"]
        assert result["stack_required"] == ["Kubernetes"]
        assert result["stack_preferred"] == ["Docker"]
        assert result["company_type"] == "startup"


@patch("extractor.runner._get_client")
def test_extract_job_returns_empty_on_api_error(mock_get_client):
    mock_get_client.return_value.messages.create.side_effect = Exception("API error")
    result = extract_job("Some job description")
    assert result == {}


@patch("extractor.runner._get_client")
def test_extract_job_returns_empty_when_no_tool_block(mock_get_client):
    mock_get_client.return_value.messages.create.return_value = _make_empty_response()
    result = extract_job("Description without tool response")
    assert result == {}


@patch("extractor.runner._get_client")
def test_extract_job_returns_empty_on_truncated_response(mock_get_client):
    # Regression: a truncated tool_use block can still parse as valid-but-
    # partial JSON, without an explicit stop_reason check, run_extraction()'s
    # `if data:` guard would treat a truthy-but-incomplete dict as a final,
    # complete extraction and never retry the missing fields.
    payload = {"remote": True}  # as if cut off mid-object
    mock_get_client.return_value.messages.create.return_value = _make_tool_response(payload, stop_reason="max_tokens")
    result = extract_job("Some job description")
    assert result == {}


@patch("extractor.runner._get_client")
def test_extract_job_strips_linkedin_junk_before_extraction(mock_get_client):
    # Regression guard: extract_job used to send description[:3000] raw, so LinkedIn's
    # page-chrome junk could land inside the extraction window for short postings.
    mock_get_client.return_value.messages.create.return_value = _make_tool_response({})
    description = "Senior Python Developer, remote.\nSet alert for similar jobs\nUnrelated footer junk."

    extract_job(description, source="linkedin")

    sent_content = mock_get_client.return_value.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Senior Python Developer" in sent_content
    assert "Unrelated footer junk" not in sent_content


@patch("extractor.runner._get_client")
def test_extract_job_non_linkedin_source_unaffected(mock_get_client):
    mock_get_client.return_value.messages.create.return_value = _make_tool_response({})
    extract_job("Clean posting text", source="remotive")

    sent_content = mock_get_client.return_value.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Clean posting text" in sent_content


@patch("extractor.runner.job_repository")
@patch("extractor.runner.extract_job")
def test_run_extraction_skips_jobs_without_description(mock_extract, mock_repo):
    jobs = [{"id": "j1", "title": "Dev", "company": "Co", "description": None, "structured_data": None}]
    count = run_extraction(jobs)
    assert count == 0
    mock_extract.assert_not_called()


@patch("extractor.runner.job_repository")
@patch("extractor.runner.extract_job")
def test_run_extraction_replaces_legacy_extraction(mock_extract, mock_repo):
    mock_extract.return_value = {"remote": True}
    jobs = [{
        "id": "j1", "title": "Dev", "company": "Co",
        "description": "desc", "structured_data": '{"remote": true}',
    }]
    count = run_extraction(jobs)
    assert count == 1
    mock_extract.assert_called_once()


@patch("extractor.runner.job_repository")
@patch("extractor.runner.extract_job")
def test_run_extraction_calls_update_on_success(mock_extract, mock_repo):
    mock_extract.return_value = {"remote": True, "stack": ["Python"]}
    jobs = [{"id": "j1", "title": "Dev", "company": "Co", "description": "desc", "structured_data": None}]
    count = run_extraction(jobs)
    assert count == 1
    mock_repo.update_facts.assert_called_once()


@patch("extractor.runner.job_repository")
@patch("extractor.runner.extract_job")
def test_run_extraction_passes_job_source_through(mock_extract, mock_repo):
    mock_extract.return_value = {}
    jobs = [{"id": "j1", "title": "Dev", "company": "Co", "source": "linkedin", "description": "desc", "structured_data": None}]
    run_extraction(jobs)
    mock_extract.assert_called_once_with("desc", "linkedin")


@patch("extractor.runner.job_repository")
@patch("extractor.runner.extract_job")
def test_run_extraction_returns_zero_when_extract_returns_empty(mock_extract, mock_repo):
    mock_extract.return_value = {}
    jobs = [{"id": "j1", "title": "Dev", "company": "Co", "description": "desc", "structured_data": None}]
    count = run_extraction(jobs)
    assert count == 0


@patch("extractor.runner.job_repository")
@patch("extractor.runner.extract_catalog_gate", return_value={})
@patch("extractor.runner.extract_job")
def test_run_extraction_accepts_non_object_evidence(mock_extract, mock_gate, mock_repo):
    mock_extract.return_value = {
        "summary": "A role summary.", "remote": True, "evidence": "Remote in Europe",
    }
    jobs = [{"id": "j1", "title": "Dev", "company": "Co", "description": "desc"}]

    assert run_extraction(jobs, catalog=True) == 1
    provenance = mock_repo.update_facts.call_args.args[5]
    assert provenance["remote"]["evidence"] is None


@patch("extractor.runner.job_repository")
@patch("extractor.runner.extract_catalog_gate", return_value={})
@patch("extractor.runner.extract_job")
def test_run_extraction_continues_after_one_malformed_job(mock_extract, mock_gate, mock_repo):
    mock_extract.side_effect = [
        {"skills": ["invalid"]},
        {"summary": "Valid summary", "remote": True},
    ]
    jobs = [
        {"id": "bad", "title": "Bad", "company": "Co", "description": "bad"},
        {"id": "good", "title": "Good", "company": "Co", "description": "good"},
    ]

    assert run_extraction(jobs, catalog=True) == 1
    assert mock_repo.update_facts.call_args.args[0] == "good"


def test_catalog_gate_rejects_only_explicit_ineligibility():
    assert _catalog_gate_rejects({"remote": False}) is True
    assert _catalog_gate_rejects({"remote": True, "hybrid": True}) is True
    assert _catalog_gate_rejects({"country_eligibility": [
        {"country_code": "PL", "eligible": False},
        {"country_code": "BG", "eligible": False},
    ]}) is True
    assert _catalog_gate_rejects({"remote": True, "country_eligibility": [
        {"country_code": "PL", "eligible": None},
        {"country_code": "BG", "eligible": False},
    ]}) is False


@patch("extractor.runner.job_repository")
@patch("extractor.runner.extract_job")
@patch("extractor.runner.extract_catalog_gate")
def test_catalog_gate_skips_full_extraction_for_explicitly_ineligible_job(mock_gate, mock_extract, mock_repo):
    mock_gate.return_value = {
        "remote": True,
        "hybrid": False,
        "remote_regions": ["United States"],
        "country_eligibility": [
            {"country_code": "PL", "eligible": False, "engagement_modes": ["unknown"], "evidence": "US only"},
            {"country_code": "BG", "eligible": False, "engagement_modes": ["unknown"], "evidence": "US only"},
        ],
    }
    jobs = [{"id": "us", "title": "Engineer", "company": "Co", "description": "Remote US only"}]

    assert run_extraction(jobs, catalog=True) == 1
    mock_extract.assert_not_called()
    saved = mock_repo.update_facts.call_args.args[4]
    assert saved["_extraction_tier"] == "catalog_gate"


@patch("extractor.runner.job_repository")
@patch("extractor.runner.extract_job")
@patch("extractor.runner.extract_catalog_gate")
def test_catalog_gate_runs_full_extraction_when_eligibility_is_unknown(mock_gate, mock_extract, mock_repo):
    mock_gate.return_value = {"remote": True, "hybrid": False, "remote_regions": []}
    mock_extract.return_value = {"remote": True, "hybrid": False, "summary": "Remote role"}
    jobs = [{"id": "unknown", "title": "Engineer", "company": "Co", "description": "Remote"}]

    assert run_extraction(jobs, catalog=True) == 1
    mock_extract.assert_called_once()
    saved = mock_repo.update_facts.call_args.args[4]
    assert saved["_extraction_tier"] == "full"


class TestMergeSourceStructuredData:
    # A source's own native fields (e.g. justjoin.it's salary/skills API
    # fields) are ground truth, not a guess from the description text.

    def test_no_source_data_returns_haiku_data_unchanged(self):
        data = {"remote": True, "salary_min": 15000}
        job = {"source_structured_data": None}
        assert _merge_source_structured_data(data, job) == data


def test_normalize_facts_builds_legacy_stack_and_salary_fields():
    result = _normalize_facts({
        "skills": [{
            "canonical_name": "Node.js", "original_name": "Node.js",
            "requirement": "required", "importance": "core", "confidence": 0.8,
        }],
        "stack": [], "stack_required": [], "stack_preferred": [],
        "compensation_bands": [{
            "amount_min": 100, "amount_max": 140, "currency": "PLN",
            "period": "hourly", "compensation_type": "base",
        }],
    })
    assert result["stack"] == ["nodejs"]
    assert result["stack_required"] == ["nodejs"]
    assert result["salary_max"] == 140
    assert result["salary_period"] == "hourly"


def test_normalize_facts_removes_unknown_summary_sentinel():
    assert _normalize_facts({"summary": "<UNKNOWN>"})["summary"] == ""


def test_normalize_facts_derives_poland_and_bulgaria_from_eu_remote():
    result = _normalize_facts({
        "remote": True, "remote_regions": ["European Union"], "contract_types": ["b2b"],
    })
    assert result["country_eligibility"] == [
        {"country_code": "PL", "eligible": True, "engagement_modes": ["b2b"], "confidence": 0.9, "evidence": "European Union"},
        {"country_code": "BG", "eligible": True, "engagement_modes": ["b2b"], "confidence": 0.9, "evidence": "European Union"},
    ]


def test_normalize_facts_keeps_unspecified_remote_country_unknown():
    result = _normalize_facts({"remote": True, "remote_regions": ["Remote"]})
    assert [item["eligible"] for item in result["country_eligibility"]] == [None, None]


def test_normalize_facts_expands_eu_region_and_does_not_store_it_as_country():
    result = _normalize_facts({
        "remote": True,
        "country_eligibility": [{
            "country_code": "EU", "eligible": True, "engagement_modes": ["employment"],
            "confidence": 0.8, "evidence": "Remote in the EU",
        }],
    })
    assert {item["country_code"] for item in result["country_eligibility"]} == {"PL", "BG"}
    assert all(item["eligible"] is True for item in result["country_eligibility"])

def test_source_data_overrides_matching_keys():
    data = {"remote": True, "salary_min": None, "salary_max": None}
    job = {"source_structured_data": json.dumps({"salary_min": 15000, "salary_max": 20000})}
    result = _merge_source_structured_data(data, job)
    assert result["salary_min"] == 15000
    assert result["salary_max"] == 20000
    assert result["remote"] is True


def test_source_data_as_dict_not_json_string_also_works():
    data = {"remote": True}
    job = {"source_structured_data": {"salary_min": 15000}}
    result = _merge_source_structured_data(data, job)
    assert result["salary_min"] == 15000


def test_unparseable_source_data_falls_back_to_haiku_only():
    data = {"remote": True}
    job = {"source_structured_data": "not json"}
    assert _merge_source_structured_data(data, job) == data


@patch("extractor.runner.job_repository")
@patch("extractor.runner.extract_job")
def test_run_extraction_merges_source_structured_data_over_haiku_output(mock_extract, mock_repo):
    # Regression: justjoin.it discloses salary as a structured API field,
    # Haiku's own guess from the description text must not win over it.
    mock_extract.return_value = {"remote": True, "salary_min": None, "salary_max": None}
    jobs = [{
        "id": "j1", "title": "Dev", "company": "Co", "description": "desc", "structured_data": None,
        "source_structured_data": json.dumps({"salary_min": 15000, "salary_max": 20000, "salary_currency": "PLN"}),
    }]
    run_extraction(jobs)
    saved = mock_repo.update_facts.call_args.args[4]
    assert saved["salary_min"] == 15000
    assert saved["salary_max"] == 20000
    assert saved["remote"] is True
