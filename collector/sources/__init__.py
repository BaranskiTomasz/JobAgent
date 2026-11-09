from collector.sources.linkedin import LinkedInSource
from collector.sources.theprotocol import TheProtocolSource
from collector.sources.itpracuj import ItPracujSource
from collector.sources.dou import DouSource
from collector.sources.djinni import DjinniSource

_REGISTRY: dict[str, dict] = {
    "linkedin":        {"name": "LinkedIn",          "cls": LinkedInSource},
    "theprotocol":     {"name": "theprotocol.it",    "cls": TheProtocolSource},
    "itpracuj":        {"name": "it.pracuj.pl",      "cls": ItPracujSource},
    "dou":             {"name": "DOU.ua",            "cls": DouSource},
    "djinni":          {"name": "Djinni.co",         "cls": DjinniSource},
}


def available() -> list[dict]:
    return [{"id": k, "name": v["name"]} for k, v in _REGISTRY.items()]


def make(source_id: str, **kwargs):
    entry = _REGISTRY.get(source_id)
    if not entry:
        raise ValueError(f"Unknown source: {source_id!r}")
    return entry["cls"](**kwargs)
