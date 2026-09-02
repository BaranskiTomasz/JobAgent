from collector.sources.linkedin import LinkedInSource
from collector.sources.remotive import RemotiveSource
from collector.sources.remoteok import RemoteOKSource
from collector.sources.workingnomads import WorkingNomadsSource
from collector.sources.weworkremotely import WWRSource
from collector.sources.himalayas import HimalayasSource
from collector.sources.justjoin import JustJoinSource
from collector.sources.theprotocol import TheProtocolSource
from collector.sources.itpracuj import ItPracujSource
from collector.sources.nofluffjobs import NoFluffJobsSource
from collector.sources.solidjobs import SolidJobsSource

_REGISTRY: dict[str, dict] = {
    "linkedin":        {"name": "LinkedIn",          "cls": LinkedInSource},
    "remotive":        {"name": "Remotive.io",       "cls": RemotiveSource},
    "remoteok":        {"name": "Remote OK",         "cls": RemoteOKSource},
    "workingnomads":   {"name": "Working Nomads",    "cls": WorkingNomadsSource},
    "weworkremotely":  {"name": "We Work Remotely",  "cls": WWRSource},
    "himalayas":       {"name": "Himalayas",         "cls": HimalayasSource},
    "justjoin":        {"name": "justjoin.it",       "cls": JustJoinSource},
    "theprotocol":     {"name": "theprotocol.it",    "cls": TheProtocolSource},
    "itpracuj":        {"name": "it.pracuj.pl",      "cls": ItPracujSource},
    "nofluffjobs":     {"name": "NoFluffJobs",       "cls": NoFluffJobsSource},
    "solidjobs":       {"name": "SOLID.Jobs",        "cls": SolidJobsSource},
}


def available() -> list[dict]:
    return [{"id": k, "name": v["name"]} for k, v in _REGISTRY.items()]


def make(source_id: str, **kwargs):
    entry = _REGISTRY.get(source_id)
    if not entry:
        raise ValueError(f"Unknown source: {source_id!r}")
    return entry["cls"](**kwargs)
