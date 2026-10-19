# Variant 2 — Revolution: from-zero rebuild of the job-postings pipeline and preference learning

Written to the shared schema in `docs/variant_analysis_shared_criteria.md`, grounded in
`docs/variant_analysis_data_snapshot_2026-10-19.md` (cited below as "snapshot §N") and the
actual pipeline source. Analysis only; no pipeline code was modified.

Assumptions stated up front, per the schema's honesty rule: I had no live-DB or network
access; everything numeric comes from the snapshot file or `config.py`'s recorded prices.
Historical dollar figures come from 18 recorded runs (snapshot §7), all of which ran the
scorer on `claude-sonnet-4-6` ($3/$15) rather than today's `claude-sonnet-5` ($2/$10)
(`config.py:MODEL_COSTS`), so historical Sonnet-leg costs overstate today's by roughly a
third; I adjust where it matters and say so.

## 0. Executive verdict

Variant 2 is worth doing mainly if the learning/CV driver is real, because the pure
matching-quality case is thin: the current pipeline's ranking already correlates strongly
with real decisions (apply rate 86.2% at rank 1–5 falling to 14.3% at 16–20, divergence
only 2.4%, snapshot §6), so a rebuild buys little at the top of the list and risks a
transition regression while the rebuilt preference profile re-warms. Where a rebuild
genuinely wins on quality and cost, concretely and tied to observed failures: a
spoken-language hard gate (rejection reasons in snapshot §3 show French/German/Spanish
recurring kills that `evaluator/dealbreakers.py` never checks), company-level
duplicate/mirror keying with no time window (the "same ad, different country, already
applied" reason appears 7+ times in one 25-reason sample, snapshot §3), dismissal-driven
preference weights that would have killed the 100%-consistent Python/"Lead"-title
over-penalization (all 7 dismissed items, snapshot §4), and staged cheap-model gating
that intercepts part of the 79% of auto-rejects that currently pay full Sonnet scoring
before dying (snapshot §2). The one-time cost is roughly 60–90 engineering hours plus a
backfill in the tens of dollars (re-extraction of ~5,259 jobs), and steady-state cost
drops to roughly $1.00–1.40/day from the $2.25 baseline (snapshot §7). The wipe has a
real, quantifiable cost: at the historical decision rate (~209 decisions over several
months, snapshot §5) an implicitly-rebuilt profile takes ~19–21 weeks to reach coverage
comparable to today's 11 signals, which is exactly why the rebuild centers on explicit
elicitation (a ~45–90 minute pairwise-comparison session producing a usable day-one
profile). My honest recommendation: do it, but sequenced so the deterministic wins
(language gate, dedup keying, staged gating) land first and independently of the
preference-learning rebuild, because each of those is individually justified and none
of them actually requires the full revolution.

## 1. Scope of change — is this evolution or revolution, concretely?

**This is a revolution because every stage downstream of the raw description text is
replaced, the data model is replaced, and the historical per-job AI outputs (extraction
facts, scores, ranks, preference signals) are discarded and re-derived from the kept raw
text.**

Touched, added, or deleted:

- Deleted / replaced wholesale: `preference_agent/runner.py` (308 lines) and its
  Opus-distillation design — replaced by an explicit-elicitation + Bradley-Terry (or
  Elo) preference learner. `embeddings/indexer.py`'s scoring half
  (`score_pool_by_similarity`, `_max_scores`, ~60 lines) — the max-sim-minus-0.3×rejected
  heuristic is replaced by a learned linear utility model. `ranker/debate.py` (227 lines)
  — dropped; its job (catching dealbreakers the primary pass missed) moves into the
  deterministic gate plus the first-stage cheap scorer, because debate is a workaround
  for missing structure, not a signal source of record (it cost real money, ~15% of run
  12 with snapshot §7, for a ±3-rank nudge).
- Heavily reworked: `extractor/runner.py` (682 lines) — the single ~45-required-field
  `FACT_SCHEMA_VERSION = 6` call becomes a two-tier schema (tier 1: cheap hard facts for
  gating; tier 2: full profile, only for survivors). `evaluator/dealbreakers.py` (293
  lines) — gains spoken-language and duplicate/company gates. `evaluator/scorer.py` (286
  lines) — restructured into the tiered scorer. `scripts/rank_jobs.py` (223 lines) —
  pipeline order changes. `ranker/fusion.py`, `ranker/reranker.py`, `ranker/listwise.py`
  survive in recognizable form (RRF fusion, cross-encoder, listwise LLM are still the
  right bones) but read new inputs.
- Added: an elicitation UI/session module (pairwise comparison presentation, active
  selection of pairs), a pairwise-preference learner, a company/posting canonicalization
  module (minhash/SimHash over normalized description text + company keys), and an
  offline evaluation harness that replays the kept decisions against any candidate
  ranker.
- Rough LOC delta: +2,500–3,500 added/rewritten, −800–1,200 removed, net pipeline size
  roughly similar but with most complexity moved from prompt-orchestration code into a
  schema + one small learner.

**Data model: changed.** New tables for pairwise comparisons (choice, pair, timestamp,
presentation order), decisions with structured reason codes (the free-text
`rejection_reason` stays but is additionally coded), a canonical-company / canonical-posting
table with content-hash keys, and per-job feature vectors stored as native columns or
`pgvector` rather than the current JSON-text embedding column. Migrations with backfills:
canonicalization of all ~5,259 historical jobs (1,178 reviewed + 76 applied + 221
rejected + 3,783 auto-rejected + 1 new, snapshot §1), and re-extraction under the new
schema.

**Re-running expensive work over historical data: yes, twice.**
- Re-extraction of everything with a description: ~5,259 jobs. At run 12's real blended
  rate (Haiku extraction $0.86 / 91 jobs ≈ $0.0095/job, snapshot §7) that is ~$50 on
  `claude-haiku-4-5`; on the newer `claude-haiku-5-5` ($0.10/$0.50, one tenth the rate,
  `config.py:MODEL_COSTS`) it is ~$5–15 depending on how much tier-2 extraction the
  backfill does. Budget $15–50.
- Re-embedding: at run 12's real Voyage rate ($0.003 per ~91-job run ≈ $0.000033/job),
  all 5,259 jobs ≈ $0.17. Negligible; noted only to show I sized it.
- Re-scoring history: not done as a backfill — old scores are discarded, not recomputed,
  and the offline harness evaluates new rankers against the kept decisions (76 applied /
  221 rejected) instead of re-paying Sonnet over the corpus.

## 2. Strengths and weaknesses, explicitly relative to the OTHER two variants

**Versus Variant 0 (status quo):**
- Better at: fixing the three recurring human-rejection categories the current
  deterministic filter provably misses — spoken-language requirements (snapshot §3:
  French/German/Spanish/Russia reasons), country-mirror duplicates (7+ occurrences in a
  25-reason sample), and the salary-adjacent UK-residency/geo lock cases — because the
  rebuild makes every hard gate a schema-level field with provenance, rather than the
  current state where the extractor already produces a `languages[]` array
  (`extractor/runner.py`, `_EXTRACT_TOOL` "languages" field with `requirement` enum) that
  `evaluator/dealbreakers.py` simply never reads. Variant 0 keeps all of these misses by
  definition.
- Worse at: risk-free continuity. Variant 0 has 209 decisions of accumulated calibration
  behind its preference profile and a demonstrated 86.2% top-bucket apply rate; variant 2
  throws that away and runs a transition period where the new profile is unproven against
  this specific candidate. A rebuild bug can silently auto-reject good jobs at scale
  (3,783 jobs already died in `auto_rejected`; a worse gate kills more, and that
  destruction is only partially reversible since descriptions go stale).

**Versus Variant 1 (evolution, same storage, process-only changes):**
- Better at: the cost problem that actually matters. Snapshot §2 says 79% of auto-rejects
  (393/500 sampled) paid a full Sonnet scoring call before dying, and snapshot §7 says
  extraction+scoring is 85% of a $2.25 run. Variant 1 can tweak prompts and filtering
  order within the existing architecture, but only a schema-level redesign (tiered
  extraction + tiered scoring, where tier 1 is `claude-haiku-5-5` at $0.10/$0.50 versus
  Sonnet at $2/$10) restructures *what gets paid at all*. It also beats variant 1 on the
  cold-start problem: variant 1 must keep the implicit applied/rejected distillation that
  yielded 11 signals per 209 decisions (snapshot §5); variant 2 replaces it with explicit
  elicitation that produces a usable profile on day one.
- Worse at: shippable incremental value. Everything variant 2 does well takes weeks and
  lands as a big-bang cutover; variant 1 can ship the single highest-ROI fix available
  (teach the existing dealbreaker filter to read the already-extracted `languages[]`
  field) in hours, and gets most of the dismissal-bias fix (snapshot §4) by prompt
  changes to `evaluator/scorer.py`. If matching quality were the only axis, variant 1's
  quality-per-hour-of-work dominates variant 2 decisively.

## 3. Matching-quality analysis, grounded in real behavior

Which observed problems does the rebuild fix, which does it leave, what new failure modes
does it risk?

**Fixed, with mechanism:**
- Python/"Lead"-title over-penalization (snapshot §4, all 7 dismissals, 100% consistent
  in direction). Root cause today: the scorer's cons flow only into the Opus distiller
  (`preference_agent/runner.py::_build_dismissed_section`), which weighs them against
  everything else and evidently chose to emit nothing about them (11 signals total,
  snapshot §5, none covering this). In the rebuild, a dismissal is not evidence for an
  LLM to mull over; it is a direct constraint update: "penalty for absent Python given
  PHP-core role → ~0", "penalty for non-Lead title → 0". Pairwise weights update
  immediately and deterministically. The same class of bias (new postings scored
  against an out-of-date weight vector) is caught going forward because weights are
  versioned and each decision updates them, rather than waiting for a full re-distill.
- Spoken-language dealbreakers (snapshot §3: French, German C1, Spanish, Russia-exclusion
  reasons). The data already exists — `extractor/runner.py` extracts
  `languages[].requirement` including "required" — but no consumer enforces it
  (`evaluator/dealbreakers.py` only checks `working_language`, the company-level field,
  mapped in `_WORKING_LANGUAGE_CODES`). The rebuild makes gate fields first-class: tier-1
  extraction is *defined* as exactly the fields the deterministic gate needs, with
  evidence-backed confidence, so a gate field that is extracted but never enforced is a
  schema test failure, not a live bug discovered in rejection reasons months later.
- Duplicate/country-mirror rejections (snapshot §3: 7+ occurrences of "same ad, different
  countries, already applied"). The current dedup lives in JobAgentWeb's
  `dedup_repo.py` with a 21-day window (snapshot §3) — the observed pattern recurs across
  months, so the window is structurally too short. Rebuild: canonical company/posting
  identity keyed on normalized description content hash (near-duplicate via SimHash,
  no time window) plus company name normalization, so a mirror of an already-applied
  posting is auto-rejected deterministically at gate time with reason "mirror of X,
  applied YYYY-MM-DD". This also fixes a subtle ranking distortion: today those mirrors
  consume Sonnet scoring calls and top-N listwise slots before a human kills them.
- Wasted spend on doomed jobs (snapshot §2: 79% of auto-rejects paid Sonnet first).
  Not a matching-quality gain per se, but it funds the above: staged gating with a cheap
  tier-1 model intercepts a share of that 79% before Sonnet. Concretely, if tier-1
  catches even half of the 393/500 cohort, Sonnet scoring volume drops by roughly 40% on
  those runs. I cannot verify the catchable share without live data — flagged as an
  estimate, not a measurement.

**Left untouched (honesty section):**
- The top-of-list precision the snapshot actually praises. Apply-rate gradient
  86.2% → 45.0% → 38.1% → 14.3% across rank buckets, `precision_at_10` 0.90, divergence
  2.4% (snapshot §6). A from-zero rebuild does not promise 95% at rank 1–5; the ceiling
  is partly candidate-side (availability, luck, posting staleness). Anyone claiming big
  top-bucket gains from a rebuild is selling something.
- LinkedIn postings have no reliable `posted_at` (`ranker/listwise.py::_posting_age_days`
  treats it as None); no rebuild fixes a source that does not publish dates. Collection
  sources themselves are out of scope of the kept-raw-text constraint and I did not
  redesign the scrapers.
- The 73% of jobs that never get a human decision (3,783 auto-rejected, snapshot §1).
  A rebuild makes the *reasons* for those auto-rejects better-grounded, but it does not
  make humans review more jobs; the fundamental information bottleneck (few decisions,
  snapshot §5) persists.

**New failure modes introduced:**
- Stated-vs-revealed preference divergence: explicitly elicited preferences are known to
  diverge from behavior (the current distiller's system prompt in
  `preference_agent/runner.py` even asks about "divergence ... they say they want product
  companies but keep applying to agencies"). If the elicited model over-trusts stated
  preferences, it can be *worse* than the behavior-derived profile on day one.
- Overfitting the pairwise learner: 30–40 comparisons fitting, say, 8–12 weights is on
  the edge; without regularization the model will encode presentation noise. Mitigation:
  ridge-regularized Bradley-Terry, and the active-selection loop picks maximal-uncertainty
  pairs rather than easy ones.
- Transition regression: during re-warm, ranking quality may sit *below* variant 0 for
  some weeks (quantified in section 6). If the candidate is actively job-hunting during
  the transition, that is a real cost, not a rounding error.
- Schema-migration bugs destroying recall: a wrong tier-1 gate at scale is worse than the
  current slow-but-fail-open gates (`evaluator/dealbreakers.py` is scrupulously
  fail-open — "unknown, never treat absence as a violation" — and the rebuild must
  preserve that discipline or it will auto-reject on extraction noise).

## 4. User-behavior grounding (explicit, not implicit)

**The 7 dismissed-item cases (snapshot §4):** treated fundamentally differently. Today a
dismissal is a row the Opus distiller reads and may or may not act on; the evidence says
it did not (0 of the 7 became signals, snapshot §5's 11-signal profile). In the rebuild,
each dismissal is an immediate, versioned weight update against the con's underlying
feature. "That's not a problem. Python is nice have requirement" (2026-10-18) zeroes the
missing-Python penalty conditioned on PHP-core roles; "Engineer is satisfactory for me"
(2026-08-11, LUMASERV) zeroes the title-level penalty. Replaying all 7 against the new
model is part of the offline acceptance test: the rebuilt scorer must not raise either
con class on the 7 source jobs.

**The recurring "duplicate posting, different country, already applied" pattern (7+
occurrences, snapshot §3):** treated at the gate, before any paid model. Canonical
posting identity (content-hash + normalized company, no 21-day window — the window is the
structural failure per snapshot §3) means a mirror of an applied posting is auto-rejected
with an explanation the candidate can trust ("mirror of the emagine Polska posting you
applied to on YYYY-MM-DD"), rather than surfacing in the ranked list to be manually
rejected an eighth time. Jobs at *different* companies with similar text are not
collapsed (hash threshold tuned so only true mirrors match; threshold validated against
the known 76 applied jobs, snapshot §1).

**The required-spoken-language pattern, currently uncaught:** treated as a first-class
gate. Tier-1 extraction emits `languages[]` with requirement levels and evidence; the gate
compares required languages against the candidate's spoken set (per snapshot §8, English
working language, Polish native — the exact set is a candidate questionnaire field,
`evaluator/dealbreakers.py::_candidate_language_codes` already computes an analogous set
for working languages). A "C1 German required" posting dies deterministically at zero LLM
scoring cost, with the reason text pulled from the evidence span, instead of surviving to
be rejected by a human after a Sonnet call. Note honestly: this specific fix does not
require the revolution — it requires reading a field the current system already extracts
— but the revolution's schema discipline is what makes such gaps structurally impossible
rather than accidentally present.

## 5. Cost analysis — two separate numbers

**(a) One-time implementation cost.**
Engineering: assumed done by the repo owner (the candidate) as the solo developer,
experienced with the codebase, working part-time. My estimate: 60–90 focused hours over
4–6 calendar weeks — roughly 15–25 h for the extraction-schema split and gate rework,
20–30 h for elicitation UI + Bradley-Terry learner, 10–15 h for canonicalization and
migrations, 10–20 h for the offline replay harness and transition validation. That
engineering time is the dominant cost of this variant by orders of magnitude, and it is
also — said plainly — a large share of the learning value (section 0's third axis).

One-off paid API cost (using snapshot §7's real per-unit rates, not list prices):
- Re-extraction backfill over ~5,259 jobs: ~$50 at run 12's real Haiku-4.5 blended rate
  ($0.86/91 ≈ $0.0095/job); ~$5–15 if tiered with `claude-haiku-5-5` ($0.10/$0.50 per
  `config.py:MODEL_COSTS`). Budget $15–50.
- Re-embedding all jobs: ~$0.17 (run 12's Voyage rate, $0.003/91 jobs).
- Elicitation session LLM calls (pair selection, few summaries): single-digit dollars.
- Total one-off API: under ~$60 in the worst case. The historical lifetime spend of the
  entire system is $60.07 (snapshot §7) — i.e., this rebuild's backfill costs at most
  about as much as the system has ever spent on inference, which is the correct frame for
  "is the backfill scary": it is not; the engineering time is.

**(b) Ongoing day-to-day operational cost.**
Baseline (snapshot §7): ~$2.25/typical run (run 12: 91 jobs; Opus $0.33, Haiku $0.86,
Sonnet $1.05, rerank $0.0014, embed $0.003), `cost_per_100` ≈ $3.14 lifetime, and 85% of a
run is extraction+scoring. Variant 2 **lowers** the typical run, via three mechanisms,
each tied to a real number:
- Tiered extraction with `claude-haiku-5-5` at one tenth of Haiku-4.5's rate: the $0.86
  extraction leg drops toward ~$0.10–15 (some jobs still get tier-2 extraction).
- Staged scoring: tier-1 cheap gate before Sonnet intercepts a share of the 79% of
  auto-rejects that currently pay full Sonnet (snapshot §2). If it catches half of them,
  the $1.05 Sonnet leg drops toward ~$0.55–0.65. Note the historical figure is on
  sonnet-4-6 ($3/$15); on today's sonnet-5 the same volume would already be ~$0.70, so
  the honest combined estimate for the scoring leg is ~$0.40–0.55.
- Dropping the debate pass removes roughly $0.15–0.30 of Opus per run (debate+listwise
  together were ~15% of run 12 ≈ $0.33, and listwise survives).
Result: typical run ≈ $0.10–15 (extract) + $0.40–0.55 (score) + $0.20–0.25 (listwise
Opus) + ~$0.01 (embed+rerank) ≈ **$0.75–1.00 per run**, i.e. a 55–65% cut versus the $2.25
baseline, or roughly $1.50–2.00 per week at a daily-run cadence. Caveat stated: run 12's
mix (91 jobs evaluated, of which an unknown fraction were doomed auto-rejects) means the
staged-gating saving is an estimate from snapshot §2's 500-job sample, not a measured
end-to-end number.

## 6. (Variant 2 only) The job-collection side from zero, extraction schema, preference bootstrapping, and the honest cold-start cost

**Collection process, better or worse.** I read only what the assigned files and the
snapshot say about collection (I did not read the collector sources in full — flagged as
a gap). From that: the from-zero rebuild keeps the scrapers' *outputs* (raw description
text is the fixed input) and changes what happens at intake. Better: (1) language
detection at intake becomes decisive, not advisory — snapshot §3's rejections
("Role is in French", "the offer description is in spanish") are postings whose *text
language* a rebuild rejects at zero cost, before extraction; (2) canonical posting
identity at intake ends the country-mirror problem (section 4); (3) the dedup window
problem (21 days, confidence 0.60, snapshot §3) disappears because identity is
content-keyed, not time-windowed. Worse/delayed: a rebuilt intake pipeline needs to
re-earn trust on source-specific quirks (excerpt completeness — the current
`collector.utils.build_excerpt`/`excerpt_looks_incomplete` machinery exists precisely
because sources truncate; a new intake layer rediscovers those bugs). Net: intake gets
cheaper and harder to fool, at the price of a few weeks of re-found edge cases.

**Extraction schema, from raw description text.** The current schema
(`extractor/runner.py`, FACT_SCHEMA_VERSION 6) is one monolithic tool with ~45 required
fields, run per job at Haiku cost, including fields (full skills taxonomy,
compensation bands, country eligibility) that only matter for jobs the gate will kill
anyway — that is part of why 79% of auto-rejects still cost a model call (snapshot §2).
Rebuild: two tiers. Tier 1 (every job, cheapest model): exactly the gate fields — work
mode, office presence, salary band, required spoken languages, seniority band, geo
eligibility, duplicate/mirror fingerprint — each with an evidence span and confidence,
mirroring the current `_field_confidence` discipline but smaller. Tier 2 (only gate
survivors): the rich profile (skills with requirement levels, responsibilities,
company/stage facts) that scoring and ranking actually consume. What is gained: cost
proportional to promise, and gate fields with provenance so a filter can never again be
blind to data the pipeline already extracted (the `languages[]` gap, section 4). What is
genuinely lost: the historical `structured_data` rows become obsolete — they were
produced under schema v6 — so the backfill in section 5(a) is mandatory, and during the
backfill window the new gates run on partially migrated data.

**Preference-profile bootstrapping — the core of this section.** The current approach is
implicit-only: wait for applied/rejected decisions, then have Opus distill them
(`preference_agent/runner.py`). The snapshot's numbers are its indictment: 209 decisions
over several months produced 11 signals, ~1 per 19 decisions (snapshot §5), and absorbed
none of the 7 dismissal corrections (snapshot §4). The rebuild replaces this with three
sources, in priority order:
1. **Explicit cold-start elicitation (day one):** a one-time session of ~30–40 pairwise
   comparisons drawn from the candidate's *own historical corpus* (76 applied, 221
   rejected — snapshot §1/§5 — whose raw text is kept), presented as "which of these two
   would you rather apply to?", plus a handful of tradeoff sliders (salary vs. stack,
   title vs. scope). Pairs are chosen actively (maximize expected information gain
   against the current weight vector), so each answer does the work of many passive
   decisions. Fitting a regularized Bradley-Terry model over ~8–12 dimensions (stack fit,
   seniority/title, company type, salary, work mode, language, domain, location) yields
   a *usable day-one profile*. Cost to the candidate: ~45–90 minutes, once. Cost in
   dollars: single-digit (section 5a).
2. **Structured ongoing capture:** every rejection gets a reason *code* (language /
   duplicate / seniority / salary / stack-absence / company-type / geo / other) alongside
   the current free text, and every dismissal (snapshot §4 mechanism) is a direct weight
   update. This raises decision yield per event by roughly an order of magnitude versus
   1-signal-per-19, because no LLM re-derives what the candidate already said.
3. **Behavioral refinement:** the BT model's weights keep updating from revealed
   decisions, with stated-vs-revealed divergence surfaced to the candidate as a question
   ("you said product companies, but 3 of your last 4 applications were agencies — which
   should I weight?") instead of being silently averaged away by a distiller.

   Why this is better than the implicit approach is not just yield: the current profile
   is un-auditable (11 opaque signals from an Opus call) while BT weights are inspectable
   ("title level contributes 0.05 to your utility; stack match contributes 0.40") and
   individually correctable — the exact property the 7 dismissals needed.

**The honest cold-start cost, quantified.** The question the schema asks me to answer
directly: "how many weeks until this is useful again?" Two answers, depending on what
"rebuilt in a better way" buys:
- If the profile were wiped and rebuilt *implicitly only* (same applied/rejected
  distillation, fresh start): parity with today's 11 signals requires re-accumulating on
  the order of the original 209 decisions. Snapshot §5 says those took "several months";
  interpolating a 4–5 month window, the user decides ~1.4–1.6 jobs/day, so 209 decisions
  ≈ **19–21 weeks** — call it 4–5 months of effectively unpersonalized ranking (worse
  than variant 0 during that whole window, since variant 0 keeps its calibrated profile).
  This is the real price of the wipe, and I do not want it blurred.
- With the explicit-elicitation rebuild above, the profile is *usable on day one* — the
  ~45–90 minute session substitutes for months of passive accumulation, because a forced
  pairwise choice is a much higher-information event than a slow trickle of apply/reject
  decisions. The residual cold-start cost is then ~3–5 weeks of the candidate's normal
  decision rate (~2/day given the structured capture) before the behavioral refinement
  layer has enough new events to be trusted over the elicited prior.
So the wipe costs either ~4–5 months (implicit rebuild, not recommended) or one focused
hour plus a few weeks of refinement (explicit rebuild, recommended). The recommendation
to spend that hour is a *quality-driven* call, not a learning-driven one: no reasonable
cost model says 19–21 weeks of degraded ranking is cheaper than 90 minutes of the
candidate's time.

**Embedding/retrieval layer from zero.** Keep: Voyage embeddings of description text and
a rerank stage — at this pool size (active pool capped at 2,000 in
`scripts/rank_jobs.py::RANKING_POOL_LIMIT`, with server-side cosine scans already cheap
enough that `embeddings/indexer.py::score_by_similarity` moved the math server-side for
bandwidth, not latency, reasons), exact search is fine and a dedicated vector database or
ANN index would be modernity for its own sake, which the schema forbids me from
recommending without a number, and no number in the snapshot supports it (embed+rerank
together are ~$0.004 of a $2.25 run, snapshot §7). Change: (1) store vectors natively
(pgvector or float columns) instead of the JSON-text embedding column — this is
**explicitly a learning/ergonomics-driven recommendation**, the quality delta at n=2,000
is negligible; (2) replace the max-sim-kNN-minus-0.3×rejected heuristic
(`embeddings/indexer.py::score_pool_by_similarity`) with the learned utility model's
score as one more RRF leg (`ranker/fusion.py` already handles extra legs generically) —
the fixed 0.3 rejected-penalty and the no-man's-land centroid problem documented in that
file are exactly what a learned linear model over job features does better; (3) keep
HyDE (`evaluator/profile.py::build_hyde_query`) — it survives the rebuild intact because
its premise (CV voice vs. posting voice) is a property of the fixed inputs, not of the
old architecture.

## 7. Self-scored summary (for the orchestrator's cross-variant comparison)

MATCHING_QUALITY_SCORE: 7/10 — the rebuild provably fixes the three real observed failure
classes (language/duplicate rejections consuming top slots, the 7/7-consistent
Python-and-title bias, dismissal signal being dropped), but the snapshot's own evidence
(86.2% top-bucket apply rate, 2.4% divergence) caps the headroom at the top of the list,
and the 3–5 week behavioral re-warm plus stated-vs-revealed risk hold it below 8.

ONGOING_COST_PER_DAY_USD: $1.00 — the tiered extraction/scoring design cuts the $2.25
baseline by roughly half to two thirds by moving doomed jobs (79% of auto-rejects,
snapshot §2) off the Sonnet leg, with the residual uncertainty being what share of that
79% a tier-1 gate can actually catch.
