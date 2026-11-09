import json
import re
from datetime import datetime
from email.utils import parsedate_to_datetime

import httpx
from xml.etree import ElementTree as ET


_HEADERS = {"User-Agent": "JobAgent/1.0"}
_LD_JSON_PATTERN = re.compile(
    r"<script\b[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)


def fetch_rss_items(
    url: str,
    *,
    timeout: float = 15.0,
    user_agent: str = "JobAgent/1.0",
) -> list[ET.Element]:
    try:
        response = httpx.get(
            url,
            headers={"User-Agent": user_agent},
            timeout=timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except (httpx.HTTPError, ET.ParseError) as exc:
        raise RuntimeError(f"Could not fetch RSS feed {url}: {exc}") from exc
    return root.findall(".//item")


def item_text(item: ET.Element, tag: str) -> str:
    value = item.findtext(tag)
    return value.strip() if value else ""


def parse_pub_date(item: ET.Element) -> datetime | None:
    value = item_text(item, "pubDate")
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def fetch_jobposting_ld_json(
    url: str,
    *,
    timeout: float = 15.0,
) -> dict | None:
    try:
        response = httpx.get(
            url,
            headers=_HEADERS,
            timeout=timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return None

    for raw_payload in _LD_JSON_PATTERN.findall(response.text):
        try:
            payload = json.loads(raw_payload)
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("@type") == "JobPosting":
                return candidate
    return None
