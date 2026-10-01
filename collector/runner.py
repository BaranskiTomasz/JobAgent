import sys
import random
import time
import logging
import math
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

from config import STEALTH
from db.repositories import (
    candidate_preferences_repository, criteria_repository, excluded_search_queries_repository,
    job_repository, search_stats_repository, session_repository,
)
from collector.filters import apply_keyword_filter, title_banned_reason
from collector.language_filter import apply_language_filter
from collector.sources import available as available_sources, make as make_source
from collector.query_planner import order_queries, source_queries
from collector.query_matcher import job_matches_query
from collector.sources.linkedin import LinkedInSource


def _fetch_one(source, job_id: str, url: str, source_id: str) -> bool:
    """Fetch a single pending description, retrying once on failure. Returns True on
    success; marks the job auto_rejected (listing gone) on a second failure."""
    desc = source.fetch_description(url)
    if desc:
        job_repository.update_description(job_id, desc)
        logger.info(f"  OK: {url.rstrip('/').split('/')[-1]}")
        return True

    logger.info(f"  Retry: {url.rstrip('/').split('/')[-1]}")
    time.sleep(random.uniform(30, 60))
    desc = source.fetch_description(url)
    if desc:
        job_repository.update_description(job_id, desc)
        logger.info("  Retry OK")
        return True

    logger.info(f"  Failed (unavailable): {url}")
    job_repository.update_score_and_status(job_id, 0.0, f"Job listing no longer available on {source_id}", "auto_rejected")
    return False


def _fetch_descriptions_stealthily(jobs: list[tuple[str, str]]) -> tuple[int, int]:
    """LinkedIn-specific path: batches with stealth pauses and periodic distraction
    between them, a fresh browser session per batch."""
    batch_size = STEALTH["batch_size"]
    distract_every = STEALTH["distract_every_n_batches"]
    batches = [jobs[i:i + batch_size] for i in range(0, len(jobs), batch_size)]

    logger.info(f"\nFetching {len(jobs)} LinkedIn description(s) in {len(batches)} batch(es) of ≤{batch_size}...")

    ok_total = fail_total = 0
    for batch_idx, batch in enumerate(batches):
        if batch_idx > 0:
            pause = random.uniform(STEALTH["batch_pause_min"], STEALTH["batch_pause_max"])
            resume = (datetime.now() + timedelta(seconds=pause)).strftime("%H:%M")
            logger.info(f"\n[stealth] Pausing {pause / 60:.1f} min before batch {batch_idx + 1}/{len(batches)}... → resume ~{resume}")
            time.sleep(pause)

        logger.info(f"\n--- Batch {batch_idx + 1}/{len(batches)} ({len(batch)} jobs) ---")

        with LinkedInSource() as source:
            source.login()
            if distract_every > 0 and batch_idx > 0 and batch_idx % distract_every == 0:
                source.distract()

            ok = fail = 0
            for job_id, url in batch:
                if _fetch_one(source, job_id, url, "linkedin"):
                    ok += 1
                else:
                    fail += 1

        logger.info(f"  Batch {batch_idx + 1} done: {ok} OK, {fail} failed")
        ok_total += ok
        fail_total += fail

    return ok_total, fail_total


def _fetch_descriptions_directly(source_id: str, jobs: list[tuple[str, str]]) -> tuple[int, int]:
    """Non-LinkedIn path: no login, no stealth pacing needed, one session, straight
    through the list."""
    logger.info(f"\nFetching {len(jobs)} {source_id} description(s)...")
    try:
        source = make_source(source_id)
    except ValueError as e:
        logger.warning(f"Can't fetch pending descriptions for {source_id!r}: {e}")
        return 0, len(jobs)

    ok = fail = 0
    with source:
        for job_id, url in jobs:
            if _fetch_one(source, job_id, url, source_id):
                ok += 1
            else:
                fail += 1
    return ok, fail


def _fetch_descriptions_in_batches(jobs_pending_description: list[tuple[str, str, str]]) -> None:
    by_source: dict[str, list[tuple[str, str]]] = {}
    for job_id, url, source_id in jobs_pending_description:
        by_source.setdefault(source_id, []).append((job_id, url))

    ok_total = fail_total = 0
    for source_id, jobs in by_source.items():
        if source_id == "linkedin":
            ok, fail = _fetch_descriptions_stealthily(jobs)
        else:
            ok, fail = _fetch_descriptions_directly(source_id, jobs)
        ok_total += ok
        fail_total += fail

    logger.info(f"\nDescriptions: {ok_total} fetched, {fail_total} still missing")


# Routes each source's search calls given the candidate's selected countries
# (remote-mode) and cities (hybrid/onsite-mode). LinkedIn gets one search per
# location. The 4 worldwide remote boards get one search per candidate
# location instead of a single "Remote" search, since collector/location.py
# treats "Remote" as matching everything unconditionally (_REMOTE_TERMS),
# which would make a country selection a no-op for these sources. The
# Poland-focused boards only ever search "Poland", and only when the
# candidate wants Poland at all.
_WORLDWIDE_REMOTE_SOURCES = frozenset({
    "remotive", "remoteok", "workingnomads", "weworkremotely", "himalayas",
    "jobicy", "jobscollider", "arbeitnow", "arbeitnow_uk",
    "greenhouse", "lever", "ashby", "hackernews",
})
_POLAND_ONLY_SOURCES = frozenset({"justjoin", "theprotocol", "itpracuj", "nofluffjobs", "solidjobs"})
_POLAND_ALIASES = frozenset({"poland", "polska", "pl"})
_POLAND_CITIES = frozenset({
    "warsaw", "warszawa", "krakow", "kraków", "wroclaw", "wrocław", "gdansk", "gdańsk",
    "poznan", "poznań", "lodz", "łódź", "katowice", "szczecin", "lublin", "bydgoszcz",
    "bialystok", "białystok", "gdynia", "sopot", "torun", "toruń", "rzeszow", "rzeszów",
    "kielce", "gliwice", "czestochowa", "częstochowa", "radom", "opole",
})


def _locations_for_source(source_id: str, locations: list[str], work_country: str | None = None) -> list[str]:
    if source_id == "linkedin":
        return [work_country] if work_country else locations
    if source_id in _WORLDWIDE_REMOTE_SOURCES:
        return [work_country] if work_country else (locations if locations else ["Remote"])
    if source_id in _POLAND_ONLY_SOURCES:
        wants_poland = any(
            l.strip().lower() in _POLAND_ALIASES or l.strip().lower() in _POLAND_CITIES
            for l in locations
        )
        return ["Poland"] if wants_poland else []
    return locations


def _search_pause_seconds(new_count: int) -> float:
    """Post-search pause modeling a human glancing at results: a flat look at the
    page (always, duplicates need no extra attention) plus reading time for each
    newly-found job. A search that surfaces nothing new is over in seconds; one
    that finds several new listings takes proportionally longer."""
    glance = random.uniform(STEALTH["search_glance_min"], STEALTH["search_glance_max"])
    reading = sum(
        random.uniform(STEALTH["search_new_min"], STEALTH["search_new_max"])
        for _ in range(new_count)
    )
    return glance + reading


# Hard ceiling on total search calls in one run, independent of max_jobs,
# since a job-count budget doesn't help when searches simply find nothing new.
# Generous on purpose: a safety rail, not a normal-case limit. Only counts
# searches against sources with requires_stealth_pauses=True (LinkedIn,
# theprotocol, itpracuj): those make one real, paced network request per
# (title, location) pair, so the count reflects real cost. The worldwide-remote
# sources (remotive/remoteok/workingnomads/weworkremotely) fetch once per query
# and then filter that cached response locally per candidate country - counting
# each of those ~27 free local iterations as a "search" let them exhaust this
# cap before LinkedIn even finished its own queries, starving every source
# after them (including every Poland-focused board) of a single real search,
# every run, since the per-country expansion landed (2026-08-02).
_MAX_TOTAL_SEARCHES = 150
_MAX_QUERIES_PER_SOURCE = 6


def _collect_job_cards(
    selected_sources: list[str],
    search_queries: list[str],
    locations: list[str],
    days_back: int,
    max_jobs: int | None,
    known_urls: set[str],
    rejected_kw: list[str],
    session_id: int,
    work_country: str | None = None,
    max_jobs_per_source: int | None = None,
) -> tuple[int, int, list[tuple[str, str, str]]]:
    jobs_found = 0
    jobs_new = 0
    jobs_pending_description: list[tuple[str, str, str]] = []
    jobs_prefiltered = 0
    applicable_sources = [
        source_id for source_id in selected_sources
        if _locations_for_source(source_id, locations, work_country)
    ]

    for source_index, source_id in enumerate(applicable_sources):
        if max_jobs is not None and jobs_new >= max_jobs:
            break

        locations_for_source = _locations_for_source(source_id, locations, work_country)
        if not locations_for_source:
            logger.info(f"\n[{source_id}] Skipping, none of the candidate's selected countries apply to this source.")
            continue

        logger.info(f"\n[{source_id}] Starting source...")
        try:
            source = make_source(source_id, days_back=days_back)

            with source:
                source.login()

                queries_for_source = source_queries(source_id, search_queries)
                try:
                    query_summary = search_stats_repository.get_query_summary(source_id)
                except Exception:
                    query_summary = []
                queries_for_source = order_queries(queries_for_source, query_summary, session_id)
                if source_id == "linkedin":
                    excluded = excluded_search_queries_repository.get_excluded("linkedin")
                    if excluded:
                        for q in search_queries:
                            if q in excluded:
                                logger.info(f"  [prune] Skipping LinkedIn query {q!r} (auto-excluded: {excluded[q]})")
                        queries_for_source = [q for q in queries_for_source if q.original not in excluded]
                queries_for_source = queries_for_source[:_MAX_QUERIES_PER_SOURCE]

                # Fair-share budgets instead of first-come-first-served, so the
                # first source/query can't consume the whole max_jobs budget.
                remaining_sources = len(applicable_sources) - source_index
                source_budget = (
                    math.ceil((max_jobs - jobs_new) / remaining_sources)
                    if max_jobs is not None else None
                )
                if max_jobs_per_source is not None:
                    source_budget = min(source_budget, max_jobs_per_source) if source_budget is not None else max_jobs_per_source
                jobs_new_this_source = 0
                source_searches = 0
                source_cap_hit = False

                first_search = True
                pending_pause = 0.0
                for query_index, query in enumerate(queries_for_source):
                    title = query.original
                    if source_budget and jobs_new_this_source >= source_budget:
                        break
                    if source_cap_hit:
                        break
                    remaining_queries = len(queries_for_source) - query_index
                    query_budget = (
                        math.ceil((source_budget - jobs_new_this_source) / remaining_queries)
                        if source_budget is not None else None
                    )
                    jobs_new_this_query = 0
                    for location in locations_for_source:
                        if source_budget and jobs_new_this_source >= source_budget:
                            break
                        if query_budget and jobs_new_this_query >= query_budget:
                            break
                        if source.requires_stealth_pauses and source_searches >= _MAX_TOTAL_SEARCHES:
                            logger.warning(
                                f"\n[search-cap] Reached the {_MAX_TOTAL_SEARCHES}-search limit for this run, "
                                "stopping early rather than continuing unbounded."
                            )
                            source_cap_hit = True
                            break

                        if not first_search and source.requires_stealth_pauses:
                            resume = (datetime.now() + timedelta(seconds=pending_pause)).strftime("%H:%M")
                            logger.info(f"  [stealth] Pausing {pending_pause:.0f}s before next search... → resume ~{resume}")
                            time.sleep(pending_pause)
                        first_search = False

                        logger.info(f"\nSearching: {title!r} in {location!r}")
                        if source.requires_stealth_pauses:
                            source_searches += 1
                        remaining = None
                        if query_budget:
                            remaining = min(query_budget - jobs_new_this_query, source_budget - jobs_new_this_source)
                        source.last_search_diagnostics = {}
                        raw_jobs = source.search(query.outbound, location, max_results=remaining, known_urls=known_urls)
                        diagnostics = getattr(source, "last_search_diagnostics", {})
                        raw_jobs = [
                            raw for raw in raw_jobs
                            if job_matches_query(title, raw.title, raw.description)
                        ]
                        jobs_found += len(raw_jobs)

                        new_this_search = 0
                        for raw in raw_jobs:
                            if source_budget and jobs_new_this_source >= source_budget:
                                break
                            if query_budget and jobs_new_this_query >= query_budget:
                                break
                            try:
                                job_id = job_repository.insert(
                                    title=raw.title,
                                    company=raw.company,
                                    location=raw.location,
                                    url=raw.url,
                                    source=raw.source,
                                    source_id=raw.source_id,
                                    description=raw.description,
                                    search_query=title,
                                    posted_at=raw.posted_at,
                                    source_structured_data=raw.source_structured_data,
                                )
                            except Exception as e:
                                # A single job's insert failing shouldn't take down
                                # the whole run.
                                logger.warning(f"  Skip (insert failed): {raw.title} @ {raw.company}, {e}")
                                continue
                            if job_id is None:
                                logger.info(f"  Skip (duplicate): {raw.title} @ {raw.company}")
                                continue

                            jobs_new += 1
                            jobs_new_this_source += 1
                            jobs_new_this_query += 1
                            new_this_search += 1
                            known_urls.add(raw.url)
                            if not raw.description:
                                reason = title_banned_reason(raw.title, rejected_kw) if rejected_kw else None
                                if reason:
                                    job_repository.update_score_and_status(job_id, 0.0, reason, "auto_rejected")
                                    jobs_prefiltered += 1
                                    logger.info(f"  [prefilter] {raw.title} @ {raw.company}, {reason}")
                                else:
                                    jobs_pending_description.append((job_id, raw.url, source_id))
                            logger.info(f"  [{jobs_new}{'/' + str(max_jobs) if max_jobs else ''}] {raw.title} @ {raw.company}")
                            if max_jobs is not None and jobs_new >= max_jobs:
                                break

                        search_stats_repository.record(
                            session_id, source_id, title, location,
                            cards_found=len(raw_jobs), new_found=new_this_search,
                            upstream_found=diagnostics.get("upstream_found", len(raw_jobs)),
                            query_matched=diagnostics.get("query_matched", len(raw_jobs)),
                            date_matched=diagnostics.get("date_matched", len(raw_jobs)),
                            geo_matched=diagnostics.get("geo_matched", len(raw_jobs)),
                        )

                        if source.requires_stealth_pauses:
                            pending_pause = _search_pause_seconds(new_this_search)

        except Exception as exc:
            logger.warning(f"[{source_id}] Source failed, skipping: {exc}")

    if jobs_prefiltered:
        logger.info(f"\n[prefilter] Skipped description fetch for {jobs_prefiltered} job(s), banned keyword in title")

    return jobs_found, jobs_new, jobs_pending_description


def run(
    days_back: int = 7,
    max_jobs: int | None = None,
    titles: list[str] | None = None,
    locations: list[str] | None = None,
    search_queries_override: list[str] | None = None,
    source_ids: list[str] | None = None,
    session_id: int | None = None,
    max_jobs_per_source: int | None = None,
) -> dict:
    criteria = criteria_repository.get_active_dict()
    preferences = candidate_preferences_repository.get_active() or {}
    if titles:
        criteria["titles"] = titles
    if locations:
        criteria["locations"] = locations
    elif preferences:
        work_mode = preferences.get("work_mode") or []
        markets = []
        if "remote" in work_mode and preferences.get("work_country"):
            markets.append(preferences["work_country"])
        if "hybrid" in work_mode or "onsite" in work_mode:
            markets.extend(preferences.get("hybrid_cities") or [])
        if markets:
            criteria["locations"] = list(dict.fromkeys(markets))
    if search_queries_override:
        criteria["search_queries"] = search_queries_override

    search_queries = criteria["search_queries"] or criteria["titles"]
    if not search_queries or not criteria["locations"]:
        raise ValueError("At least one search query (or job title) and one location must be configured before running the collector.")

    selected_sources = source_ids or [s["id"] for s in available_sources()]
    work_country = (preferences.get("work_country") or "").strip() or None

    # Only start/finish our own session when nobody handed us one to reuse:
    # a caller running this as one stage of a larger pipeline already has an
    # active session, and starting a second one would trip JobAgentWeb's
    # concurrent-session guard.
    owns_session = session_id is None
    if owns_session:
        session_id = session_repository.start()
    jobs_found = 0
    jobs_new = 0

    logger.info(f"Collector starting, last {days_back} day(s), limit: {max_jobs or 'unlimited'}")
    logger.info(f"Sources:        {', '.join(selected_sources)}")
    logger.info(f"Search queries: {', '.join(search_queries)}")
    logger.info(f"Locations:      {', '.join(criteria['locations'])}")
    logger.info("=" * 50)

    try:
        known_urls = job_repository.get_all_urls()
        urls_before_run = set(known_urls)
        logger.info(f"Loaded {len(known_urls)} known URLs for early-stop deduplication.")

        rejected_kw = [r.lower() for r in criteria["rejected"]]
        jobs_found, jobs_new, jobs_pending_description = _collect_job_cards(
            selected_sources, search_queries, criteria["locations"],
            days_back, max_jobs, known_urls, rejected_kw, session_id, work_country,
            max_jobs_per_source,
        )

        if jobs_pending_description:
            cooldown = random.uniform(30, 90)
            resume = (datetime.now() + timedelta(seconds=cooldown)).strftime("%H:%M")
            logger.info(f"\n[stealth] Cooldown {cooldown:.0f}s before fetching descriptions... → resume ~{resume}")
            time.sleep(cooldown)
            _fetch_descriptions_in_batches(jobs_pending_description)

        # Fetched once and shared, both filters used to independently pull the
        # entire 'new' pool (full descriptions included) over HTTP every run.
        new_jobs = [job for job in job_repository.get_new() if job.get("url") not in urls_before_run]

        logger.info("\n=== LANGUAGE FILTER ===")
        lang_result = apply_language_filter(new_jobs)
        if lang_result["checked"]:
            logger.info(f"Checked {lang_result['checked']} job(s), auto-rejected {lang_result['auto_rejected']}")
        else:
            logger.info("No languages configured, all jobs passed through")

        # Excludes what the language filter just rejected, so the keyword
        # filter doesn't overwrite that rejection reason with its own.
        already_rejected = set(lang_result["rejected_ids"])
        remaining_jobs = [j for j in new_jobs if j["id"] not in already_rejected]

        logger.info("\n=== KEYWORD FILTER ===")
        filter_result = apply_keyword_filter(remaining_jobs)
        if filter_result["checked"]:
            logger.info(f"Checked {filter_result['checked']} job(s), auto-rejected {filter_result['auto_rejected']}")
        else:
            logger.info("No criteria configured, all jobs passed through")

        if owns_session:
            session_repository.mark_collected(session_id)
            session_repository.finish(session_id, jobs_found=jobs_found, jobs_scored=0)

        logger.info("\n" + "=" * 50)
        logger.info(f"Done. Found: {jobs_found}  New: {jobs_new}")

        return {
            "jobs_found": jobs_found,
            "jobs_new": jobs_new,
            "job_ids": [job["id"] for job in new_jobs],
        }

    except Exception as e:
        if owns_session:
            session_repository.finish(session_id, jobs_found=jobs_found, jobs_scored=0, status="error")
        logger.error(str(e))
        raise


if __name__ == "__main__":
    import argparse

    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")

    parser = argparse.ArgumentParser(description="Collect job listings")
    parser.add_argument("--days",           type=int,  default=7,    help="Days back to search (default: 7)")
    parser.add_argument("--max-jobs",       type=int,  default=None, help="Max new jobs to collect (default: unlimited)")
    parser.add_argument("--max-jobs-per-source", type=int, default=None, help="Max new jobs from each source")
    parser.add_argument("--titles",         nargs="*", default=None, help="Override job titles (scoring only)")
    parser.add_argument("--locations",      nargs="*", default=None, help="Override search locations")
    parser.add_argument("--search-queries", nargs="*", default=None, help="Override search queries")
    parser.add_argument("--sources",        nargs="*", default=None, help="Sources to use (default: all)")
    parser.add_argument("--session-id",     type=int,  default=None, help="Reuse an existing session (set by the dashboard when this runs as one stage of a larger pipeline) instead of starting a new one")
    args = parser.parse_args()

    run(
        days_back=args.days,
        max_jobs=args.max_jobs,
        titles=args.titles,
        locations=args.locations,
        max_jobs_per_source=args.max_jobs_per_source,
        search_queries_override=args.search_queries,
        source_ids=args.sources,
        session_id=args.session_id,
    )
