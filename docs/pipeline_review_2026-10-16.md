# JobAgent collection and AI pipeline review — 2026-10-16

## Executive verdict

JobAgent has a coherent HTTP-only client architecture and all 19 advertised collector adapters are registered, but this checkout cannot establish that the collectors or paid AI calls work live today. There are no credentials, no local run logs or database snapshot, no sibling JobAgentWeb checkout, and no usable pytest installation.

The pipeline is not fully correct as documented. The most important defect is the hand-off from extraction to filtering/scoring: Haiku-derived facts are assigned `medium` confidence, while the deterministic filter retains only `high` confidence facts, and the scorer does not receive `structured_data` at all. A posting whose description says “remote in the US only” can therefore survive the Poland geo dealbreaker if its adapter does not provide native geo facts. Ranking debate does work: it changes ranks and the changed order is persisted. Fingerprint invalidation is strong for scoring but covers only part of the ranking implementation.

The known justjoin.it code failure that caused a 0/247 extraction run is fixed; whether the approximately 247 stored jobs were actually backfilled is unverifiable. The known unsafe `remote_regions` behavior is only partly fixed: three Polish adapters removed it, while it.pracuj.pl and theprotocol.it still infer `remote_regions=["Poland"]` merely from a remote-mode flag.

## Scope and evidence

- Reviewed `HEAD` `5da9d75` and the architecture described in [README.md](../README.md#L1).
- Confirmed the client-side repository layer goes through [api_client.py](../api_client.py#L1), whose shared `httpx.Client` targets JobAgentWeb ([api_client.py](../api_client.py#L13)). No local SQL/database implementation is present.
- Inspected collector, extractor, evaluator, embedding, reranking, listwise, debate, cache, orchestration, tests, and recent git history.
- Checked current vendor documentation. The configured Anthropic IDs are real, although Sonnet 4.6 and Opus 4.8 are now legacy models: [Anthropic model IDs](https://platform.claude.com/docs/en/about-claude/models/model-ids-and-versions), [Opus 4.8](https://platform.claude.com/docs/en/models/opus-4-8/overview), and [Sonnet 4.6](https://platform.claude.com/docs/en/models/sonnet-4-6/overview). Live account access was not tested.

## Test-suite result

**Verdict: unverifiable in this worktree.** `pytest -q` failed with `pytest: command not found` (exit 127); `python3 -m pytest -q` failed with `No module named pytest` (exit 1). No dependency installation was attempted. Consequently there is no honest pass/fail/skip count.

Even with pytest installed, the autouse fixture requires a sibling JobAgentWeb checkout and venv, launches it against an isolated Postgres service, and waits for `/healthz` ([tests/conftest.py](../tests/conftest.py#L29), [tests/conftest.py](../tests/conftest.py#L45), [tests/conftest.py](../tests/conftest.py#L79)); none is present here.

There are no xfail declarations. The two Anthropic e2e modules skip without a real key: distillation/scorer round-trip ([tests/e2e/test_distillation.py](../tests/e2e/test_distillation.py#L22)) and scoring scenarios ([tests/e2e/test_pipeline.py](../tests/e2e/test_pipeline.py#L28)). One permissions test skips on Windows ([tests/integration/test_web_routes.py](../tests/integration/test_web_routes.py#L380)). Unit tests use fake API keys ([tests/conftest.py](../tests/conftest.py#L10)), so they cannot demonstrate live Anthropic or Voyage compatibility.

## Collection review

### Inventory and current-health verdicts

The registry contains exactly 19 integrations ([collector/sources/__init__.py](../collector/sources/__init__.py#L20)), matching the README inventory ([README.md](../README.md#L67)). All have offline/mock test coverage, either in a named source test or the shared ATS tests. That supports implementation presence and local parsing behavior, not upstream availability.

| Integration | Static verdict | Live verdict | Evidence / qualification |
|---|---|---|---|
| LinkedIn | Working by code/tests | Unverifiable | Registered; browser/login-specific and intentionally excluded from offline live scraping ([README.md](../README.md#L81)). |
| Remotive | Working by code/tests | Unverifiable | Adapter and `test_remotive_source.py`; cached feed design is documented ([README.md](../README.md#L93)). |
| Remote OK | Working by code/tests | Unverifiable | Adapter and `test_remoteok_source.py`; generic/tag fallback is documented ([README.md](../README.md#L95)). |
| Working Nomads | Working by code/tests | Unverifiable | Adapter and `test_workingnomads_source.py`; upstream window is explicitly bounded ([README.md](../README.md#L97)). |
| We Work Remotely | Working by code/tests | Unverifiable | Adapter plus `test_weworkremotely_source.py`/`test_wwr_source.py`; partial-feed diagnostics exist. |
| Himalayas | Working by code/tests | Unverifiable | Adapter and `test_himalayas_source.py`; RSS is inherently a bounded sample ([README.md](../README.md#L99)). |
| Jobicy | Working by code/tests | Unverifiable | Adapter and `test_jobicy_source.py`; cursor and short-query fallback are covered. |
| JobsCollider / Remote First Jobs | Working by code/tests | Unverifiable | Adapter and `test_jobscollider_source.py`; hard upstream five-page cap remains documented ([README.md](../README.md#L115)). |
| Arbeitnow Europe | Working by code/tests | Unverifiable | Adapter and `test_arbeitnow_source.py`; pagination/diagnostics are present. |
| Arbeitnow UK | Working by code/tests | Unverifiable | Same adapter/test family; intentionally rejects ambiguous UK-only/bare-remote inventory ([README.md](../README.md#L103)). |
| Greenhouse | Working by code/tests | Unverifiable | Registered and covered by `test_ats_sources.py`; per-board partial failures are retained. |
| Lever | Working by code/tests | Unverifiable | Registered and covered by ATS/Lever tests; pagination and partial failures are implemented. |
| Ashby | Working by code/tests | Unverifiable | Registered and covered by `test_ashby_source.py`; current registry is limited to configured boards. |
| Hacker News “Who is hiring?” | Working by code/tests | Unverifiable | Adapter and `test_hackernews_source.py`; explicit country evidence is required. |
| justjoin.it | Working after recent fixes | Unverifiable | Skill dictionaries are now normalized ([collector/sources/justjoin.py](../collector/sources/justjoin.py#L61)); listings are cached per session ([collector/sources/justjoin.py](../collector/sources/justjoin.py#L118)); description retry is implemented ([collector/sources/justjoin.py](../collector/sources/justjoin.py#L181)). |
| theprotocol.it | **Broken geo assertion** | Unverifiable | Remote mode unconditionally writes `remote_regions=["Poland"]` ([collector/sources/theprotocol.py](../collector/sources/theprotocol.py#L357)). |
| it.pracuj.pl | **Broken geo assertion** | Unverifiable | Same unsupported Poland assertion ([collector/sources/itpracuj.py](../collector/sources/itpracuj.py#L347)). |
| NoFluffJobs | Working after geo fix | Unverifiable | It now deliberately avoids asserting Poland for every remote listing ([collector/sources/nofluffjobs.py](../collector/sources/nofluffjobs.py#L328)). |
| SOLID.Jobs | Working after geo fix | Unverifiable | It now avoids the unsafe assertion after live counterexamples ([collector/sources/solidjobs.py](../collector/sources/solidjobs.py#L313)). |

The README says the ATS registry contains 200 companies ([README.md](../README.md#L75)); `collector/sources/ats_companies.json` currently has **187 entries**. This is documentation drift, not a runtime failure.

### Known-issue status

**justjoin.it 247-job backfill — code fixed, data state unverifiable.** The source comment records the real failure: raw skill dictionaries reached `dict.fromkeys`, producing `unhashable type: 'dict'` and 0/247 extracted jobs ([collector/sources/justjoin.py](../collector/sources/justjoin.py#L61)). Commit `a32993e` normalizes them to names, and the generic missing-description retry is now usable. There is no JobAgentWeb/database access or run artifact proving those existing rows were reprocessed. This issue must remain open operationally until production reports `facts_schema_version=6` (or the intended current version), non-null facts, and no failed rows for that cohort.

**Unsafe remote eligibility — improved but not fixed.** NoFluffJobs and SOLID.Jobs explicitly removed the unsafe hardcoded Poland region, and justjoin does not add it. it.pracuj and theprotocol still convert a remote work-mode label into positive Poland eligibility. Since source-native data is promoted to high confidence ([extractor/runner.py](../extractor/runner.py#L371)), it bypasses the evidence-based uncertainty that normal extraction would preserve. A remote posting actually restricted to another country can enter the Poland pool.

### Additional collection bug

**Medium — timezone substring produces false EU eligibility.** `location_matches` tests timezone abbreviations with raw substring membership ([collector/location.py](../collector/location.py#L77)). For a Poland search, a location such as `Remote — weekly spreadsheet review` contains `eet` inside `sheet` and therefore matches the EET token. Use boundary-aware matching, as the same function already does for countries and `EU`.

## AI pipeline stage verdicts

| Stage | Verdict | Why |
|---|---|---|
| 1. Preference distillation | Working, resilience gap | Uses feedback and questionnaire and saves validated signals ([preference_agent/runner.py](../preference_agent/runner.py#L200)). It makes one API attempt and returns failure on any exception ([preference_agent/runner.py](../preference_agent/runner.py#L242)); orchestration treats failure as non-fatal and continues with the old profile ([scripts/run_all.py](../scripts/run_all.py#L110)). |
| 2. Structured extraction | Working, but downstream hand-off broken | Extraction precedes evaluation ([scripts/run_all.py](../scripts/run_all.py#L116)), uses forced tool output, and avoids saving truncated output ([extractor/runner.py](../extractor/runner.py#L276)). It catches all API errors and returns `{}` without retry ([extractor/runner.py](../extractor/runner.py#L324)); orchestration still scores the job. |
| 3. Dealbreaker pre-filter | **Broken for ordinary AI-derived facts** | It genuinely runs before scoring ([evaluator/runner.py](../evaluator/runner.py#L41), [evaluator/runner.py](../evaluator/runner.py#L105)), but retains only high-confidence fields ([evaluator/dealbreakers.py](../evaluator/dealbreakers.py#L24)); Haiku marks extracted fields medium ([extractor/runner.py](../extractor/runner.py#L319)). Most non-native salary/geo/remote/seniority facts therefore fail open. |
| 4. Sonnet scoring | Working, but does not read extracted structure | The shared prompt includes CV, questionnaire, preferences, examples and calibration ([evaluator/runner.py](../evaluator/runner.py#L64)); the per-job message contains only title/company/location/raw description ([evaluator/scorer.py](../evaluator/scorer.py#L178)). This contradicts the documented claim that the scorer reads extracted structure. |
| 5. Voyage embedding | Working by inspection; live unverifiable | Text hashes cause changed job text/model to be re-embedded ([scripts/rank_jobs.py](../scripts/rank_jobs.py#L61)). Rate limits get string-based backoff, but connection/5xx errors abort the ranking script ([embeddings/indexer.py](../embeddings/indexer.py#L39)). |
| 6. Semantic retrieval | Working, README stale | Current code uses max similarity to any applied vector minus 0.3 × max similarity to rejected vectors, not applied/rejected centroids ([embeddings/indexer.py](../embeddings/indexer.py#L99)). Cold start uses a profile/questionnaire HyDE query. |
| 7. Voyage rerank | Working with degraded fallback | Failure is caught and marked unreliable, preserving prior order ([ranker/reranker.py](../ranker/reranker.py#L47)). There is no retry. |
| 8. Opus listwise rank | Working with fallback | Typed transient errors are retried; failures retain rerank order ([ranker/listwise.py](../ranker/listwise.py#L204)). Invalid IDs are ignored and omitted jobs appended ([ranker/listwise.py](../ranker/listwise.py#L234)). A syntactically valid truncated partial response is not explicitly rejected. |
| 9. Debate / second opinion | **Working and effective** | `debate_rank` output replaces the listwise result ([scripts/rank_jobs.py](../scripts/rank_jobs.py#L151)); dealbreaker risks move to the bottom, other flags move ±3, and ranks are renumbered ([ranker/debate.py](../ranker/debate.py#L190)). The changed ranks and flags are persisted ([scripts/rank_jobs.py](../scripts/rank_jobs.py#L187)) and affect `would_apply` ([scripts/rank_jobs.py](../scripts/rank_jobs.py#L163)). |

The documented “top-20” can currently be 23 on exploration days: three out-of-pool jobs are appended before listwise/debate ([scripts/rank_jobs.py](../scripts/rank_jobs.py#L129)). This is intentional exploration, but README diagrams and wording are stale.

## Concrete defects and failure scenarios

1. **High — hard dealbreakers are bypassed for Haiku-only facts.** Trigger: an adapter supplies no native country/salary/seniority fact, while the description clearly says “US only,” “hybrid,” or a salary below the floor. Extraction stores that fact at medium confidence; `_trusted_fields` removes it; the filter sees unknown and accepts the job. The scorer then sees raw prose but not the structured fact, so the advertised deterministic guarantee is absent.
2. **High — two Polish sources manufacture Poland eligibility.** Trigger: theprotocol or it.pracuj marks a listing remote even though employment/remote eligibility is limited to another country. The adapter writes Poland as source-native/high-confidence and the catalog gate accepts it.
3. **Medium — optional hybrid can be treated as mandatory office attendance.** The extraction schema explicitly models remote and hybrid as independent options and has `office_presence_required` ([extractor/runner.py](../extractor/runner.py#L33)), but `_remote_only_reason` rejects whenever `hybrid is True`, even if `remote is True`, and does not consult `office_presence_required` ([evaluator/dealbreakers.py](../evaluator/dealbreakers.py#L86)). Trigger: a role offers both fully remote and optional hybrid; a remote-only candidate is falsely rejected.
4. **Medium — invalidation omits extraction and part of ranking.** Score fingerprints contain the scoring model/source but not the extraction model, schema, prompt, or implementation. Normal orchestration extracts only IDs newly inserted in that collection run ([scripts/run_all.py](../scripts/run_all.py#L119)), so changing extraction does not refresh existing rows; if their stored facts stay unchanged, their score fingerprint also stays unchanged. Ranking fingerprints hash listwise, debate, reranker, fusion, profile and utility code ([evaluator/fingerprints.py](../evaluator/fingerprints.py#L39)), but omit retrieval (`embeddings/indexer.py`), orchestration/pool selection (`scripts/rank_jobs.py`), exploration, cache behavior and would-apply logic. Trigger: an extraction prompt/model or max-sim/exploration algorithm changes while stored facts/selected IDs remain the same; stale output can be retained or cache-reused ([ranker/rank_cache.py](../ranker/rank_cache.py#L15)). The blanket README invalidation claim is false.
5. **Medium — transient API handling is inconsistent.** Distillation and extraction do not retry; scorer retries only exception text containing `overloaded` ([evaluator/scorer.py](../evaluator/scorer.py#L235)); Voyage embedding retries only strings resembling rate limits; rerank has no retry. A 429, timeout or 5xx may silently leave stale preferences, skip structure/score, degrade reranking, or abort ranking. Only listwise/debate use typed transient retries ([ranker/retry.py](../ranker/retry.py#L8)).
6. **Medium — EU timezone matching is substring-based.** Trigger and impact are described in the collection section; unrelated words containing `cet`, `eet`, etc. can admit restricted inventory.
7. **Low — valid truncated listwise output can be treated as partial success.** Unlike debate/extraction/scoring, listwise does not reject `stop_reason == "max_tokens"`; if the text happens to contain parseable partial JSON, omitted jobs are merely appended. At 16,000 output tokens and roughly 20–23 jobs, the practical likelihood is low.
8. **Low — documented source count and retrieval algorithm are stale.** The configured ATS registry has 187 entries, not 200, and retrieval is max-sim rather than centroid subtraction. This impedes accurate operations/debugging but does not itself corrupt results.

## Fingerprint coverage by promised trigger

| Trigger | Score invalidation | Ranking invalidation | Verdict |
|---|---|---|---|
| CV | Included in constructed system prompt | Explicit candidate profile | Covered |
| Questionnaire | Included in system prompt | Explicit questionnaire | Covered |
| Learned preferences | Rendered into system prompt | Explicit preference list | Covered |
| Job content | Title/company/location/description/source/structured data | Same plus posting date and score fields | Covered |
| Prompts | Final scorer prompt is covered; extractor/distiller prompts are not directly covered | Listwise/debate prompt-bearing source files are hashed | **Partial** |
| Models | Sonnet is covered; extractor/distiller models affect the hash only if their persisted output changes | Opus, Sonnet, Voyage embed/rerank IDs | **Partial across the full pipeline** |
| Ranking implementation | N/A | Only a selected file subset | **Not covered completely** |

The score fingerprint implementation is at [evaluator/fingerprints.py](../evaluator/fingerprints.py#L23). It sensibly hashes the final rendered prompt, so changes in CV/questionnaire/preferences/examples/criteria/calibration invalidate existing scores. Changes in extraction only cause rescoring if stored `structured_data` actually changes; extraction freshness itself is governed separately by server-side facts metadata and could not be verified without JobAgentWeb.

## Prioritized improvement plan

1. **P0 — repair the extraction → dealbreaker/scorer contract (medium).** Define per-field enforceable confidence using evidence/provenance rather than dropping every AI field; keep fail-open behavior for genuinely ambiguous facts. Pass normalized structured facts and evidence to the scorer. Add end-to-end regression cases for US-only remote, optional hybrid plus full remote, below-floor salary, seniority mismatch, and missing native adapter data. This addresses the largest correctness gap and paid-scoring waste.
2. **P0 — remove the two unsupported Poland assertions (small).** Make it.pracuj/theprotocol behave like NoFluffJobs/SOLID: assert remote availability only, then derive eligibility from explicit description/detail evidence. Backfill affected stored facts afterward.
3. **P1 — verify and complete the justjoin backfill in JobAgentWeb (small operational check; medium if repair is needed).** Query the affected cohort, report counts by facts version/status/error, rerun extraction for stale rows, and verify samples. Do not close the known issue based solely on the code fix.
4. **P1 — standardize vendor retries and degraded-stage reporting (medium).** Reuse typed exception handling with bounded exponential backoff and jitter for all Anthropic calls and Voyage embedding/rerank. Record per-stage success/degraded/failure in the run summary. Ensure a transient rerank failure is not cached as a fully fresh ranking.
5. **P1 — make fingerprints declarative and complete (small/medium).** Hash every result-affecting module/config value (retrieval, pool selection, exploration, cache, RRF weights, top-Ns and nudge values), or maintain explicit algorithm-version constants. Add one sensitivity test per promised trigger.
6. **P1 — establish a repeatable live smoke suite (medium).** Run one cheap query against every public adapter, a non-destructive logged-in LinkedIn check, one Anthropic tool call per request shape, one Voyage embed and rerank, and authenticated JobAgentWeb round trips. Store timestamp, upstream/geo/new counts, latency and errors without storing secrets. Offline mocked tests are insufficient for “works today.”
7. **P2 — fix boundary matching and expand geo property tests (small).** Boundary-match timezone tokens and add adversarial strings, exclusions, EU aliases, optional-hybrid cases and country restrictions. Consider a structured country/region matcher instead of substring parsing.
8. **P2 — reject truncated listwise responses explicitly (small).** Treat `max_tokens` as a degraded fallback, matching extractor/scorer/debate behavior, so cache logic retries it next run.
9. **P2 — update operational documentation (small).** Document max-sim retrieval, exploration up to 23 jobs, the actual 187-company registry, confidence policy, and live-health/backfill commands.
10. **P3 — evaluate a 20th source only after correctness/health telemetry exists (medium).** Wellfound could broaden startup coverage, but it is browser/anti-bot sensitive and likely duplicative. A better near-term “source” improvement is expanding and automatically health-checking the direct ATS registry; add a new board only after measuring unique eligible yield and maintenance cost.

## What could not be verified

- No live collector run: this environment has no configured credentials/session, no JobAgentWeb service, no browser session, and no accessible production data. Upstream endpoint availability, anti-bot behavior, actual yields, and freshness for all 19 sources remain unknown.
- No pytest execution: pytest is absent, and the required sibling JobAgentWeb checkout/venv/Postgres test environment is absent.
- No live Anthropic or Voyage request: keys are unset. Model IDs were checked against vendor documentation, but account entitlement, SDK compatibility, request-schema acceptance, quotas, latency and real response shapes were not exercised.
- No confirmation of the 247-job justjoin backfill or any database freshness/row counts.
- No verification of JobAgentWeb's server-side facts invalidation, batch update semantics, embedding dimensions, or API schema because the sibling repository is absent. Those items should be reconciled with the separate JobAgentWeb review.

