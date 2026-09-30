import hashlib
import json
from pathlib import Path

from config import CLAUDE_MODEL, CLAUDE_RANK_MODEL, VOYAGE_EMBED_MODEL, VOYAGE_RERANK_MODEL


_ROOT = Path(__file__).resolve().parent.parent


def _source_digest(paths: list[str]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update((_ROOT / path).read_bytes())
    return digest.hexdigest()


def _digest(value: dict) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def score_fingerprint(job: dict, system_prompt: str) -> str:
    return _digest({
        "model": CLAUDE_MODEL,
        "implementation": _source_digest(["evaluator/scorer.py", "collector/utils.py"]),
        "system_prompt": system_prompt,
        "job": {
            "title": job.get("title"),
            "company": job.get("company"),
            "location": job.get("location"),
            "description": job.get("description"),
            "source": job.get("source"),
            "structured_data": job.get("structured_data"),
        },
    })


def ranking_fingerprint(
    jobs: list[dict], candidate_profile: str, questionnaire: str, preferences: list[dict]
) -> str:
    return _digest({
        "models": [CLAUDE_RANK_MODEL, CLAUDE_MODEL, VOYAGE_EMBED_MODEL, VOYAGE_RERANK_MODEL],
        "implementation": _source_digest([
            "ranker/listwise.py", "ranker/debate.py", "ranker/reranker.py",
            "ranker/fusion.py", "evaluator/profile.py", "collector/utils.py",
        ]),
        "candidate_profile": candidate_profile,
        "questionnaire": questionnaire,
        "preferences": preferences,
        "jobs": [{
            "id": job.get("id"),
            "title": job.get("title"),
            "company": job.get("company"),
            "location": job.get("location"),
            "description": job.get("description"),
            "source": job.get("source"),
            "posted_at": job.get("posted_at"),
            "structured_data": job.get("structured_data"),
            "score": job.get("score"),
            "score_reason": job.get("score_reason"),
            "score_breakdown": job.get("score_breakdown"),
        } for job in sorted(jobs, key=lambda item: item["id"])],
    })
