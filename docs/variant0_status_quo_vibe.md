# Variant 0 — Status quo: a diagnostic of the pipeline as it stands today

Variant 0 changes nothing. This report documents the current system's real
behavior, grounded in this repo's source and the live numbers in
`docs/variant_analysis_data_snapshot_2026-10-19.md`. It follows the shared
schema (`docs/variant_analysis_shared_criteria.md`), sections 0–5 and 7.

**Verification scope note:** I read the pipeline source in this repo
(`embeddings/`, `ranker/`, `evaluator/`, `extractor/`, `preference_agent/`,
`scripts/rank_jobs.py`, `config.py`, `evaluation/harness.py`). I could NOT
reach the sibling `../JobAgentWeb` repo (`embeddings_repo.py`,
`migrations.py`, `dedup_repo.py`) — every file access outside this worktree
was denied by the sandbox. All JobAgentWeb-side facts below (vector storage
as JSON text, `dedup_repo.py`'s 0.60 confidence / 21-day window,
`usage_repository` cost rows) come from the snapshot file, not from my own
reading of that code.

## 0. Executive verdict

The status quo is a system that measurably works: apply rate falls
monotonically from 86.2% in rank bucket 1–5 to 14.3% in 16–20 (snapshot §6,
77 decisions), precision@5 is 0.80, would_apply precision is 0.80, and only
5 of 211 ranked-and-decided jobs (2.4%) diverged from the ranking — this is
not a broken pipeline. Its real quality cost is specific and known: a
7-for-7, 100%-consistent scorer bias against Python/"Lead"-title absence on
PHP-core roles that the learned profile has never absorbed (snapshot §4), a
preference profile that distills only 11 signals from 209 decisions
(snapshot §5), and two recurring human-rejection categories — spoken-language
requirements and cross-country duplicate mirrors — that the deterministic
filter does not check at all (snapshot §3). Its real cost cost is the ~79%
of auto-rejects that still pay a full Sonnet scoring call before being thrown
away (snapshot §2), embedded in the ~$2.25/typical-run, ~$3.14/100-jobs
baseline (snapshot §7). Variant 0's quality impact is "keep the measured
0.80/0.90 precision and keep the bias," its cost impact is "keep paying the
waste," and its implementation cost is zero. I would recommend it only as
the explicit baseline the other two variants must beat — as a standing
choice, it knowingly keeps paying for and re-correcting problems whose
fixing data (the extractor's `languages` and `skills` requirement fields)
is already sitting in `structured_data`.

## 1. Scope of change — is this evolution or revolution, concretely?

- **Files touched:** none. No module added, edited, or deleted.
- **Lines-of-code delta:** 0 added / 0 changed / 0 removed. (This report
  itself is the only artifact, and it is documentation, not pipeline code.)
- **Database schema:** unchanged — same Postgres tables, same JSON-text
  embedding column on the JobAgentWeb side (unverified directly; per the
  shared criteria and snapshot). State explicitly: **data model same**,
  trivially, because nothing changes.
- **Re-runs over historical data:** none required, none triggered. No
  re-embedding (Voyage vectors and `text_hash` in
  `embeddings/indexer.py::index_jobs` stay valid), no re-extraction
  (`FACT_SCHEMA_VERSION = 6` in `extractor/runner.py` stays put), no
  re-scoring. Note for contrast: any prompt change in the other variants
  would invalidate `score_fingerprint`s (`evaluator/runner.py::run`,
  `evaluator/fingerprints.py`) and force re-scoring of surviving jobs;
  variant 0 triggers exactly zero such spend.
- **One-sentence verdict:** this is **no change** because the deliverable is
  a diagnostic of the system as it stands; nothing in the pipeline, prompts,
  storage, or schema is modified.

## 2. Strengths and weaknesses, explicitly relative to the OTHER two variants

**Versus Variant 1 (evolution — process-only changes, same data model):**

- *Better at:* certainty of the measured quality. The 86.2% → 14.3%
  rank-bucket apply-rate gradient (snapshot §6) is produced by exactly the
  code running today — `ranker/fusion.py::fuse_by_rrf` (RRF k=60 over
  embedding + scorer legs), `ranker/listwise.py::listwise_rank` (Opus,
  shuffled presentation), `ranker/debate.py::debate_rank` (±3 nudge,
  dealbreaker_risk demotion). Variant 0 cannot regress it; any evolution
  change to prompts, fusion, or filter order makes those numbers
  non-comparable mid-job-search and requires re-validation on new
  decisions, at a measured-decision rate of only ~77 rank-bucket decisions
  so far. Evolution also invalidates `score_fingerprint`s on any scorer
  prompt change, triggering immediate re-scoring spend that variant 0
  avoids entirely.
- *Worse at:* leaving quantified money on the table that evolution could
  capture with the data already in hand. The extractor already produces
  `languages[]` with `requirement: required|preferred|working_language`
  and `skills[]` with `requirement`/`min_years`
  (`extractor/runner.py::_EXTRACT_TOOL`, schema v6), but
  `evaluator/dealbreakers.py::apply_dealbreaker_filter` never reads either
  — it checks only salary floor, remote/hybrid, geo, seniority, company
  type, `working_language`, and salary-disclosure. A variant-1 pre-filter
  over those existing fields would intercept some of the 79%
  score-then-auto-reject class (snapshot §2) for free; variant 0 keeps
  paying Sonnet for all of it.

**Versus Variant 2 (revolution — greenfield reset):**

- *Better at:* no cold-start. The preference profile (11 signals), the
  applied/rejected decision vectors that power max-sim retrieval
  (`embeddings/indexer.py::score_pool_by_similarity`), the few-shot
  applied/rejected examples in the scorer prompt
  (`evaluator/scorer.py::build_system_prompt`), and the calibration
  section fed by divergence cases all survive. Revolution wipes the
  profile by design; at this user's observed decision rate (209 decisions
  over several months, snapshot §5), personalization would be at or near
  zero for a comparable period, while variant 0 stays personalized on day
  one. Variant 0 also pays zero backfill; revolution must re-extract
  and re-embed the raw description text of the historical pool from
  zero.
- *Worse at:* everything structural. Revolution is the only variant that
  can address the storage/data model itself (JSON-text embedding column,
  per the shared criteria) and the distillation schema whose "one signal
  per dimension" rule over 8 fixed dimensions
  (`preference_agent/runner.py::_SYSTEM`) structurally caps the profile at
  low density — 11 signals from 209 decisions is near that schema's
  natural ceiling, so variant 0's sparsity is a property of the design, not
  a one-off. Revolution is also the only variant with learning value as a
  design driver; variant 0 keeps the candidate operating a pipeline whose
  techniques they have already fully learned.

## 3. Matching-quality analysis, grounded in real behavior

**Where the current system genuinely works (and variant 0 preserves it):**

- The apply-rate-by-rank-bucket table (snapshot §6) is the strongest real
  evidence: 86.2% (n=29) → 45.0% (n=20) → 38.1% (n=21) → 14.3% (n=7),
  monotonic across 77 decisions. The fused ranking
  (`ranker/fusion.py::fuse_by_rrf`, with the id-based tiebreak that
  prevents the embedding leg from silently double-weighting ties) followed
  by listwise Opus + debate genuinely orders jobs the way the user decides.
- precision@5 0.80, precision@10 0.90, would_apply precision 0.80 (16/20
  decided flags applied; `ranker/would_apply.py::compute_would_apply` gates
  on score ≥7.0 and rank ≤10), and only 5 divergence cases in 211
  ranked-and-decided jobs (2.4%). The pipeline also already closes some
  loops: divergence cases feed back into the scorer prompt
  (`evaluator/scorer.py::_build_calibration_section`, capped at 10 by
  `evaluator/runner.py::_CALIBRATION_LIMIT`) and into distillation
  (`preference_agent/runner.py::run`).

**Real, already-observed problems variant 0 leaves untouched:**

1. **The 79% score-then-throw-away waste.** Of auto-rejects, 393/500 (79%)
   arrived via Sonnet scoring ≤1.0, not the free filter (snapshot §2). The
   filter in `evaluator/dealbreakers.py` simply does not cover the reasons
   Sonnet rejects at ≤1 — the sample's explicit rejection reasons
   (snapshot §3) are dominated by spoken-language mandates, UK/other
   residency locks, and stack mismatches like "PHP entirely absent," none
   of which has a deterministic check, even though the extraction layer
   already captures `languages[].requirement`, `residency_requirement`,
   `work_authorization_requirement`, and `stack_required`
   (`extractor/runner.py::_EXTRACT_TOOL`). Variant 0 keeps paying ~$0.01
   of Sonnet per wasted job (run 12: $1.05 Sonnet leg over 91 jobs,
   snapshot §7), once per newly collected job, indefinitely.
2. **The 7-for-7 Python/"Lead"-title scorer bias.** Every dismissed item
   that exists (snapshot §4) is the scorer over-penalizing the absence of
   Python or a "Lead" title on an otherwise-good PHP role. The dismissal
   mechanism is wired up — dismissed items flow into distillation
   (`preference_agent/runner.py::_build_dismissed_section`, and `_SYSTEM`
   instructs folding them at conf ≥ HIGH) — yet the current 11-signal
   profile apparently carries no such signal (snapshot §5). Two structural
   reasons variant 0 does not fix this: (a) dismissals only reach the
   scorer indirectly through the distilled profile; the scorer prompt
   (`evaluator/scorer.py::build_system_prompt`) has no notion of
   "nice-to-have absence is not a con," and its MUST HAVE header actively
   instructs heavy penalties for missing listed requirements; (b) the
   tech_stack dimension gets one signal under the distiller's one-signal-
   per-dimension rule, and the true rule is two-sided ("PHP core required;
   Python nice-to-have is fine; Python-only is a reject" — the flip side
   appears 4× in the sample reasons as "PHP is entirely absent," snapshot
   §3), which a single ACCEPT/REJECT line cannot express. The user will
   keep hand-correcting this class of cons.
3. **Sparse preference yield.** 11 signals (5 ACCEPT / 4 REJECT / 2 INFER)
   from 209 decisions, ~1 per 19 decisions (snapshot §5). Given the
   distiller's fixed 8-dimension schema with one signal per dimension
   (`preference_agent/runner.py`), 11 is close to the schema's ceiling —
   the yield is structural, not a run-to-run accident, and variant 0 keeps
   it.
4. **Uncaught spoken-language rejections.** The candidate's explicit
   rejections include French-fluency-mandatory, C1 German hard requirement,
   Spanish-language description, Russia-linked offers (snapshot §3).
   `_working_language_reason` in `evaluator/dealbreakers.py` checks only
   the company's coarse `working_language` enum
   (`polish|english|both|unknown`) and never the extracted
   `languages[]` requirements; the posting-text language filter it defers
   to (`collector/language_filter.py`, per the comment) detects the
   *posting's* language, not an explicit spoken-language *requirement
   buried in an English posting*. These jobs pay a Sonnet call and then
   land in the user's review queue to be rejected by hand.
5. **Uncaught cross-country duplicate mirrors.** "Duplicate, published for
   different countries, already applied" appears 7× in a 25-reason sample
   of 154 reasoned rejections (snapshot §3). Nothing in this repo dedups
   at all; JobAgentWeb's `dedup_repo.py` (confidence 0.60, 21-day window,
   per snapshot §3 — unverified directly, see scope note) cannot catch a
   pattern the user says recurs across months. Variant 0 keeps surfacing
   these for manual rejection.
6. **A measurement gap worth flagging:** no pipeline run has been recorded
   in `usage_repository` between 2026-08-30 and today (snapshot §7). Variant
   0 keeps operating with two months of unobserved actual cost and no fresh
   per-leg decomposition to validate the baseline against.

**New failure modes introduced:** none. That is variant 0's defining
property. The only "new" risk is stagnation — every problem above is
already live and stays live.

## 4. User-behavior grounding

- **The 7 dismissed-item cases:** no behavioral change, plainly. The data
  is collected and fed to the distiller
  (`preference_agent/runner.py::_build_dismissed_section`,
  `dismissed_item_repository.get_recent(50)`), and the distiller is
  instructed to weight it at conf ≥ HIGH — but the empirical outcome
  (snapshot §4: the bias persists in the profile that exists today) shows
  the loop is too indirect and the one-signal-per-tech_stack slot too
  coarse to express it. Under variant 0 the candidate keeps dismissing
  the same category of cons by hand, at roughly the historical rate.
- **The "duplicate posting, different country, already applied" pattern
  (7+ occurrences):** no change. No cross-posting duplicate detection
  exists anywhere in this repo's pipeline, and the JobAgentWeb-side dedup
  window (21 days) is shorter than the observed recurrence interval.
  These jobs continue to be ranked, shown, and rejected manually.
- **The "required spoken language" pattern (uncaught by the deterministic
  filter):** no change. `_working_language_reason`
  (`evaluator/dealbreakers.py`) only enforces the company working-language
  enum and fails open on everything else by design; the already-extracted
  `languages[].requirement` data is never consulted. These postings keep
  costing a Sonnet scoring call and then the user's manual rejection,
  exactly as in the snapshot's §3 sample.

## 5. Cost analysis

**(a) One-time implementation cost: $0 and 0 engineer-hours.** No code is
written, no migration, no backfill, no one-off API spend. (For scale only:
the expensive backfills variants 1/2 would trigger — re-scoring on prompt
change, or full re-extraction/re-embedding under a new schema — are exactly
$0 here.)

**(b) Ongoing operational cost: unchanged, with a known waste share and a
two-month observation gap.** The real baseline (snapshot §7): ~$2.25 for a
typical 91-job daily run, `cost_per_100 ≈ $3.14`, dominated by extraction
(Haiku, $0.86) + scoring (Sonnet, $1.05) = 85% of run 12; embedding
($0.003) and rerank ($0.0014) are noise. Two caveats on the true current
number:

- The recorded runs used `claude-sonnet-4-6` ($3/$15) for scorer+debate;
  `config.py` now pins `claude-sonnet-5` ($2/$10), so today's scorer leg is
  plausibly ~30% cheaper (≈$0.70–0.75 vs $1.05 for a run-12-shaped job
  mix), putting a typical run nearer ~$1.90–2.00. But no run has been
  recorded since 2026-08-30, so this is an estimate from list prices
  (`config.py::MODEL_COSTS`) applied to run 12's token mix, not an observed
  figure.
- The 79% waste (snapshot §2) is embedded in that number and variant 0
  keeps it: at run 12's observed ~$0.012 of Sonnet per evaluated job, and
  with 3783 auto-rejects of 5259 terminal jobs (72%, snapshot §1) of which
  79% paid the scorer first, roughly half or more of all-time Sonnet
  scoring spend has gone to jobs thrown away at ≤1 — order of $0.5–0.6 of
  run 12's $1.05 Sonnet leg, i.e. on the order of $25–35 across the $60.07
  of recorded spend, subject to the assumption that scoring volume in the
  recorded window resembles the lifetime mix. Per-job waste is incurred
  once (scores are computed only for unscored or fingerprint-mismatched
  jobs, `evaluator/runner.py::run`), so it scales with collection volume,
  not with days elapsed.

Variant 0 neither raises nor lowers the baseline. It forgoes the savings
a pre-filter over existing `structured_data` fields could capture
(targeting the extraction/scoring legs, the correct 85% of the cost, not
the embedding layer — snapshot §7's own guidance).

## 7. Self-scored summary

- `MATCHING_QUALITY_SCORE: 7/10` — the rank-bucket apply-rate gradient
  (86.2% → 14.3%) and 2.4% divergence rate show the ranking genuinely
  works, but the uncorrected 7-for-7 Python/"Lead" bias, the uncaught
  spoken-language and cross-country-duplicate classes, and the
  structurally sparse 11-signal profile cap it well below the top.
- `ONGOING_COST_PER_DAY_USD: $2.25` — unchanged from the historical
  baseline per section 5(b), including the ~79%-of-auto-rejects Sonnet
  waste it embeds; plausibly ~$1.90–2.00 today on the cheaper
  sonnet-5 scorer leg, but unobserved since 2026-08-30.
