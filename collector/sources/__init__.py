from collector.sources.linkedin import LinkedInSource
from collector.sources.remotive import RemotiveSource
from collector.sources.remoteok import RemoteOKSource
from collector.sources.workingnomads import WorkingNomadsSource
from collector.sources.weworkremotely import WWRSource
from collector.sources.himalayas import HimalayasSource
from collector.sources.jobicy import JobicySource
from collector.sources.jobscollider import JobsColliderSource
from collector.sources.arbeitnow import ArbeitnowSource, ArbeitnowUKSource
from collector.sources.greenhouse import GreenhouseSource
from collector.sources.lever import LeverSource
from collector.sources.ashby import AshbySource
from collector.sources.hackernews import HackerNewsSource
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
    "jobicy":          {"name": "Jobicy",            "cls": JobicySource},
    "jobscollider":    {"name": "JobsCollider",      "cls": JobsColliderSource},
    "arbeitnow":       {"name": "Arbeitnow Europe", "cls": ArbeitnowSource},
    "arbeitnow_uk":    {"name": "Arbeitnow UK",     "cls": ArbeitnowUKSource},
    "greenhouse":      {"name": "Greenhouse",       "cls": GreenhouseSource},
    "lever":           {"name": "Lever",            "cls": LeverSource},
    "ashby":           {"name": "Ashby",            "cls": AshbySource},
    "hackernews":      {"name": "HN Who's Hiring", "cls": HackerNewsSource},
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
