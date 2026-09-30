import json
import logging
import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_EXTRACT_MODEL
from collector.utils import build_excerpt
from db.repositories import job_repository
from db.repositories.usage_repository import log_anthropic

logger = logging.getLogger(__name__)

_EXTRACT_TOOL = {
    "name": "submit_structured_data",
    "description": "Submit structured information extracted from a job description.",
    "input_schema": {
        "type": "object",
        "properties": {
            "remote":   {"type": ["boolean", "null"], "description": "Is full remote work available?"},
            "hybrid":   {"type": ["boolean", "null"], "description": "Is hybrid work available?"},
            "seniority": {
                "type": ["string", "null"],
                "enum": ["junior", "mid", "senior", "lead", "director", None],
                "description": "Expected seniority level.",
            },
            "salary_min":      {"type": ["integer", "null"], "description": "Min salary/rate, gross, in whatever period salary_period identifies (e.g. a B2B rate of '100-145 PLN/h' is salary_min=100 with salary_period='hourly', do not silently assume monthly/annual)."},
            "salary_max":      {"type": ["integer", "null"], "description": "Max salary/rate, same period as salary_min."},
            "salary_period": {
                "type": ["string", "null"],
                "enum": ["hourly", "monthly", "yearly", None],
                "description": "The pay period salary_min/salary_max are expressed in. null if not determinable from the text.",
            },
            "salary_currency": {
                "type": ["string", "null"],
                "enum": ["PLN", "EUR", "USD", "GBP", None],
            },
            "stack": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Technologies, frameworks, and tools explicitly mentioned.",
            },
            "stack_required": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Subset of `stack` explicitly stated as required/must-have (e.g. "
                    "'5 years of Kubernetes required'). Empty array if the posting "
                    "doesn't distinguish required from nice-to-have, do not guess "
                    "which items in `stack` would belong here."
                ),
            },
            "stack_preferred": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Subset of `stack` explicitly stated as nice-to-have/a plus/preferred, "
                    "not required (e.g. 'Kubernetes is a plus'). Empty array if the posting "
                    "doesn't distinguish required from nice-to-have."
                ),
            },
            "company_type": {
                "type": "string",
                "enum": ["startup", "scaleup", "enterprise", "agency", "unknown"],
            },
            "product_vs_outsourcing": {
                "type": "string",
                "enum": ["product", "outsourcing", "mixed", "unknown"],
            },
            "working_language": {
                "type": "string",
                "enum": ["polish", "english", "both", "unknown"],
            },
            "remote_regions": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Geographic regions/countries the posting explicitly says remote work is "
                    "allowed from (e.g. ['Poland'], ['EU'], ['worldwide'], ['Poland', 'Ukraine']). "
                    "Empty array if remote isn't offered (remote=false), OR if remote is offered "
                    "but the posting never states which locations are eligible, an empty array "
                    "means 'unstated', not 'nowhere', and must never be treated as a geographic "
                    "restriction by anything reading this field."
                ),
            },
            "timezone_requirement": {
                "type": ["string", "null"],
                "description": (
                    "Required working-hours timezone/overlap if explicitly stated "
                    "(e.g. 'CET ±2', 'US Eastern business hours overlap'). null if not mentioned."
                ),
            },
            "contract_types": {
                "type": "array",
                "items": {"type": "string", "enum": ["b2b", "employment", "mandate", "other"]},
                "description": (
                    "Contract type(s) offered: b2b, employment (UoP/permanent), mandate "
                    "(zlecenie/contractor), other. Empty array if not stated."
                ),
            },
        },
        "required": [
            "remote", "hybrid", "seniority",
            "salary_min", "salary_max", "salary_period", "salary_currency",
            "stack", "stack_required", "stack_preferred",
            "company_type", "product_vs_outsourcing", "working_language",
            "remote_regions", "timezone_requirement", "contract_types",
        ],
    },
}

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def extract_job(description: str, source: str | None = None) -> dict:
    excerpt = build_excerpt(description, source)[:3000]
    try:
        response = _get_client().messages.create(
            model=CLAUDE_EXTRACT_MODEL,
            max_tokens=600,
            system=(
                "Extract structured information from the job description. "
                "Use null for fields not explicitly stated, do not infer or assume."
            ),
            messages=[{"role": "user", "content": f"Extract structured data:\n\n{excerpt}"}],
            tools=[_EXTRACT_TOOL],
            tool_choice={"type": "tool", "name": "submit_structured_data"},
        )
        log_anthropic(response, "extractor", CLAUDE_EXTRACT_MODEL)

        if response.stop_reason == "max_tokens":
            # A truncated tool_use block can still parse as valid-but-partial
            # JSON; returning {} here (not the partial dict) lets
            # run_extraction()'s `if data:` guard retry the job next run
            # instead of permanently writing missing fields as null.
            logger.warning("Extraction response truncated (max_tokens), will retry next run.")
            return {}

        tool_block = next((b for b in response.content if b.type == "tool_use"), None)
        if tool_block:
            data = dict(tool_block.input)
            data["_field_confidence"] = {
                key: "medium" for key, value in data.items() if value not in (None, [], "unknown")
            }
            return data
    except Exception as e:
        logger.warning(f"Extraction failed: {e}")
    return {}


def _merge_source_structured_data(data: dict, job: dict) -> dict:
    # A source's own native fields are ground truth, not a guess from the
    # description text, so they override whatever Haiku extracted for the
    # same keys. Only overlays what the source actually provided.
    raw = job.get("source_structured_data")
    if not raw:
        return data
    try:
        source_data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return data
    merged = {**data, **source_data}
    confidence = dict(data.get("_field_confidence") or {})
    confidence.update({
        key: "high" for key, value in source_data.items()
        if key != "_field_confidence" and value not in (None, [], "unknown")
    })
    merged["_field_confidence"] = confidence
    return merged


def run_extraction(jobs: list[dict]) -> int:
    to_extract = [j for j in jobs if j.get("description") and not j.get("structured_data")]
    if not to_extract:
        return 0

    updated = 0
    for job in to_extract:
        data = extract_job(job["description"], job.get("source"))
        if data:
            data = _merge_source_structured_data(data, job)
            job_repository.update_structured_data(job["id"], data)
            logger.info(f"  Extracted: {job['title']} @ {job['company']} → {json.dumps(data, ensure_ascii=False)[:120]}")
            updated += 1

    return updated
