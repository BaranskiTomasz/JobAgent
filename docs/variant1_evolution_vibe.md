# Variant 1 — Evolution: process-only changes over the existing data model

Analysis-only deliverable. No pipeline code was changed. Written against this repo's
actual source plus `docs/variant_analysis_data_snapshot_2026-10-19.md` (treated as
ground truth). Prior art read and built on: `docs/rag_modernization_proposal_codex.md`
and `docs/rag_modernization_proposal_vibe.md`.

**Access caveat:** every attempt to read the sibling JobAgentWeb repo
(`../JobAgentWeb/embeddings_repo.py`, `migrations.py`, and by implication
`dedup_repo.py`) was denied by this session's approval callback. Statements about
JobAgentWeb internals (the `job_embeddings` JSON-text column contract, the dedup
confidence 0.60 / 21-day window) rely on the task description, the snapshot file, and
what JobAgent-side code documents about the contract — same caveat the prior vibe
proposal recorded. Anything downstream that depends on `dedup_repo.py` is flagged as
needing verification.

## 0. Executive verdict

Variant 1 is the highest value-per-dollar of the three variants, and I would recommend
it as the immediate next step regardless of what anyone decides about variant 2. The
core finding of this analysis is that the pipeline is already extracting most of the
signal it needs to fix its own worst inefficiencies and is simply not using it:
`extractor/runner.py`'s schema v6 extracts a `languages[]` array with
`requirement: required|preferred|working_language` and levels, a `skills[]` array with
`requirement`/`min_years`, `country_eligibility`, and `work_authorization_requirement`
— and `evaluator/dealbreakers.py` reads exactly none of that, checking only seven
company-level fields. That is why 79% of auto-rejects still pay for a full Sonnet call
before being thrown away at score ≤1 (snapshot §2). Deterministic filters over already-
extracted facts, a scorer-prompt calibration fix for the 7-for-7 Python/"Lead"-title
bias (snapshot §4), a required-spoken-language dealbreaker, a local cross-posting
duplicate detector, and a structured-facet third leg through
`ranker/fusion.py::fuse_by_rrf(extra_rank_field=...)` together fix most of the real,
observed failure patterns for roughly a week of engineering and under $10 of one-off
API cost. Data model and storage change not at all. Ongoing cost drops from the
~$2.25/run baseline to roughly $1.60–1.90/run because the expensive legs (extraction +
scoring, 85% of run cost per snapshot §7) get less to do, not because anything gets
cheaper.

## 1. Scope of change — is this evolution or revolution, concretely?

**Files touched (all proposed, none implemented):**

| File | Change | Rough LOC |
|---|---|---:|
| `evaluator/dealbreakers.py` | Add `_required_spoken_language_reason` reading `structured_data.languages[]` (`requirement == "required"`, level vs. `_candidate_language_codes()`); optionally add must-have-tech absence check when `criteria_repository` has MUST HAVE entries | +70–100 |
| `evaluator/scorer.py` | Prompt calibration: absent Python on PHP-core roles and absent "Lead" title are never cons; add a bounded CANDIDATE CORRECTIONS section built from `dismissed_item_repository` | +25–35 |
| `evaluator/runner.py` | Wire dismissed-item corrections into `build_system_prompt` (mirrors the existing `_CALIBRATION_LIMIT` pattern for divergence cases) | +15 |
| `scripts/rank_jobs.py` | Deterministic structured-facet score (stack overlap / `stack_required` coverage / seniority match over `structured_data`) as the third leg in the first `fuse_by_rrf` call; local near-duplicate detection over the ranking pool (normalized title+company and normalized description-prefix match across country mirrors), tagging `[DUPLICATE?] ` in `rank_reason` (same pattern `ranker/exploration.py` established for `[EXPLORATION] `) | +80–120 |
| `preference_agent/runner.py` | Distillation prompt: allow per-correction signals that don't fold into the 8 fixed dims; explicitly instruct that dismissed-factor patterns become ACCEPT/REJECT signals at conf ≥ HIGH | +15–20 |
| `embeddings/indexer.py` | Raise `_job_to_text`'s `excerpt[:2000]` to the full `build_excerpt` length (6000/8000 chars) | 1 line |
| New `evaluation/replay.py` (or similar) | Frozen-snapshot replay harness per the two prior proposals, in their offline form: JSON snapshot via the existing JobAgentWeb API, stage-toggleable replay, `ranx`-style metrics; no new tables | +250–350 (new file) |
| `config.py` | New tunables (facet-leg weights, duplicate thresholds, corrections cap) | +10 |
| `evaluation/harness.py` / run-summary wiring | Fix the §7 gap: no runs have been logged under `record_run_summary` since 2026-08-30; make every run record, or none of the cost claims below are verifiable going forward | +10–20 |

Total: roughly **+450–550 added, ~40 changed, 0 removed**. Nothing deleted; no new
service; no new dependency except optionally `ranx` in the eval harness only.

**Does the database schema change?** No. Data model same. Same Postgres tables, same
JSON-text embedding column, same `structured_data` JSON blob (the new dealbreaker
reads fields the extractor already writes into it), same `candidate_preferences`,
same preference-profile storage. No migration, no backfill of any table. The only
storage-adjacent action is re-embedding the active pool with longer text — new vectors
in the *existing* `job_embeddings` rows, keyed by the existing `text_hash`
invalidation mechanism, which `scripts/rank_jobs.py` already handles automatically.

**Does it require re-running anything expensive over historical data?**

- **Re-embedding:** yes, once, because of the truncation fix — ~2,000 active-pool jobs
  at ~1,800–2,500 tokens each ≈ 3.6–5M tokens × $0.18/M (`config.py` `MODEL_COSTS`)
  ≈ **$0.65–$0.90 one-off** (same estimate the prior vibe proposal made). Ongoing
  per-job embed cost roughly triples from ~$0.0001 to ~$0.0003/job — still noise
  (embedding was $0.003 of run 12's $2.25, per snapshot §7).
- **Re-scoring:** effectively free by timing — the scorer-prompt change alters
  `score_fingerprint`, so the current `new` pool re-scores on the next run, and the
  `new` count at snapshot time is **1** (snapshot §1). Historical jobs are terminal
  (`reviewed`/`applied`/`rejected`/`auto_rejected`) and are never re-scored by the
  normal path (`evaluator/runner.py` scores `new`-status jobs only), so there is no
  3,783-job backfill implied. Optional eval-harness experiments (a 150–300-job frozen
  labeled rescore grid) cost roughly **$1–$4 one-off** using the ~$0.007/sonnet-scored-job
  historical rate implied by run 12 ($1.05 scorer+debate over 91 jobs, snapshot §7)
  and the prior proposals' estimates.
- **Re-extraction:** **not required.** `languages[]`, `skills[]` with
  `requirement`, `country_eligibility` etc. already exist in fact schema v6
  (`extractor/runner.py`, `FACT_SCHEMA_VERSION = 6`). The dealbreaker filter runs
  only over `new` jobs, which are schema-v6 by construction; older rows fail open,
  consistent with the file's existing conservative style.

One-sentence verdict: **this is an evolution because every change is a prompt, a
filter, a ranking leg, or a measurement over data the system already extracts and
stores — the tables, columns, and storage format are untouched.**

## 2. Strengths and weaknesses, explicitly relative to the other two variants

**vs. Variant 0 (status quo):**

- Better: it attacks the snapshot's quantified waste instead of living with it —
  79% of auto-rejects paying for a Sonnet call before disposal (§2), the 7-for-7
  scorer bias the candidate personally corrected seven times (§4), and the two
  recurring human-rejection categories (cross-country duplicates ×7+, spoken-language
  requirements) that the deterministic filter does not check at all (§3). Variant 0
  keeps paying all of that forever.
- Worse: variant 0 has zero implementation risk and zero chance of a new failure
  mode; variant 1 introduces deterministic rejections that can be wrong (see §3,
  "new failure modes") and needs the discipline of the replay harness to prove each
  change helps before it ships. Variant 0 is also cheaper to *have* — $0 of
  engineering — which matters if the pipeline is about to be discarded anyway.

**vs. Variant 2 (revolution):**

- Better: no cold start. Variant 1 preserves the 209-decision learned profile, the
  86.2% top-5 apply rate gradient, and the 2.4% divergence rate (§5, §6) from day one;
  every change is independently deployable and independently revertible, and the
  cheap wins (language dealbreaker, prompt calibration) pay off within one run.
  Variant 2 wipes the preference profile and, per the snapshot's decision velocity
  (209 decisions over several months), spends a long time re-earning what variant 1
  keeps.
- Worse: variant 1 inherits every accumulated design compromise the data model
  forces — one JSON-text vector per posting (no per-facet or multi-representation
  storage), the 8-dimension fixed distillation schema in
  `preference_agent/runner.py` (which is *why* only 11 signals exist, see §3), and a
  duplicate detector bolted onto the ranker instead of fixing collection-side dedup
  properly. Variant 2 can design preference modeling and extraction from zero;
  variant 1 can only loosen the prompts inside the existing 8-dim contract. Its
  ceiling on matching quality is genuinely lower.

## 3. Matching-quality analysis, grounded in real behavior

The honest baseline first: the current pipeline is not broken. Apply-rate by rank
bucket (86.2% → 14.3%, monotonic, n=77; snapshot §6), precision@5 0.80, would_apply
precision 0.80, and a 2.4% divergence rate all say ranking correlates with real
decisions. Variant 1's job is to fix the specific, observed defects without
disturbing that gradient — which is why every item below ships gated on the replay
harness (adopted from both prior proposals, which agree on this point; I concur with
them that it is the prerequisite change, and note variant 1 can adopt it entirely in
its offline, no-new-tables form).

**Problem 1 — 79% of auto-rejects still pay for Sonnet (snapshot §2).**
Position: fix, deterministically, using already-extracted facts. The current filter
(`evaluator/dealbreakers.py::apply_dealbreaker_filter`) checks salary floor, remote
presence, geo, seniority, company type, `working_language`, and no-salary — and only
on `_trusted_fields` (high-confidence extractor fields). Meanwhile the extractor's
own schema v6 captures per-job `languages` with `requirement: required`, `skills` with
`requirement: required` and `min_years`, `role_family`, `seniority_min/max`, and
`work_authorization_requirement`. The gap between what is extracted and what is
enforced *is* the 79%. Concretely I would add, in order of expected catch rate:

1. Required-spoken-language check (see Problem 3a below) — directly addresses the
   French/German/Spanish rejections in §3.
2. Must-have-tech absence: snapshot §8 says pure-Python-without-PHP postings are a
   large fraction of what scores moderately then gets thrown away, and the §3 sample
   contains "PHP is entirely absent" ×4. When the candidate's MUST HAVE criteria are
   configured (the OR-semantics list the scorer already enforces in
   `evaluator/scorer.py::build_system_prompt`), a job whose `role_family` is
   backend/fullstack-ish and whose high-confidence `skills[]`/`stack` contains none
   of the MUST HAVE entries can be auto-rejected without the Sonnet call. I would
   gate this hard: high-confidence fields only, correct `role_family` only, reason
   logged, status reversible (the existing `force_rescore` recovery path in
   `evaluator/runner.py` already restores dealbreaker-rejected jobs to `new`).
3. Work-authorization/residency check from the already-extracted
   `work_authorization_requirement`/`residency_requirement` fields, against the
   candidate's work country — the §3 "authorisation to work in the United Kingdom"
   and UK-residency-locked rejections.

What I would deliberately NOT do: replace or pre-stage the Sonnet scorer with a
Haiku pre-scorer. It looks like the obvious cost fix, but it swaps an auditable
failure mode (deterministic rule fires or doesn't) for an opaque one (Haiku
false-rejects a marginal job the candidate would have wanted), and with the
deterministic filters in place the residual Sonnet volume is the jobs worth a real
score. Also deliberately not "fixed": the auto_reject threshold itself
(`SCORING["auto_reject_at_or_below"] = 1.0` in `config.py`) stays — the snapshot gives
no evidence it misfires.

Sizing, honestly: the snapshot does not say what the 79% *consists of*, so I cannot
claim a precise interception rate. But the two known-recurring categories inside it
(no-PHP roles, spoken-language requirements) plausibly cover 30–50% of it; every
intercepted job saves ~$0.007–0.0115 of Sonnet spend (run 12: $1.05 scorer+debate over
91 jobs). At a 91-job run's volume that is roughly $0.25–0.45/run — see §5(b).

**Problem 2 — the 7-for-7 Python/"Lead"-title scorer bias (snapshot §4).**
Position: fix in the scorer prompt, immediately; do not wait for the preference
profile to absorb it. All 7 dismissals are the same bias in the same direction:
over-penalizing absent Python or an absent "Lead" title on otherwise-strong PHP roles.
The candidate's own corrections are unambiguous ("Python is nice have requirement",
"Engineer is satisfactory for me"). The preference pipeline *intends* to catch this —
`preference_agent/runner.py` feeds dismissed items into the distiller at conf ≥ HIGH
(`_build_dismissed_section`) — but the distilled profile did not absorb it (§5: the
bias "never became a signal"). Three process fixes:

1. A calibration paragraph in `evaluator/scorer.py::build_system_prompt`: absent
   Python is never a con when the role's core matches the candidate's primary stack;
   absent "Lead"/"Architect" title is never a con for senior-level roles. One prompt
   edit, effective on the next run, auditable by reading new cons.
2. Feed the dismissed items themselves (bounded, like `_CALIBRATION_LIMIT`) into the
   scorer prompt as a CANDIDATE CORRECTIONS section — the same pattern
   `_build_calibration_section` already uses for divergence cases. Today dismissals
   only reach the distiller, which runs occasionally; the scorer never sees them.
3. Filter cons matching previously-dismissed item text from `score_breakdown` before
   display, so the same wrong con cannot reappear.

Risk of overcorrection (never penalizing genuinely Python-primary-no-PHP roles) is
handled by wording: the instruction is scoped to roles matching the candidate's
primary stack, which is exactly the §8 profile (PHP/Symfony core, Python secondary).

**Problem 3a — required spoken language, buried in the description.**
Position: fix deterministically, at zero marginal LLM cost. This is the cleanest
find of the analysis: the extractor *already* extracts
`languages: [{language, level, requirement: required|preferred|working_language,
evidence}]` (schema v6, `extractor/runner.py`), and the dealbreaker module *already*
has the right comparison primitive (`_candidate_language_codes()` — the candidate's
working-level ≥B2 language codes, reused from `collector/language_filter.py`). The
existing `_working_language_reason` checks only the company-level
`working_language` enum (polish/english/both/unknown) and cannot see "C1 German
language proficiency is listed as a hard mandatory requirement" (§3). A
`_required_spoken_language_reason` that rejects when `languages[]` contains
`requirement == "required"` for a language not in `_candidate_language_codes()`,
failing open when the field is absent or the level is unstated, catches the
French/German/Spanish cases in §3 before Sonnet is paid. Note the posting-*language*
filter (`collector/language_filter.py`) already exists and handles
"description is in French/Spanish" — the §3 cases that got through are requirement-
in-English-posting cases, which is exactly the gap this closes. (I cannot explain why
"the offer description is in spanish" survived `apply_language_filter`; possibly it
predates the filter or langdetect demurred — flagged as an unverified gap.)

Deliberately not fixed deterministically: the Russia-exclusion case ("I won't even
consider offers connected to Russia"). A deterministic banned-country check needs a
structured banned-list preference field, which is a questionnaire/`candidate_preferences`
schema change — outside variant 1's hard constraint. It stays with the LLM scorer via
`open_notes` free text (already injected by
`evaluator/profile.py::load_questionnaire_preferences`), which is where it lives today.

**Problem 3b — cross-country duplicate postings ("duplicate, already applied", 7+ occurrences).**
Position: fix cheaply and deterministically, on the JobAgent side, as a *demote-and-
tag* rather than a hard reject. The pattern (same ad republished for different
countries) produces near-identical `title + company + description` triples, and
`scripts/rank_jobs.py` already holds the full pool with descriptions in memory. A
local detector — normalize title (strip country/location tokens), normalize company,
and hash a normalized description prefix — groups country mirrors cheaply; keep the
group member matching the candidate's work country/remote eligibility and tag the
rest `[DUPLICATE?] ` in `rank_reason` (the exact tagging pattern
`ranker/exploration.py` established for `[EXPLORATION] `), plus a debate-style demote.
Why not hard-reject: fuzzy matching false-positives are plausible (two similar ads at
the same company), the candidate's rejection reason implies "already applied", not
"bad job", and JobAgentWeb's own `dedup_repo.py` (confidence 0.60, 21-day window —
too short for a pattern that recurs across months, per the snapshot) remains the right
place for a durable fix; lengthening its window is a JobAgentWeb *code* change with no
storage impact, but I could not verify that file (access denied), so I mark it
recommend-to-verify rather than commit-to-detail.

**Problem 4 — 11 signals from 209 decisions (snapshot §5).**
Position: yes, this is a process problem, and it is fixable without a data-model
change — but only partly. Two causes are visible in `preference_agent/runner.py`:

1. The contract is 8 fixed dimensions with "one signal per dimension", so the
   theoretical maximum is ~10–12 signals and 11 output is the schema working as
   designed, not the model being lazy. The schema is why the 7 dismissed corrections
   (which map cleanly onto tech_stack and seniority) produced no visible change: the
   instruction "fold each into whichever dimension it belongs to" dissolves a sharp
   correction ("Python absence is not a con") into a coarse dimension whose signal
   may already be occupied.
2. Nothing forces the distiller to reconcile the profile against the dismissed
   factors it was *shown* — it is told to weight them "at least conf=HIGH", but no
   output slot exists for "the candidate explicitly corrected the evaluator here."

The fix is prompt-level: permit free-form correction signals alongside the 8 dims
(the `submit_profile` tool schema already allows free-form `value` and `note` per
signal — no tool-schema change needed, only the prose rules), and require the
distiller to emit one ACCEPT/REJECT signal per distinct dismissed-factor pattern.
The deeper limitation — the profile is a flat list re-distilled wholesale, not a
per-facet store — is inherited from the data model and is variant-2 territory;
variant 1 should say so plainly rather than pretend a prompt can fix it.

**The structured-facet RRF leg (prior art, adopted).** Both prior proposals recommend
a deterministic facet leg through `ranker/fusion.py::fuse_by_rrf(extra_rank_field=...)`;
I concur under the evolution constraint and it is nearly free: a stack-overlap /
`stack_required`-coverage / seniority-match rank over `structured_data` fused as the
third leg in the first RRF pass (the one selecting the top-50 rerank pool in
`scripts/rank_jobs.py`, which currently calls `fuse_by_rrf(ranking_eligible)` with no
extra leg). It directly serves the observed quality problem that dense similarity
blurs exact discriminators — the same-posting-different-stack confusion behind "6+
years of Magento 2 required" (§3) being scored as a near-match. Zero API cost. I add
one caution neither proposal states: the facet leg and the dense leg are not
independent for PHP-heavy postings (the decision vectors are themselves PHP-role
embeddings), so RRF double-counts stack agreement — acceptable, but the replay
harness should measure the leg's lift rather than assume it.

**The embedding truncation (prior art, adopted as in-scope).** Raising
`embeddings/indexer.py::_job_to_text`'s `excerpt[:2000]` to the full excerpt is a
representation change, not a storage change — same table, same JSON column, new
`text_hash`, one-off ~$0.65–0.90 re-embed (§5). Both prior proposals treat it as the
one real recall defect; I agree and keep it in variant 1. The snapshot's §3 evidence
(requirements buried mid-posting, e.g. the Magento/Azure "must-have" lists) is
consistent with requirements routinely living past char 2000 of an 8000-char LinkedIn
excerpt.

**New failure modes this variant risks introducing.** (a) Deterministic language/
must-have filters false-rejecting: mitigated by high-confidence gating, fail-open on
missing fields, `requirement == "required"` only, and the existing reversible
auto_reject status. (b) Duplicate tagger false positives: mitigated by demote-not-
reject. (c) Prompt calibration overcorrecting the other way (under-penalizing genuine
Python-primary roles): mitigated by scoping the instruction to roles matching the
primary stack, and measurable via the harness. (d) Facet leg double-counting stack
agreement (above). The replay harness is what keeps all four honest — which is why
it ships first.

## 4. User-behavior grounding

**The 7 dismissed-item cases (snapshot §4):** treated differently in three concrete
ways. The scorer prompt stops generating those cons at all (calibration paragraph);
the candidate's own dismissal texts are injected into every scoring call as a
CANDIDATE CORRECTIONS section, so the correction has effect immediately instead of
waiting for a distillation cycle; and the distiller is required to emit explicit
ACCEPT signals for the dismissed patterns ("tech_stack: python-absence-not-a-con",
"seniority: lead-title-not-required"), so the learned profile finally encodes what
the candidate has said seven times in writing. The emagine Polska, Lendable, and
LUMASERV cons ("No mention of Python, LLM/AI tooling", "Lead/architect title not
offered") would not reappear, and those roles' scores stop being dragged by phantom
cons — which matters not just cosmetically: score is one of the two RRF legs deciding
what reaches the top-50 rerank pool.

**The cross-country duplicate pattern (7+ occurrences in the §3 sample):** the local
detector groups country mirrors of one ad in the ranking pool, tags all but the
best-eligible member `[DUPLICATE?] ` and demotes them, so the candidate stops seeing
— and manually re-rejecting with "Duplicate. The same job advertisment is publish
for different countries" — the same posting week after week. It is a demote-and-tag,
not an auto-reject, so a false grouping costs a lower rank, not a lost job. The
JobAgentWeb-side fix (widen the 21-day dedup window) is recommended but flagged
unverified (file access denied).

**The required-spoken-language pattern:** caught deterministically before any paid
call, for schema-v6 jobs, via the already-extracted `languages[]` field — "French
language fluency is listed as mandatory" and "C1 German ... hard mandatory
requirement" become free auto-rejects with a logged reason, instead of a paid Sonnet
score followed by a human rejection. The Russia-exclusion case is the one §3 pattern
this variant deliberately does NOT catch deterministically: doing so needs a
structured banned-countries preference field (a questionnaire data-model change),
so it remains with the LLM scorer via `open_notes` — same behavior as today, stated
plainly rather than glossed.

## 5. Cost analysis

**(a) One-time implementation cost.** Assumption: the repo's author (one senior
engineer, deeply familiar with both repos) does the work. Engineering effort by item:
replay harness 2–4 days (both prior proposals agree on this range and I adopt it),
dealbreaker additions 0.5–1 day, scorer-prompt calibration + corrections wiring 0.5
day, duplicate detector 1 day, facet leg 0.5–1 day, distiller prompt 0.5 day,
truncation fix + re-embed 0.5 day, run-summary logging fix 0.5 day. Total ≈ **6–9
days**. One-off paid API cost, using snapshot §7's real rates: re-embed of the ~2,000
active pool ≈ 3.6–5M tokens × $0.18/M ≈ **$0.65–0.90**; optional eval-harness
experiment grid (150–300-job Sonnet rescore variants) ≈ **$1–4**; one re-distillation
Opus call ≈ **$0.40** (prior vibe proposal's historical estimate, consistent with
Opus $5/$25 in `config.py`); scorer-prompt-change rescore of the current `new` pool =
1 job ≈ $0.01 (snapshot §1). Total one-off paid: **under $10**. No re-extraction
backfill is required (see §1).

**(b) Ongoing day-to-day operational cost.** The real baseline (snapshot §7): a
typical daily run is ~$2.25 (run 12, 91 jobs; $3.14 per 100 jobs), dominated by
extraction (Haiku $0.86) + scoring (Sonnet $1.05) = 85% of the run; embedding
($0.003) and rerank ($0.0014) are noise. Two adjustments to that baseline before my
delta: (i) the historical runs priced scorer/debate at claude-sonnet-4-6 ($3/$15)
while `config.py` now uses claude-sonnet-5 ($2/$10), so today's true baseline is
plausibly ~$1.95–2.05; (ii) no runs have been logged since 2026-08-30 (§7's own gap),
so recent reality is unverified — fixing run-summary logging is part of this variant
precisely so this section becomes checkable. Variant 1's delta:

- Down: deterministic interception of part of the 79% (§2) removes Sonnet scoring
  calls at ~$0.007–0.0115/job. If the new checks intercept 30–50% of what the
  scorer currently rejects — a stated estimate, not a measurement, since the
  snapshot does not decompose the 79% — the Sonnet leg drops ~$0.30–0.50/run at
  run-12 volume. The language and duplicate checks also remove jobs before ranking
  and before some Haiku extraction is re-attempted on them, a smaller second-order
  saving.
- Up (negligible): facet leg $0 (pure Python over already-fetched data); duplicate
  detector $0; longer embeddings ~+$0.0002/job; corrections section lengthens the
  scorer system prompt slightly (cached per batch via the existing
  `cache_control` ephemeral block, so the marginal token cost is one cache-write per
  run, not per job).

Net steady state: roughly **$1.60–1.90 per typical daily run**, i.e. ~15–30% below
the $2.25 historical baseline, driven by less scoring volume — which is exactly
where the snapshot says the money is ("any cost-reduction proposal should target
extraction/scoring volume", §7). I want to be explicit that the confidence interval
here is wide until the new filters have run for a few weeks with logging restored;
the direction (down) is much more certain than the magnitude.

## 7. Self-scored summary

- `MATCHING_QUALITY_SCORE: 8/10` — the ranking gradient is already strong (86.2%
  top-5 apply rate, 2.4% divergence), and variant 1 removes the three concrete,
  human-verified defect classes (7-for-7 scorer bias, uncaught spoken-language
  requirements, cross-country duplicates) plus adds the facet leg, without
  disturbing what works; it loses the last 2 points to the 8-dim distillation
  ceiling and JSON-vector storage it is forbidden to redesign.
- `ONGOING_COST_PER_DAY_USD: $1.75` — the midpoint of the §5(b) $1.60–1.90 range:
  the historical $2.25 baseline minus an estimated 30–50% interception of the 79%
  of scorer-rejects, with embedding/rerank legs remaining noise, and with the
  honest caveat that this number is unverifiable until run-summary logging is
  fixed and the filters have a few weeks of recorded runs.
