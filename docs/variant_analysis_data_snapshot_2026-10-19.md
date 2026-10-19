# Real data snapshot for the variant-0/1/2 analysis — pulled live 2026-10-19

All numbers below were pulled today via the live JobAgentWeb API (the only
data store; this repo holds no local DB). Treat this file as ground truth
for the current system's real behavior — use these numbers instead of
inventing illustrative ones. Where something isn't covered here and you
need it, say so explicitly as a gap rather than guessing.

## 1. Job counts by status (all-time, this user)

| Status | Count |
|---|---:|
| new | 1 (near-zero at snapshot time; this fluctuates hourly as the pipeline runs) |
| reviewed | 1178 |
| applied | 76 (distinct companies: 61) |
| rejected (explicit, user-decided) | 221 |
| auto_rejected (dealbreaker filter + LLM score ≤1) | 3783 |

**Read this carefully:** of everything ever collected, only 76 postings
(≈1.5%) were ever applied to. The overwhelming majority (3783, ≈73% of all
non-"reviewed" terminal jobs) never led to a real decision either way —
they were auto-rejected before a human ever saw them.

## 2. What auto-rejects actually before they're thrown away (sample of 500)

| Cause | Share of sample |
|---|---:|
| LLM scorer gave ≤1.0 (dealbreaker filter did NOT catch it first — Sonnet was already paid for) | 393/500 (79%) |
| Deterministic dealbreaker: seniority level | 77/500 (15%) |
| Deterministic dealbreaker: salary floor | 18/500 (4%) |
| Deterministic dealbreaker: company type | 10/500 (2%) |
| Deterministic dealbreaker: remote/hybrid presence | 2/500 (<1%) |

**Implication to engage with, not ignore:** the deterministic (free)
dealbreaker filter only intercepts ~21% of what ends up auto-rejected.
~79% of auto-rejects still cost a full Sonnet scoring call before being
thrown away at score ≤1. If a cheaper pre-filter could catch more of this
79% before paying for Sonnet, that's real, quantifiable savings — size it.

## 3. Explicit (human) rejections — real reasons, verbatim sample of 25 out of 154 reasoned ones (out of 221 total explicit rejects)

```
- Role is in French — job description and likely day-to-day communication may require French proficiency
- Duplicate. The same job advertisment is publish for different countries. I've already applied to it   (× 7 occurrences in this sample alone)
- French language fluency is listed as mandatory
- The job explicitly states 'With a distributed team working fully remotely across the UK' — this is a UK-residency-locked remote role
- Hybrid
- I won't even consider offers connected to Russia
- the job explicitly requires a minimum of 6 years of Magento 2 experience as a backend developer
- To high possition
- Nie remote
- The job description explicitly states the office is in Aldbury, near Tring (UK)
- PHP is entirely absent — candidate's primary specialisation is unused   (× 4 occurrences in this sample alone)
- The job explicitly requires authorisation to work in the United Kingdom
- The offer description is in spanish
- C1 German language proficiency is listed as a hard mandatory requirement
- The role lists 6+ years of Azure as a must-have and 5+ years of Data Science/NumPy as a must-have
```

**Implication:** "duplicate posting, already applied, different country
mirror" and "required spoken language (French/German/Spanish/Russian
exclusion)" are *recurring, real* human-rejection categories that the
current deterministic dealbreaker filter (`evaluator/dealbreakers.py`)
does **not** check at all — it only checks the *company's working
language* (`working_language` field), not an explicit spoken-language
*job requirement* buried in the description, and it has no cross-posting
duplicate detection of its own (that lives in JobAgentWeb's
`dedup_repo.py`, confidence 0.60, 21-day window — too short for the
duplicate pattern seen here, which recurs across months).

## 4. Dismissed items — explicit "the model was wrong about me" signals (ALL 7 that exist, in full)

These are moments the candidate looked at a `score_breakdown`'s listed
"con" and explicitly told the system it was not actually a problem for
them, with their own reasoning:

| Date | Job | Con the scorer raised | Candidate's correction |
|---|---|---|---|
| 2026-10-18 | Senior PHP Fullstack Developer @ emagine Polska | "No mention of Python, LLM/AI tooling" | "That's not a problem. Python is nice have requirement" |
| 2026-09-09 | PHP Developer @ Kempuri | "Magento is the central framework — mismatch with Symfony" | "not a problem" |
| 2026-09-09 | PHP Developer @ Kempuri | "Magento-heavy PHP work is typically legacy" | "Not a problem" |
| 2026-09-09 | PHP Developer @ Kempuri | "E-commerce agency profile, less product/SaaS ownership" | "It's not a problem" |
| 2026-08-11 | Senior PHP Engineer @ Lendable | "No explicit mention of Python" | "Python might be nice to have, when PHP is the core" |
| 2026-08-11 | Senior PHP Engineer @ Lendable | "Lead/architect title not offered" | "That is not a problem" |
| 2026-08-11 | PHP Laravel Engineer @ LUMASERV | "Role title is 'Engineer' not 'Lead'" | "Engineer is satisfactory for me" |

**Implication:** every single one of the 7 dismissals is the scorer
over-penalizing the *absence* of Python or a "Lead" title on an
otherwise-good PHP role. This is a small sample (7), but 100% consistent
in direction — a real, specific calibration bias in the current scoring
prompt/preference profile, not noise. The learned preference profile (see
below) apparently has not absorbed this yet.

## 5. Learned preference profile (distilled from applied/rejected history)

- Distilled from **68 applied** / **141 rejected** decisions.
- Produced only **11 signals total**: 5 `ACCEPT`, 4 `REJECT`, 2 `INFER`, 0 `NEUTRAL` surfaced.
- That's roughly **1 distilled signal per 19 real decisions** — a very
  sparse yield. Whether that's "correctly conservative" or "leaving real
  signal on the table" (e.g. the Python/Lead-title bias in §4 above never
  became a signal) is a fair question for the variant-1/2 analyses to take
  a position on.

## 6. Ranking quality — the system's own feedback-loop numbers

- `precision_at_5` (of the last 5 ranked-and-decided jobs): **0.80**
- `precision_at_10`: **0.90**
- Apply-rate by listwise-rank bucket (this is the strongest real signal
  in this whole snapshot — it says the ranking **does** correlate with
  real decisions):

  | Rank bucket | Apply rate | n |
  |---|---:|---:|
  | 1–5   | 86.2% | 29 |
  | 6–10  | 45.0% | 20 |
  | 11–15 | 38.1% | 21 |
  | 16–20 | 14.3% | 7 |

- `would_apply` flag (score ≥7.0, rank ≤10, no dealbreaker_risk flag):
  31 jobs ever flagged, 20 later decided (16 applied, 4 rejected) →
  **precision 0.80**.
- `divergence_cases` (ranked top-5 but rejected, OR ranked 16+ but
  applied): only **5** cases out of **211** total ranked-and-decided jobs
  (**2.4%**). This is a *low* divergence rate — evidence the current
  pipeline is not badly broken, whatever its other inefficiencies.

## 7. Real historical cost (from `usage_repository`, 18 recorded pipeline runs, 2026-08-06 → 2026-08-30 — the most recent recorded window; no runs were logged between then and today under `record_run_summary`, a gap worth flagging on its own)

- **Lifetime total: $60.07** across **36.35M tokens**, `cost_per_100 ≈ $3.14`
  (JobAgentWeb's own normalized "$ per 100 jobs" metric).
- Per-run cost is **highly variable**: from **$0.0072** (0 jobs evaluated,
  just a ranking-cache hit) to **$4.24** (197 jobs evaluated, run on
  2026-08-06/07, 13+ hours wall time — likely a backfill/catch-up run).
- Typical "normal" daily run (e.g. run id 12, 2026-08-30, 91 jobs
  evaluated): **$2.25**, breakdown: Opus $0.33, Haiku (extraction)
  $0.86, Sonnet (scorer+debate, was 4.6 at the time) $1.05, rerank-2
  $0.0014, Voyage embed $0.003. **Note: scorer/debate ran on
  `claude-sonnet-4-6` ($3/$10) in every one of these 18 historical runs —
  today's config.py already uses the cheaper `claude-sonnet-5` ($2/$10),
  so these historical $-figures slightly overstate current per-run cost
  on that leg; everything else (Opus, Haiku, Voyage) is unchanged.**
- Extraction (Haiku) and scoring (Sonnet) dominate cost, not
  embedding/rerank/listwise — e.g. in run 12: Haiku $0.86 + Sonnet $1.05 =
  $1.91 of the $2.25 total (85%). Opus listwise+debate together were only
  ~15% of that run's cost. **Any cost-reduction proposal should target
  extraction/scoring volume, not the embedding/retrieval layer, to move
  the real cost needle.**

## 8. Candidate profile and market context (for the "rynek" / market framing)

- Candidate: PHP/Symfony core (9+ yrs), Python as AI/automation secondary,
  senior/lead level, fully remote from Poland, floor 216k+ PLN/year gross
  equivalent (B2B or UoP; both tolerated).
- From §1/§3: PHP-core postings dominate the applied-to set (61 distinct
  companies); pure-Python-without-PHP postings are a large fraction of
  what gets rejected/scored moderately (score_reason text in multiple
  prior triage rounds this session repeatedly says "lacks PHP entirely").
- No live market-salary-benchmark data was pulled for this snapshot (would
  require an external web search, not a DB query); if a variant's analysis
  leans on market salary comparisons, say explicitly that the number is an
  external estimate, not from this system's own data.
