# Shared output schema — variant 0 / 1 / 2 analyses (read this before writing anything)

You are writing ONE of three separate, independently-commissioned reports
comparing options for JobAgent's ranking/matching pipeline. The other two
reports are being written by other analysts in parallel; you will not see
them, and they will not see yours. To make the three reports usable
side-by-side afterward, every report MUST follow this exact structure and
MUST ground every claim in real evidence: this repo's actual source code,
and the real numbers in `docs/variant_analysis_data_snapshot_2026-10-19.md`
(pulled live from the production database today — treat it as ground
truth, don't invent illustrative numbers when real ones are sitting in
that file).

## The three variants (for context — you are writing about ONE of them)

- **Variant 0 — status quo.** Change nothing. Document reality as it is.
- **Variant 1 — evolution.** Keep the current data model/storage
  (Postgres, JSON-text embedding column, same tables) exactly as-is; only
  change the *process* — prompts, fusion logic, filtering order, what gets
  measured, which models are called when.
- **Variant 2 — revolution.** Full reset, explicitly allowed to assume a
  clean slate: the learned preference profile is wiped and can be rebuilt
  from scratch in a better way from day one; the job-postings side can
  also be rebuilt from scratch, EXCEPT you keep the raw collected
  description text already sitting in the DB (do not assume you'd
  re-scrape history) — everything downstream of that raw text (extraction
  schema, embeddings, storage, ranking logic, preference modeling) is open
  to a from-zero redesign if you think it's worth it. This variant is
  explicitly allowed to prioritize "the human learns real modern
  technique and gets a strong, honest CV/interview story" as a legitimate
  design driver, not just quality/cost.

## Required structure, in this order, for your one report

### 0. Executive verdict (5–8 sentences)
State your bottom line up front: for THIS variant, what's the real
quality impact, real cost impact, and (if variant 2) real learning-value,
and whether you'd actually recommend it.

### 1. Scope of change — is this evolution or revolution, concretely?
Don't just assert a label — show it:
- Which files/modules are touched, added, or deleted (name them).
- Rough lines-of-code delta (added/changed/removed), even if approximate.
- Does the database schema change? New tables/columns? A migration with a
  backfill? State explicitly: data model same / data model changed.
- Does it require re-running anything expensive over historical data
  (re-embedding, re-extracting, re-scoring)? Estimate how much (jobs ×
  cost, using real per-unit costs from the data snapshot file's §7, not
  invented ones).
- One sentence verdict: "this is [no change / an evolution / a
  revolution] because ___."

### 2. Strengths and weaknesses, explicitly relative to the OTHER two variants
For each of the other two variants (named above), say one concrete thing
THIS variant is better at and one concrete thing it is worse at. Don't
just describe this variant in isolation — the comparison is the point.

### 3. Matching-quality analysis, grounded in real behavior
Use the data snapshot file's real numbers: the apply-rate-by-rank-bucket
table, the precision@5/@10 and would_apply precision, the divergence
cases, the 7 dismissed-item corrections (§4 of the snapshot — a real,
100%-consistent pattern of the scorer over-penalizing Python/"Lead"-title
absence), the sparse learned-preference-signal yield (11 signals from 209
decisions), and the real rejection-reason patterns (duplicates across
country-mirrors, spoken-language requirements the dealbreaker filter
doesn't check). For THIS variant specifically: which of these real,
already-observed problems does it fix, which does it leave untouched, and
does it risk introducing any NEW failure mode? Do not write generically
about "RAG retrieval quality" without tying it back to one of these
concrete, already-observed patterns.

### 4. User-behavior grounding (explicit, not implicit)
Answer directly: under this variant, how would the system treat the 7
dismissed-item cases differently (if at all)? How would it treat the
recurring "duplicate posting, different country, already applied"
rejection pattern (7+ occurrences)? How would it treat the "required
spoken language" rejection pattern, currently uncaught by the
deterministic filter? If this variant doesn't change behavior on these
real cases, say so plainly rather than skipping the question.

### 5. Cost analysis — two separate numbers, not one blended estimate
**(a) One-time implementation cost:** engineering effort (hours/days,
state your assumption about who's doing the work), plus any one-off paid
API cost (re-embedding/re-extracting/re-scoring backfills), using the real
per-unit costs in the snapshot file (§7), not Voyage/Anthropic list prices
alone — actual historical blended cost-per-job from this system's own
runs is in that file.
**(b) Ongoing day-to-day operational cost:** $ per typical daily run,
compared explicitly against the real historical baseline in the snapshot
(§7: ~$2.25/typical run, ~$3.14 cost-per-100-jobs, dominated by
extraction+scoring, not embedding/retrieval). State whether this variant
raises or lowers that number, and by roughly how much.

### 6. (Variant 2 ONLY — skip this section if you're writing variant 0 or 1)
A specific sub-section: in what way would the job-*collection* process
itself (not just ranking) be better or worse under a from-zero rebuild?
What specifically would you do differently starting from raw description
text, for: the extraction schema, the preference-profile bootstrapping
(given you're allowed to assume it's wiped), and the embedding/retrieval
layer? Be concrete about what's gained and what's genuinely lost or
delayed (e.g. a wiped preference profile means zero personalization until
enough new decisions accumulate — how long, roughly, given this user
historically produced ~209 decisions over several months per the
snapshot?).

### 7. Self-scored summary (for the orchestrator's cross-variant comparison)
End with exactly these two lines, nothing else after them:
- `MATCHING_QUALITY_SCORE: X/10` — your own estimate of this variant's
  job-matching quality once implemented, with one sentence of
  justification tied to section 3's evidence.
- `ONGOING_COST_PER_DAY_USD: $X` — your own estimate of steady-state daily
  operational cost, with one sentence tying it to section 5(b).

## Ground rules

- Cite specific files/line numbers or specific numbers from the snapshot
  file wherever you make a factual claim about the current system.
- Do not recommend a dedicated vector database, ANN index, or any
  infrastructure change purely because it "sounds more modern" — only if
  you can tie it to a concrete number in the snapshot (pool size, cost,
  or a specific observed failure pattern).
- Be honest about uncertainty. If you can't verify something (e.g. you
  lack network/live-DB access and can only use the snapshot file), say so
  explicitly rather than inventing a plausible-sounding number.
- Do not modify any pipeline code. This is a written analysis only.
