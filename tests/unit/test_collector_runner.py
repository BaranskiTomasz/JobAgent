from unittest.mock import MagicMock, patch

import pytest

from config import STEALTH
from collector.base import RawJob
from collector.runner import (
    _search_pause_seconds, _fetch_one, _fetch_descriptions_directly, _fetch_descriptions_in_batches,
    _locations_for_source, _collect_job_cards, run as collector_run,
)


class TestLocationsForSource:
    def test_linkedin_searches_candidate_eligibility_scopes_not_employer_markets(self):
        countries = ["Poland", "Germany", "United Kingdom"]
        assert _locations_for_source("linkedin", countries, "Poland") == ["Poland"]

    def test_remote_feed_uses_work_country_for_eligibility(self):
        assert _locations_for_source("jobscollider", ["United States", "United Kingdom"], "Poland") == ["Poland"]

    def test_worldwide_remote_sources_search_once_per_candidate_location(self):
        # Regression: a single "Remote" search used to be an optimization, but
        # collector/location.py's matching treats "Remote" as matching
        # everything unconditionally, that made country selection a no-op
        # for these 4 sources, silently letting e.g. "Remote, US only"
        # through for a candidate who only selected Poland.
        for source_id in (
            "remotive", "remoteok", "workingnomads", "weworkremotely", "himalayas",
            "jobicy", "jobscollider", "arbeitnow", "arbeitnow_uk",
            "greenhouse", "lever", "ashby", "hackernews",
        ):
            assert _locations_for_source(source_id, ["Poland", "Germany", "Canada"]) == ["Poland", "Germany", "Canada"]

    def test_worldwide_remote_source_falls_back_to_remote_when_no_countries_selected(self):
        # No candidate country to check a job's disclosed location against,
        # fall back to the unrestricted single "Remote" search.
        assert _locations_for_source("remotive", []) == ["Remote"]

    def test_poland_only_source_returns_poland_when_selected(self):
        assert _locations_for_source("justjoin", ["Germany", "Poland"]) == ["Poland"]

    def test_poland_only_source_returns_empty_when_poland_not_selected(self):
        assert _locations_for_source("theprotocol", ["Germany", "United Kingdom"]) == []

    def test_poland_only_source_accepts_polish_spelling(self):
        assert _locations_for_source("itpracuj", ["Polska"]) == ["Poland"]

    def test_poland_only_source_case_insensitive(self):
        assert _locations_for_source("nofluffjobs", ["POLAND"]) == ["Poland"]

    def test_poland_only_source_empty_country_list_returns_empty(self):
        assert _locations_for_source("solidjobs", []) == []

    def test_unknown_source_falls_back_to_full_country_list(self):
        countries = ["Poland", "France"]
        assert _locations_for_source("some-future-source", countries) == countries

    def test_poland_only_source_triggers_on_polish_hybrid_city(self):
        # A hybrid/onsite candidate never picks a country, only a city, so the
        # Polish boards must still activate for a known Polish city.
        assert _locations_for_source("justjoin", ["Warsaw"]) == ["Poland"]

    def test_poland_only_source_triggers_on_polish_city_with_diacritics(self):
        assert _locations_for_source("nofluffjobs", ["Kraków"]) == ["Poland"]

    def test_poland_only_source_ignores_non_polish_city(self):
        assert _locations_for_source("theprotocol", ["Berlin"]) == []

    def test_linkedin_gets_mixed_countries_and_cities_unchanged(self):
        locations = ["Germany", "Warsaw"]
        assert _locations_for_source("linkedin", locations) == locations


class TestSearchPauseSeconds:
    def test_zero_new_stays_within_glance_range(self):
        for _ in range(50):
            pause = _search_pause_seconds(0)
            assert STEALTH["search_glance_min"] <= pause <= STEALTH["search_glance_max"]

    def test_new_jobs_add_reading_time_on_top_of_glance(self):
        for _ in range(50):
            pause = _search_pause_seconds(5)
            lower = STEALTH["search_glance_min"] + 5 * STEALTH["search_new_min"]
            upper = STEALTH["search_glance_max"] + 5 * STEALTH["search_new_max"]
            assert lower <= pause <= upper

    def test_more_new_jobs_means_longer_expected_pause(self):
        few = sum(_search_pause_seconds(1) for _ in range(200)) / 200
        many = sum(_search_pause_seconds(10) for _ in range(200)) / 200
        assert many > few


class TestFetchOne:
    @patch("collector.runner.job_repository")
    def test_success_on_first_try(self, mock_repo):
        source = MagicMock()
        source.fetch_description.return_value = "A real description"
        result = _fetch_one(source, "job1", "https://example.com/job1", "justjoin")
        assert result is True
        mock_repo.update_description.assert_called_once_with("job1", "A real description")

    @patch("collector.runner.time.sleep")
    @patch("collector.runner.job_repository")
    def test_success_on_retry(self, mock_repo, mock_sleep):
        source = MagicMock()
        source.fetch_description.side_effect = [None, "Description on retry"]
        result = _fetch_one(source, "job1", "https://example.com/job1", "justjoin")
        assert result is True
        mock_repo.update_description.assert_called_once_with("job1", "Description on retry")

    @patch("collector.runner.time.sleep")
    @patch("collector.runner.job_repository")
    def test_marks_auto_rejected_after_both_failures(self, mock_repo, mock_sleep):
        source = MagicMock()
        source.fetch_description.return_value = None
        result = _fetch_one(source, "job1", "https://example.com/job1", "justjoin")
        assert result is False
        mock_repo.update_score_and_status.assert_called_once()
        args = mock_repo.update_score_and_status.call_args[0]
        assert args[0] == "job1"
        assert "justjoin" in args[2]
        assert args[3] == "auto_rejected"


class TestFetchDescriptionsDirectly:
    @patch("collector.runner.time.sleep")
    @patch("collector.runner.job_repository")
    @patch("collector.runner.make_source")
    def test_uses_correct_source_class(self, mock_make_source, mock_repo, mock_sleep):
        mock_source = MagicMock()
        mock_source.fetch_description.return_value = "Description"
        mock_make_source.return_value.__enter__.return_value = mock_source
        ok, fail = _fetch_descriptions_directly("justjoin", [("job1", "https://justjoin.it/job-offer/foo")])
        assert ok == 1 and fail == 0
        mock_make_source.assert_called_once_with("justjoin")

    def test_unknown_source_marks_all_failed(self):
        with patch("collector.runner.make_source", side_effect=ValueError("Unknown source")):
            ok, fail = _fetch_descriptions_directly("nonexistent", [("job1", "https://x.com/1"), ("job2", "https://x.com/2")])
        assert ok == 0 and fail == 2


class TestFetchDescriptionsInBatches:
    @patch("collector.runner._fetch_descriptions_directly")
    @patch("collector.runner._fetch_descriptions_stealthily")
    def test_groups_jobs_by_source_and_dispatches_correctly(self, mock_stealthy, mock_direct):
        mock_stealthy.return_value = (1, 0)
        mock_direct.return_value = (1, 0)
        jobs = [
            ("j1", "https://linkedin.com/jobs/view/1", "linkedin"),
            ("j2", "https://justjoin.it/job-offer/2", "justjoin"),
        ]
        _fetch_descriptions_in_batches(jobs)
        mock_stealthy.assert_called_once_with([("j1", "https://linkedin.com/jobs/view/1")])
        mock_direct.assert_called_once_with("justjoin", [("j2", "https://justjoin.it/job-offer/2")])

    @patch("collector.runner._fetch_descriptions_directly")
    def test_never_routes_non_linkedin_jobs_to_stealth_path(self, mock_direct):
        # Regression guard: a bug once sent every pending job through LinkedIn's
        # extraction logic regardless of its real source.
        mock_direct.return_value = (1, 0)
        with patch("collector.runner._fetch_descriptions_stealthily") as mock_stealthy:
            _fetch_descriptions_in_batches([("j1", "https://justjoin.it/job-offer/1", "justjoin")])
            mock_stealthy.assert_not_called()


def _mock_source():
    source = MagicMock()
    source.requires_stealth_pauses = False
    source.search.return_value = []
    source.__exit__.return_value = False
    return source


class TestCollectJobCardsQueryExclusion:
    @patch("collector.runner.excluded_search_queries_repository")
    @patch("collector.runner.search_stats_repository")
    @patch("collector.runner.job_repository")
    @patch("collector.runner.make_source")
    def test_skips_excluded_linkedin_query(self, mock_make_source, mock_jobs, mock_stats, mock_excluded):
        mock_excluded.get_excluded.return_value = {"Bad Query": "reject rate 97% over 30 jobs"}
        source = _mock_source()
        mock_make_source.return_value = source

        _collect_job_cards(
            ["linkedin"], ["Bad Query", "Good Query"], ["Poland"],
            days_back=1, max_jobs=None, known_urls=set(), rejected_kw=[], session_id=1,
        )

        searched_titles = [c.args[0] for c in source.search.call_args_list]
        assert searched_titles == ["Good Query"]

    @patch("collector.runner.excluded_search_queries_repository")
    @patch("collector.runner.search_stats_repository")
    @patch("collector.runner.job_repository")
    @patch("collector.runner.make_source")
    def test_exclusion_list_only_checked_for_linkedin(self, mock_make_source, mock_jobs, mock_stats, mock_excluded):
        source = _mock_source()
        mock_make_source.return_value = source

        _collect_job_cards(
            ["remotive"], ["Bad Query"], ["Remote"],
            days_back=1, max_jobs=None, known_urls=set(), rejected_kw=[], session_id=1,
        )

        searched_titles = [c.args[0] for c in source.search.call_args_list]
        assert searched_titles == ["Bad Query"]
        mock_excluded.get_excluded.assert_not_called()

    @patch("collector.runner.search_stats_repository")
    @patch("collector.runner.job_repository")
    @patch("collector.runner.make_source")
    def test_no_exclusions_recorded_runs_every_query(self, mock_make_source, mock_jobs, mock_stats):
        # No patch on excluded_search_queries_repository, hits the real (empty,
        # per-test-isolated) DB, confirming an empty exclusion table filters nothing.
        source = _mock_source()
        mock_make_source.return_value = source

        _collect_job_cards(
            ["linkedin"], ["Query A", "Query B"], ["Poland"],
            days_back=1, max_jobs=None, known_urls=set(), rejected_kw=[], session_id=1,
        )

        searched_titles = [c.args[0] for c in source.search.call_args_list]
        assert set(searched_titles) == {"Query A", "Query B"}


class TestCollectJobCardsBudgetAllocation:
    # Regression for the audit's exact finding: max_jobs used to be one global
    # counter consumed in source-registration order, so the first source (and
    # within it, the first query) could exhaust the whole budget before the
    # rest of the run ever contributed anything.

    @patch("collector.runner.search_stats_repository")
    @patch("collector.runner.job_repository")
    @patch("collector.runner.make_source")
    def test_max_jobs_split_fairly_across_sources(self, mock_make_source, mock_jobs, mock_stats):
        def _source_with_jobs(n, source_name):
            source = _mock_source()
            source.search.return_value = [
                RawJob(title=f"PHP Dev {i}", company="Acme", location="Poland",
                       url=f"https://{source_name}.com/{i}", source=source_name, description="desc")
                for i in range(n)
            ]
            return source

        remotive_source = _source_with_jobs(10, "remotive")
        remoteok_source = _source_with_jobs(10, "remoteok")
        mock_make_source.side_effect = lambda source_id, **kw: {
            "remotive": remotive_source, "remoteok": remoteok_source,
        }[source_id]
        mock_jobs.insert.side_effect = lambda **kw: kw["url"]

        _collect_job_cards(
            ["remotive", "remoteok"], ["PHP Developer"], ["Remote"],
            days_back=1, max_jobs=4, known_urls=set(), rejected_kw=[], session_id=1,
        )

        # Each source's search() call should be capped to its fair share (2 of
        # the total 4), under the old shared-counter logic, the second
        # source would have seen 0 (or a near-zero) remaining budget instead.
        assert remotive_source.search.call_args.kwargs["max_results"] == 2
        assert remoteok_source.search.call_args.kwargs["max_results"] == 2

    @patch("collector.runner._MAX_TOTAL_SEARCHES", 2)
    @patch("collector.runner.search_stats_repository")
    @patch("collector.runner.job_repository")
    @patch("collector.runner.make_source")
    def test_total_search_count_is_capped_for_paced_sources(self, mock_make_source, mock_jobs, mock_stats):
        # Regression: no upper limit on total searches meant a broad location
        # selection ("+Add all EU countries") times several search-query
        # criteria could run into the hundreds with no cap at all, for a
        # source that pays a real, paced network cost per (title, location).
        source = _mock_source()
        source.requires_stealth_pauses = True
        mock_make_source.return_value = source

        _collect_job_cards(
            ["linkedin"], ["PHP Developer"], ["Poland", "Germany", "France", "Spain"],
            days_back=1, max_jobs=None, known_urls=set(), rejected_kw=[], session_id=1,
        )

        assert source.search.call_count == 2

    @patch("collector.runner._MAX_TOTAL_SEARCHES", 2)
    @patch("collector.runner.search_stats_repository")
    @patch("collector.runner.job_repository")
    @patch("collector.runner.make_source")
    def test_non_paced_sources_are_not_search_capped(self, mock_make_source, mock_jobs, mock_stats):
        # Regression: remotive/remoteok/workingnomads/weworkremotely fetch once
        # per query and filter the cached response locally per candidate
        # country - each of those free local iterations used to count toward
        # the same shared cap as a real LinkedIn network search, so on a
        # multi-country profile they alone could exhaust it before any other
        # source (including every Poland-focused board) ran a single search.
        source = _mock_source()
        mock_make_source.return_value = source

        _collect_job_cards(
            ["remotive"], ["PHP Developer"], ["Poland", "Germany", "France", "Spain"],
            days_back=1, max_jobs=None, known_urls=set(), rejected_kw=[], session_id=1,
        )

        assert source.search.call_count == 4

    @patch("collector.runner._MAX_TOTAL_SEARCHES", 1)
    @patch("collector.runner.time.sleep")
    @patch("collector.runner.excluded_search_queries_repository")
    @patch("collector.runner.search_stats_repository")
    @patch("collector.runner.job_repository")
    @patch("collector.runner.make_source")
    def test_paced_source_cap_does_not_stop_later_sources(self, mock_make_source, mock_jobs, mock_stats, mock_excluded, mock_sleep):
        mock_excluded.get_excluded.return_value = {}
        linkedin = _mock_source()
        linkedin.requires_stealth_pauses = True
        remotive = _mock_source()
        mock_make_source.side_effect = lambda source_id, **kw: {
            "linkedin": linkedin, "remotive": remotive,
        }[source_id]

        _collect_job_cards(
            ["linkedin", "remotive"], ["Developer"], ["Poland", "Germany"],
            days_back=1, max_jobs=None, known_urls=set(), rejected_kw=[], session_id=1,
        )

        assert linkedin.search.call_count == 1
        assert remotive.search.call_count == 2

    @patch("collector.runner.search_stats_repository")
    @patch("collector.runner.job_repository")
    @patch("collector.runner.make_source")
    def test_max_jobs_is_not_exceeded_when_smaller_than_source_count(self, mock_make_source, mock_jobs, mock_stats):
        sources = {}
        for name in ("remotive", "remoteok", "workingnomads"):
            source = _mock_source()
            source.search.return_value = [
                RawJob(title="Developer", company=name, location="Remote", url=f"https://{name}.com/1", source=name, description="desc")
            ]
            sources[name] = source
        mock_make_source.side_effect = lambda source_id, **kw: sources[source_id]
        mock_jobs.insert.side_effect = lambda **kw: kw["url"]

        result = _collect_job_cards(
            list(sources), ["Developer"], ["Remote"],
            days_back=1, max_jobs=2, known_urls=set(), rejected_kw=[], session_id=1,
        )

        assert result[1] == 2
        assert sources["workingnomads"].search.call_count == 0

    @patch("collector.runner.search_stats_repository")
    @patch("collector.runner.job_repository")
    @patch("collector.runner.make_source")
    def test_unused_source_budget_is_redistributed(self, mock_make_source, mock_jobs, mock_stats):
        empty = _mock_source()
        full = _mock_source()
        full.search.return_value = [
            RawJob(title=f"Developer {i}", company="Acme", location="Remote", url=f"https://example.com/{i}", source="remoteok", description="desc")
            for i in range(5)
        ]
        mock_make_source.side_effect = lambda source_id, **kw: empty if source_id == "remotive" else full
        mock_jobs.insert.side_effect = lambda **kw: kw["url"]

        result = _collect_job_cards(
            ["remotive", "remoteok"], ["Developer"], ["Remote"],
            days_back=1, max_jobs=4, known_urls=set(), rejected_kw=[], session_id=1,
        )

        assert result[1] == 4
        assert full.search.call_args.kwargs["max_results"] == 4

    @patch("collector.runner.search_stats_repository")
    @patch("collector.runner.job_repository")
    @patch("collector.runner.make_source")
    def test_per_source_limit_is_never_redistributed(self, mock_make_source, mock_jobs, mock_stats):
        sources = {}
        for name in ("remotive", "remoteok"):
            source = _mock_source()
            source.search.return_value = [
                RawJob(title=f"Developer {i}", company=name, location="Remote",
                       url=f"https://{name}.com/{i}", source=name, description="desc")
                for i in range(5)
            ]
            sources[name] = source
        mock_make_source.side_effect = lambda source_id, **kw: sources[source_id]
        mock_jobs.insert.side_effect = lambda **kw: kw["url"]

        result = _collect_job_cards(
            list(sources), ["Developer"], ["Remote"],
            days_back=1, max_jobs=None, known_urls=set(), rejected_kw=[], session_id=1,
            max_jobs_per_source=2,
        )

        assert result[1] == 4
        assert all(source.search.call_args.kwargs["max_results"] == 2 for source in sources.values())


class TestCollectJobCardsSearchQueryAttribution:
    @patch("collector.runner.search_stats_repository")
    @patch("collector.runner.job_repository")
    @patch("collector.runner.make_source")
    def test_inserted_job_is_tagged_with_the_query_that_found_it(self, mock_make_source, mock_jobs, mock_stats):
        source = _mock_source()
        source.search.return_value = [
            RawJob(title="PHP Dev", company="Acme", location="Poland",
                   url="https://a.com/1", source="linkedin", description="desc")
        ]
        mock_make_source.return_value = source
        mock_jobs.insert.return_value = "job123"

        _collect_job_cards(
            ["linkedin"], ["Senior PHP Developer"], ["Poland"],
            days_back=1, max_jobs=None, known_urls=set(), rejected_kw=[], session_id=1,
        )

        assert mock_jobs.insert.call_args.kwargs["search_query"] == "Senior PHP Developer"


def _mock_run_deps(mock_criteria, mock_jobs, mock_collect, mock_lang, mock_kw):
    mock_criteria.get_active_dict.return_value = {
        "titles": ["PHP"], "locations": ["Poland"], "search_queries": [], "rejected": [],
    }
    mock_jobs.get_all_urls.return_value = set()
    mock_jobs.get_new.return_value = []
    mock_collect.return_value = (0, 0, [])
    mock_lang.return_value = {"checked": 0, "auto_rejected": 0, "rejected_ids": []}
    mock_kw.return_value = {"checked": 0, "auto_rejected": 0, "rejected_ids": []}


class TestRunSessionOwnership:
    """Regression coverage for the dashboard's 'Run Agent' pipeline: the outer
    websocket handler already has an active session spanning the whole run, so
    the COLLECTOR stage must reuse it instead of starting its own, starting a
    second one gets rejected by JobAgentWeb's concurrent-session guard."""

    @patch("collector.runner.apply_keyword_filter")
    @patch("collector.runner.apply_language_filter")
    @patch("collector.runner._collect_job_cards")
    @patch("collector.runner.job_repository")
    @patch("collector.runner.session_repository")
    @patch("collector.runner.criteria_repository")
    def test_starts_and_finishes_its_own_session_when_none_given(
        self, mock_criteria, mock_session, mock_jobs, mock_collect, mock_lang, mock_kw,
    ):
        _mock_run_deps(mock_criteria, mock_jobs, mock_collect, mock_lang, mock_kw)
        mock_session.start.return_value = 99

        collector_run(days_back=1)

        mock_session.start.assert_called_once()
        mock_session.mark_collected.assert_called_once_with(99)
        mock_session.finish.assert_called_once_with(99, jobs_found=0, jobs_scored=0)

    @patch("collector.runner.apply_keyword_filter")
    @patch("collector.runner.apply_language_filter")
    @patch("collector.runner._collect_job_cards")
    @patch("collector.runner.job_repository")
    @patch("collector.runner.session_repository")
    @patch("collector.runner.criteria_repository")
    def test_reuses_given_session_and_never_starts_or_finishes_its_own(
        self, mock_criteria, mock_session, mock_jobs, mock_collect, mock_lang, mock_kw,
    ):
        _mock_run_deps(mock_criteria, mock_jobs, mock_collect, mock_lang, mock_kw)

        collector_run(days_back=1, session_id=42)

        mock_session.start.assert_not_called()
        mock_session.finish.assert_not_called()
        # Reusing a session means the caller owns its whole lifecycle, including
        # marking it collected, that's _run_pipeline_ws's job in this case, not ours.
        mock_session.mark_collected.assert_not_called()
        assert mock_collect.call_args.args[-3] == 42

    @patch("collector.runner.apply_keyword_filter")
    @patch("collector.runner.apply_language_filter")
    @patch("collector.runner._collect_job_cards")
    @patch("collector.runner.job_repository")
    @patch("collector.runner.session_repository")
    @patch("collector.runner.criteria_repository")
    def test_reused_session_is_not_finished_on_failure_either(
        self, mock_criteria, mock_session, mock_jobs, mock_collect, mock_lang, mock_kw,
    ):
        _mock_run_deps(mock_criteria, mock_jobs, mock_collect, mock_lang, mock_kw)
        mock_jobs.get_all_urls.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError):
            collector_run(days_back=1, session_id=42)

        mock_session.start.assert_not_called()
        mock_session.finish.assert_not_called()


class TestFilterFetchSharing:
    """Regression coverage: apply_language_filter() and apply_keyword_filter()
    used to each independently call job_repository.get_new(), a full 'new'
    pool fetch with descriptions, twice per collector run. run() now fetches
    once and shares the list."""

    @patch("collector.runner.apply_keyword_filter")
    @patch("collector.runner.apply_language_filter")
    @patch("collector.runner._collect_job_cards")
    @patch("collector.runner.job_repository")
    @patch("collector.runner.session_repository")
    @patch("collector.runner.criteria_repository")
    def test_get_new_is_fetched_only_once(
        self, mock_criteria, mock_session, mock_jobs, mock_collect, mock_lang, mock_kw,
    ):
        _mock_run_deps(mock_criteria, mock_jobs, mock_collect, mock_lang, mock_kw)

        collector_run(days_back=1)

        mock_jobs.get_new.assert_called_once()

    @patch("collector.runner.apply_keyword_filter")
    @patch("collector.runner.apply_language_filter")
    @patch("collector.runner._collect_job_cards")
    @patch("collector.runner.job_repository")
    @patch("collector.runner.session_repository")
    @patch("collector.runner.criteria_repository")
    def test_keyword_filter_never_sees_a_job_the_language_filter_already_rejected(
        self, mock_criteria, mock_session, mock_jobs, mock_collect, mock_lang, mock_kw,
    ):
        # Otherwise the keyword filter would re-check a job against a stale
        # in-memory snapshot that still shows it as 'new', possibly overwriting
        # a language-based rejection reason with an unrelated keyword-based one.
        _mock_run_deps(mock_criteria, mock_jobs, mock_collect, mock_lang, mock_kw)
        good, bad = {"id": "good"}, {"id": "bad"}
        mock_jobs.get_new.return_value = [good, bad]
        mock_lang.return_value = {"checked": 2, "auto_rejected": 1, "rejected_ids": ["bad"]}

        collector_run(days_back=1)

        passed_to_keyword_filter = mock_kw.call_args.args[0]
        assert passed_to_keyword_filter == [good]
