# JobAgent

An AI-powered job search assistant that collects remote job listings, ranks them with a multi-stage AI pipeline, and learns your preferences from your apply/reject decisions over time.

JobAgent is a local, single-user client with **no database of its own** — every read and write goes through [JobAgentWeb](https://github.com/BaranskiTomasz/JobAgentWeb), the Postgres-backed API service that owns all data. This split lets the scraped job pool be shared across users while each user's scoring/ranking/decisions stay private.

---

## Table of Contents

1. [How it works — overview](#how-it-works--overview)
2. [Setup](#setup)
3. [User manual — step by step](#user-manual--step-by-step)
4. [Dashboard reference](#dashboard-reference)
5. [Technical deep-dive](#technical-deep-dive)
6. [Project structure](#project-structure)
7. [Running tests](#running-tests)
8. [Troubleshooting](#troubleshooting)

---

## How it works — overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│  COLLECTION                                                             │
│  LinkedIn + job boards → keyword/language filter → fetch descriptions  │
│  → JobAgentWeb (Postgres, shared job pool)                              │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │ new jobs with descriptions
┌──────────────────────────────▼──────────────────────────────────────────┐
│  AI PIPELINE (per Run Agent)                                            │
│                                                                         │
│  1. Distill preferences   ← your apply/reject history                  │
│          │                                                              │
│  2. Extract structure     ← Haiku: remote? seniority? stack? salary?   │
│          │                   (must run before scoring — the dealbreaker│
│          │                    filter and scorer both read this)        │
│  3. Dealbreaker pre-filter ← deterministic, zero-cost: salary floor    │
│          │                    and remote-only mismatch from questionnaire│
│  4. Score (Sonnet)        ← CV + preferences + few-shot + calibration  │
│          │                   → sub-scores, pros/cons, overall score    │
│          │                                                              │
│  5. Embed (Voyage)        ← 1024-dim vector per job                    │
│          │                                                              │
│  6. Semantic retrieval    ← ideal vector = centroid(applied)           │
│          │                   − 0.3 × centroid(rejected)                │
│          │                                                              │
│  7. Cross-encoder rerank  ← Voyage rerank-2: top-50 → top-20          │
│          │                                                              │
│  8. Listwise rank (Opus)  ← extended thinking, orders top-20          │
│          │                                                              │
│  9. Debate / second opinion ← different model critiques the top-20,   │
│                                 demotes anything flagged dealbreaker_risk│
└──────────────────────────────┬──────────────────────────────────────────┘
                               │ scored + ranked jobs
┌──────────────────────────────▼──────────────────────────────────────────┐
│  DASHBOARD                                                              │
│  Browse → apply / reject → feedback loop → better rankings next time   │
└─────────────────────────────────────────────────────────────────────────┘
```

Each run makes the next one smarter: your decisions feed the preference distiller, which shapes scoring and ranking. First-time visitors land on a short questionnaire (CV + work mode/salary/seniority/company/stack preferences) before the dashboard appears at all.

---

## Setup

This is a two-repo, self-hosted setup — JobAgent (this repo, the local client) plus [JobAgentWeb](https://github.com/BaranskiTomasz/JobAgentWeb) (a separate FastAPI + Postgres backend you also deploy yourself), not a single pip-install tool. Budget for standing up both before you have a working system.

### Prerequisites

- Python 3.10+
- Google Chrome (for LinkedIn scraping)
- [Anthropic API key](https://console.anthropic.com/) — Claude Sonnet, Haiku, Opus
- [Voyage AI API key](https://www.voyageai.com/) — embeddings + reranker
- A LinkedIn account
- A running [JobAgentWeb](https://github.com/BaranskiTomasz/JobAgentWeb) instance, reachable from this machine (see below)

### Installation

```bash
git clone <repo-url>
cd JobAgent
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

### Environment

```bash
cp .env.example .env
```

Edit `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
VOYAGE_API_KEY=pa-...
JOBAGENTWEB_BASE_URL=http://10.66.0.1:8000   # only if different from the default
JOBAGENT_API_KEY=...                         # optional — see .env.example
```

`ANTHROPIC_API_KEY` and `VOYAGE_API_KEY` are required at import time — `config.py` raises immediately if either is missing, so nothing (not even the dashboard) starts without both set.

`JOBAGENTWEB_BASE_URL` defaults to a WireGuard-tunneled address, **not** the public HTTPS domain — the reference deployment's Caddy config only reverse-proxies and doesn't gate access with its own auth layer (JobAgentWeb has its own per-user session login for that), but if you put a reverse-proxy auth layer of your own in front of it, that would block API traffic exactly like it blocks a browser, since there's no way for it to tell the two apart. Point this at whatever address reaches JobAgentWeb without hitting that wall.

### Start the dashboard and log in

```bash
python web/app.py
```

Open `http://localhost:5000` — with no saved session yet, this lands on `/login`. Log in with your JobAgentWeb username/password (no account yet? Register at `<JOBAGENTWEB_BASE_URL>/register` first — registration requires an invite code, so get one from the operator of that JobAgentWeb instance before you start); the session cookie is saved to `~/.jobagent/session.json` and reused by every script and the dashboard afterward. On first visit after logging in (no saved preferences yet) you land on a landing page that routes you into the questionnaire; the dashboard itself only appears once a preference profile exists.

Headless/server installs with no browser access to port 5000 can authenticate the same way from a terminal instead:

```bash
python scripts/login.py
```

---

## User manual — step by step

### Step 1 — Fill out the questionnaire

On first visit you land on a landing page and are routed to `/questionnaire`. Upload a PDF résumé — Claude parses it into a structured candidate profile injected into every scoring prompt — plus a set of optional preference sections, all editable later from **Actions → Change criteria** on the dashboard:

| Section | Feeds into |
|---------|-----------|
| Work mode & location | Remote countries / hybrid-onsite cities — drives the deterministic remote-only *and* geo-restriction dealbreaker filters, the collector's search locations, and the dashboard's Countries/Cities filters |
| Compensation | Annual salary floor + currency — drives the deterministic salary-floor dealbreaker filter (job pay is normalized to annual before comparing, whatever period it's quoted in), and the separate "no salary disclosed" dealbreaker if you opt to hide postings that don't list one |
| Seniority & role | Seniority drives a deterministic seniority-mismatch dealbreaker filter; role types also feed auto-derived search queries and soft scoring context |
| Company | Preferred company type/product-vs-outsourcing — a **hard dealbreaker filter**, not just scoring context: a job that doesn't match any selected type is auto-rejected |
| Technologies (required / avoid) | Auto-derived search titles + the rejected-keyword filter |
| Languages | Two separate checks: the collector auto-rejects postings whose *detected posting-text* language doesn't match any you listed, and the dealbreaker filter separately rejects jobs whose extracted *company working language* doesn't match your working-level (CEFR B2+) languages |
| Anything else | Free-text notes injected into the candidate profile |

Every section is optional except the CV — leave one blank and it's simply not used as a filter or signal, never treated as a violation. Saving the questionnaire also regenerates the collector's search criteria (titles derived by Claude from your tech/role/seniority, locations from work mode, rejected keywords from avoided tech) — there's no separate criteria-editing UI; it's fully driven by this form.

### Step 2 — First Run Agent

Click **Run agent** in the header. A modal lets you configure:
- **Search since last run** (default, recommended) — automatically covers every day since the last successful run
- **Search last N days** — manual override if you uncheck the above

The pipeline runs in order:
1. Collect jobs from configured sources
2. Distill preferences from your history (skipped on first run — no history yet)
3. Extract structured data (remote/hybrid, seniority, stack, salary, company type) — must run before scoring, since both the dealbreaker filter and the scorer read it
4. Apply the deterministic dealbreaker pre-filter (salary floor, remote-only, geo, seniority, company type, working language, no-salary-disclosed) — zero LLM cost for jobs it catches
5. Score surviving jobs with Claude Sonnet
6. Embed, rerank, and listwise-rank the pool, then run the debate/second-opinion pass over the top-20

**First run — LinkedIn login:** Chrome opens and pauses at the LinkedIn login screen. Log in manually. Your session is saved to `data/chrome_profile/` and reused on all future runs.

### Step 3 — Review jobs

After the run, browse the **New** tab. For each job card:

- Click the title to open the original posting
- Expand **Why this score** for the sub-score breakdown (stack/seniority/company/compensation fit) and pros/cons
- Expand **Description** to read it without leaving the dashboard
- A **Second opinion** callout appears if the debate pass flagged the job (`dealbreaker_risk`, `overrated`, or `underrated`) — dealbreaker-risk jobs are visually dimmed and sorted to the bottom of the ranked shortlist
- Use the action buttons:

| Button | What it does |
|--------|-------------|
| **Reviewed** | You've read it; staying visible but not yet decided |
| **Applied ✓** | You applied; becomes a positive example for future scoring |
| **Reject ✗** | Opens a reason box; becomes a negative example |

**Tip:** Write a rejection reason — e.g. "stawka za niska", "outsourcing body shop", "too junior". These are included verbatim in the preference distillation prompt and directly influence what Opus extracts as signals.

**Bulk actions:** Click **Select** in the toolbar to enter selection mode. Select multiple cards, then apply a status to all at once from the bulk bar. For bulk reject, a shared reason input appears.

### Step 4 — Filter and search

The toolbar and **More filters** modal offer several ways to narrow the list:

- **Search bar** — searches title, company, location, description, and AI reasoning text simultaneously
- **Score** — filter to one or more *exact* score values (not a min/max range), each shown with its job count
- **Sort** — AI rank (default), score, date, or company
- **More filters** — work type (remote/hybrid/on-site), seniority, company type, product-vs-outsourcing, tech stack, source, company, and separate **Cities** (hybrid/on-site) vs **Countries** (remote — derived from the free-text location field, validated against a real country/region list) groups
- Clicking any badge on a job card (company, location, work type, seniority, stack…) toggles that same filter directly. Active filters appear as chips above the job list.

### Step 5 — Second run and beyond

After you've reviewed a batch:

1. Click **Run agent** again, or use **Actions** for a narrower re-run: **Re-score new jobs**, **Re-evaluate auto-rejected**, or **Rank jobs (AI)** alone
2. The distiller runs first — it reads your decisions and updates the preference profile
3. New jobs get scored and ranked using your updated profile
4. The AI rank badge (`#N`) on each card shows the Opus listwise position; the **Calibration** panel shows Precision@5/@10 and divergence cases (rank ≤5 but rejected, or rank ≥16 but applied) — click it for the full report

**The loop:** every apply/reject decision improves the next ranking. After ~20–30 decisions the preference profile becomes meaningful. After ~50+ it converges.

### Step 6 — Other actions

Everything below lives in the **Actions** modal (header, next to Run agent):

| Action | When to use |
|--------|-------------|
| **Rank jobs (AI)** | Run only the Voyage + Opus ranking (+ debate) step, without collecting or scoring |
| **Re-score new jobs** | After reviewing many jobs — re-scores with updated preferences without collecting |
| **Re-evaluate auto-rejected** | After changing keywords/preferences — re-runs the filters + scoring on all auto-rejected jobs |
| **Fetch missing descriptions** | Retries jobs collected without a description; badge shows how many are pending |
| **Change criteria** | Back to `/questionnaire` |
| **Delete jobs** | Bulk-delete by status and date range — removes them from *your* view only; a posting other users have found stays in the shared pool |

---

## Dashboard reference

### Tabs

| Tab | Shows |
|-----|-------|
| **New** | Unreviewed jobs (default) |
| **Reviewed** | Jobs you've read but not decided on |
| **Applied** | Jobs you applied to |
| **Rejected** | Jobs you manually rejected |
| **Auto-rejected** | Jobs auto-rejected by the keyword/language filters or the dealbreaker filter |
| **All** | Everything |

The pipeline funnel in the summary band mirrors these same counts and doubles as a status filter — clicking a step is equivalent to clicking the matching tab.

### Summary band

| Panel | Meaning |
|-------|---------|
| Pipeline | New/Reviewed/Applied/Rejected/Auto-rejected counts, clickable |
| Avg score | Average score across **pending, new-only** jobs — excludes anything already decided so old decisions can't drag the number around |
| Calibration | Precision@5 / Precision@10 + divergence case count; click for the full report |
| Cost | Cost per 100 jobs, today's spend, all-time spend |

Below that, **"What the agent learned"** renders the distilled preference profile's signals as plain-language chips (Likes / Avoids / Inferred / No signal), with a link to the full profile and a refresh button.

### Job card anatomy

```
┌─────────────────────────────────────────────────────┬────────┐
│ Job Title (link)                                    │  #3    │  ← Opus listwise rank
│ Company · 📍 Location  [source]                     │  7.4   │  ← overall score
├─────────────────────────────────────────────────────┴────────┤
│ [remote] [senior] [startup] [product] [Python] [Django]      │  ← clickable badges
├──────────────────────────────────────────────────────────────┤
│ AI reasoning: "Strong Python/Django match, product company…" │
│ ┌ Second opinion (dealbreaker_risk) ─────────────────────┐    │  ← only if debate-flagged
│ │ "..."                                                   │    │
│ └──────────────────────────────────────────────────────────┘  │
│ ▲ Why this score  ▲ Description                              │
│   [sub-score bars]        [pros ✓]        [cons ✗]            │
├──────────────────────────────────────────────────────────────┤
│ [new]  22 Jul          [Reviewed]  [Applied ✓]  [Reject ✗]   │
└──────────────────────────────────────────────────────────────┘
```

---

## Technical deep-dive

### Data ownership

JobAgent holds no database. Every `db/repositories/*.py` module is a thin wrapper over `api_client.py`, which makes authenticated HTTP calls to JobAgentWeb. The schema itself — `job_postings`/`job_embeddings` (shared across every user) plus `user_job_states` and everything else (per-user) — lives in [JobAgentWeb's `migrations.py`](https://github.com/BaranskiTomasz/JobAgentWeb/blob/main/migrations.py); see that repo's README for the full table layout.

Two consequences worth knowing:
- **"Delete jobs"** removes rows from *your* `user_job_states` only — the underlying shared posting stays untouched for other users who've found the same URL.
- **Every script needs a valid session** (log in via the dashboard's `/login` page, or `python scripts/login.py` for headless installs) — there is no local fallback if JobAgentWeb is unreachable.

### Pipeline stages in detail

#### 1. Collection

`collector/runner.py` orchestrates all sources. Each source implements `JobSource.search(title, location, days_back, max_results, known_urls)` and returns `RawJob` objects. After collection:

- Jobs are deduplicated by URL only (JobAgentWeb's shared `job_postings` table enforces this) — existing postings are skipped; only new ones are inserted
- New LinkedIn jobs are checked against `rejected` keywords **by title alone** before a description is fetched — a job that's already doomed never costs a page load (`collector/filters.py → title_banned_reason`)
- LinkedIn descriptions are fetched with Playwright, with delays that scale to what's actually happening on the page instead of a flat random range

LinkedIn stealth parameters (`config.py → STEALTH`):
```python
# Search phase — pause after each (title, location) search
search_glance:     5–20 s   flat "glanced at results" component, always applied
search_new:        10–30 s  per newly-found job on that page (0 s added if all duplicates)

# Description phase — one browser session per batch
desc_delay:        8–45 s, scaled to word count (~200 wpm) ± 15% variance
batch_size:        10 descriptions per session
batch_pause:       120–600 s between batches
distract_every_n_batches: 2   # visits a random LinkedIn page (feed/network/jobs) between batches
```

Both delay types keep a 5% chance of an extra 60–300 s pause ("stepped away"), independent of page content.

#### How LinkedIn keyword search actually behaves

This took a lot of live trial and error to pin down — worth reading before changing `title`/`search_query` criteria.

**Every search phrase is wrapped in quotes** (`collector/sources/linkedin.py → search()`): `Senior PHP Developer` becomes `"Senior PHP Developer"` in the `keywords=` URL param. Quotes force LinkedIn to require that **exact phrase as a literal substring** somewhere in the job's searchable text — not "these words in any order" and not a fuzzy/semantic match.

- **Without quotes**, LinkedIn matches loosely on individual words (its own relevance ranking, not a literal filter). This is what searching bare `php` originally did, and it returned Ruby, Java, Salesforce, and Data Engineer postings that had nothing to do with PHP — confirmed by direct testing, which is why quoting was added.
- **Word order matters completely** with quotes, because it's a literal substring match: `"PHP Engineer"` and `"Engineer PHP"` return **completely different, non-overlapping** result sets (verified live — 6 vs 8 results, zero titles in common). Neither is "wrong"; they just catch different real-world title phrasings.
- **A short phrase is a superset of any longer phrase that contains it.** `"PHP Developer"` matches everywhere `"Senior PHP Developer"` would, plus more (`"Lead PHP Developer"`, `"Full Stack PHP Developer"`, bare `"PHP Developer"` with no prefix). This is why the criteria list was consolidated — dropping a redundant longer variant never loses coverage, as long as it's a literal substring of the phrase kept.
- **`"X Software Engineer"` is NOT a superset relationship with `"X Engineer"`** — "Software" sits in between, so these are two different, non-overlapping phrases, not a longer/shorter pair of the same one. Confirmed live: `"Python Software Engineer"` and `"Python Engineer"` returned entirely different job postings. Don't assume any two phrases that "feel similar" are substrings of each other — check literally.

**The best-performing phrase found by testing is the bare quoted language name** — `"PHP"` / `"Python"` alone, no "Developer"/"Engineer" suffix. Verified across Germany, Poland, Denmark, and the US (remote-only + last-24h filters, matching production conditions):
- Never returned zero results, unlike almost every multi-word phrase tried.
- Still clean — sampling 35+ results across two countries found no unrelated (non-PHP, non-IT) postings.
- Catches title patterns no multi-word phrase can, because the word can appear anywhere: `"Senior Backend Engineer, PHP"`, `"Software Engineer – Python"`, `"Backend-Entwickler (Python / PHP)"`.
- Also catches non-English postings, since the token itself isn't translated: German `"PHP-Entwickler"`, `"PHP Softwareentwickler"` all matched a plain `"PHP"` search — a real language-coverage win, since the DACH market posts heavily in German.

Because bare `"PHP"`/`"Python"` is a superset of `"PHP Developer"`, `"PHP Engineer"`, `"PHP Team Lead"`, etc. (all of them literally contain the word), those narrower variants were retired from the `title` criteria. **What's left needs its own entry per framework** (`Symfony Developer`, `Django Developer`, `Laravel Developer`, `FastAPI Developer`, `Flask Developer`) because a framework name doesn't contain the literal word "PHP" or "Python" — bare `"PHP"` won't find a posting titled just `"Symfony Developer"`.

**LinkedIn also supports real Boolean operators** — uppercase `AND` / `OR` / `NOT`, order-independent (`php AND developer` ≡ `developer AND php`, verified live). Tempting, because it directly fixes "0 results" — `php AND developer` returned 500+ hits vs 0–24 for any quoted phrase. **Deliberately not used**: Boolean operators match against the full job description, not just the title, so `php AND developer` also surfaces Java/.NET/generic "Backend Developer" postings where "php" is merely one word buried in a long tech-stack list. Sampled live across 4 countries: roughly half of the top results per search had no PHP/Python connection visible in the title at all. This reintroduces exactly the noise quoting was meant to eliminate, and the existing `required` keyword safety net doesn't help here — `required=[php, python]` can never reject an AND-sourced result, because the search only matched it *because* that word is already present somewhere in the document.

**The "no results" trap this all depends on getting right:** when a quoted phrase matches nothing, LinkedIn doesn't show an empty page — it shows "No matching jobs found" plus an unrelated **"Jobs you may be interested in"** widget (your own personalized recommendations, unrelated to the search). That widget reuses the exact same markup (`.scaffold-layout__list-item`) as real results, so a naive scraper — which is what this one used to be — silently scrapes someone's LinkedIn recommendations and inserts them as if they were real search hits for that query. `_collect_cards()` now checks for `.jobs-search-no-results-banner` and treats its presence as zero results. This is the reason `days_back=1` combined with narrow/compound phrases so often logged identical "found" jobs across unrelated queries and countries before the fix — always double-check this isn't happening again if search result counts look suspiciously identical across different queries.

**The actual reason for frequent "0 new jobs" isn't the phrase, usually — it's `days_back=1` + remote-only (`f_WT=2`) stacked together.** Verified live: `"Python Developer"` in Germany with no date filter has 24 results; the same phrase restricted to the last 24 hours has 0. Multiply that scarcity across every (title, location) combination run daily, and most individual searches will legitimately find nothing new — this is expected, not a bug, given how narrow the freshness window is relative to real posting volume for any single niche+country combination.

#### 2. Preference distillation

`preference_agent/runner.py` — runs with **Claude Opus 4.8**.

Inputs:
- All `applied` jobs (title, company, location, description up to 1500 chars)
- Up to 50 most recent `rejected` jobs with user-written rejection reasons
- Dismissed score factors — specific pros/cons the candidate explicitly said don't apply to them
- Up to 10 divergence cases: jobs ranked ≤ 5 by Opus but rejected by user, or ranked ≥ 16 but applied to (strongest learning signal)

Output — a list of `ProfileSignal` objects:
```
ACCEPT[company_type=product_saas; conf=HIGH; n=3/3]
REJECT[company_type=agency_outsourcing; conf=ABSOLUTE; n=5/5; note="body shop"]
INFER[compensation=min_100_eur_h; from=3 examples]
NEUTRAL[contract_form; no_signal]
```

Confidence levels: `ABSOLUTE > HIGH > MEDIUM > LOW`. `NEUTRAL` signals are stripped before being injected into the scorer.

The distiller skips if `applied_count`, `rejected_count`, and `dismissed_count` are all unchanged since the last saved profile.

Distillation is triggered as a pipeline step — not on every decision:
- At the start of **Run Agent** (before scoring new jobs)
- At the start of **Re-score new**
- On demand from **Preferences** modal

#### 3. Structured extraction

`extractor/runner.py` — runs with **Claude Haiku 4.5**, tool-use API (`submit_structured_data`). Runs **before** scoring — the dealbreaker filter and the scorer both read `structured_data`, so a freshly-collected job needs to be extracted before either can use it.

Extracts per-job JSON from the description (first 3000 chars):
```json
{
  "remote": true,
  "hybrid": false,
  "seniority": "senior",
  "salary_min": 100, "salary_max": 145, "salary_period": "hourly", "salary_currency": "PLN",
  "stack": ["Python", "Django", "PostgreSQL"],
  "company_type": "startup",
  "product_vs_outsourcing": "product",
  "working_language": "english"
}
```

Fields default to `null` when not explicitly stated — no inference. `salary_period` (hourly/monthly/yearly) exists specifically so a B2B hourly rate is never silently mistaken for an annual figure downstream. Stored via JobAgentWeb as JSON on the shared posting (`job_postings.structured_data`) — extracted once, reused by every user who has the same job. Powers the clickable badge/filter dimensions in the dashboard.

#### 4. Dealbreaker pre-filter

`evaluator/dealbreakers.py::apply_dealbreaker_filter()` — deterministic, no LLM call, runs immediately before the scoring loop over not-yet-scored jobs. Auto-rejects (score `0.0`, `status='auto_rejected'`, reason in `score_reason`) any job that violates a **structured**-field dealbreaker from the questionnaire. Every check below fails open: missing, unclear, or unconvertible data is skipped, never treated as a violation.

- **Salary floor** — job pay is normalized to an annual-gross basis (`_annualize()`: hourly ×2016, monthly ×12) before comparing against the candidate's annual `salary_min`. Currency mismatches and unknown pay periods are skipped.
- **Remote-only mismatch** — if the candidate's `work_mode` is exactly `["remote"]` and the job's structured data says `hybrid=true` or `remote=false`, it's rejected.
- **Geo restriction** — a job can be genuinely remote but still restricted to countries that don't include the candidate's selected `remote_countries` (e.g. "Remote, US only" reaching a candidate who only selected Poland).
- **Seniority mismatch** — the job's extracted seniority isn't in the candidate's selected `seniority_levels`.
- **Company type mismatch** — the job's `company_type`/`product_vs_outsourcing` doesn't match any of the candidate's selected `preferred_company_types`; a hard filter per the questionnaire's own copy, not just a scoring signal.
- **Working language mismatch** — the job's extracted `working_language` isn't among the candidate's working-level (CEFR B2+) languages.
- **No salary disclosed** — only applies if the candidate explicitly unchecked "also show postings with no salary listed"; a job that does disclose a salary is never touched by this check.

This is the only auto-reject path that runs on structured data rather than title/description keywords — it exists to catch dealbreakers a keyword filter structurally can't (e.g. a rate that's only wrong once you know the currency and pay period).

#### 5. Scoring

`evaluator/scorer.py` — runs with **Claude Sonnet 4.6**, tool-use API (`submit_score`). Only jobs that survive the dealbreaker filter reach this step.

Prompt sections, in order: candidate profile (from CV) → learned preference profile (from distillation, with confidence-weighted interpretation legend) → few-shot applied/rejected examples → calibration section (past ranking-vs-decision divergences, so the model stops repeating the same misjudgment) → MUST HAVE / PREFERRED criteria → an explicit instruction that **missing salary disclosure is neutral, never a con** — only a disclosed rate that under/overshoots the candidate's floor counts as a genuine con/pro.

Output (`submit_score` tool):
```json
{
  "sub_scores": {"stack_fit": 8, "seniority_fit": 9, "company_fit": 6, "compensation_fit": 5},
  "pros": ["Exact stack match", "Fully remote, product company"],
  "cons": ["Company type slightly off from product-SaaS preference"],
  "overall_score": 7.5,
  "score_reason": "Strong stack and seniority fit at a solid product company."
}
```
`overall_score` is the model's own holistic judgment — never a formula over `sub_scores`, since non-linear reasoning (dealbreaker penalties, MUST-HAVE logic) needs to stay possible. `sub_scores`/`pros`/`cons` are for dashboard transparency, stored as JSON (`score_breakdown`).

#### 6. Embeddings

`embeddings/indexer.py` + `embeddings/client.py` — **Voyage voyage-3-large**, 1024-dim.

Each job is embedded as: `"{title} at {company}\n{description[:2000]}"` with `input_type="document"`. Vectors are stored via JobAgentWeb's shared `job_embeddings` table — computed once per posting, reused by every user.

The **ideal candidate vector** is computed from your feedback:
```
ideal = centroid(applied_embeddings) − 0.3 × centroid(rejected_embeddings)
```

The 0.3 weight on rejected embeddings pushes the ideal vector away from job types you've rejected without over-correcting. Falls back to embedding the CV summary when there's no applied-job history yet, so a new candidate's first run still gets semantic ranking instead of arbitrary scrape-recency order. Each new job is scored by cosine similarity to this vector → stored as `embedding_score`.

#### 7. Cross-encoder rerank

`ranker/reranker.py` — **Voyage rerank-2**.

Top-50 jobs by `embedding_score` are passed to the cross-encoder with a query derived from the CV summary and preference signals. The cross-encoder evaluates each (query, document) pair jointly (not independently like an embedding model), giving more accurate relevance scores. Top-20 by `rerank_score` proceed to listwise ranking.

#### 8. Listwise ranking

`ranker/listwise.py` — **Claude Opus 4.8** with extended thinking (`adaptive` mode, `effort=high`).

The top-20 jobs from the reranker are passed together in a single prompt. Opus sees all 20 simultaneously and ranks them relative to each other — not by absolute score. This is the key advantage over pairwise scoring: Opus can reason about trade-offs across the full set.

Output format — JSON in `<ranking>` tags:
```json
[
  {"job_id": "abc123", "reason": "Exact stack match, product company, senior role"},
  {"job_id": "def456", "reason": "Good match but agency, lower confidence"},
  ...
]
```

Each job gets a `listwise_rank` (1 = best) and `rank_reason`. Jobs outside the top-20 are not ranked (NULL).

Extended thinking lets Opus internally reason about candidate-job fit before committing to an ordering. This produces more consistent rankings than a simple prompt.

#### 9. Debate / second opinion

`ranker/debate.py::debate_rank()` — runs with **Claude Sonnet 4.6** (deliberately a different model from the Opus listwise ranker) over just the top-20 shortlist, right after listwise ranking.

The critic sees the current order plus each job's `rank_reason` and `score_breakdown` (pros/cons), and does **not** re-rank from scratch — it only flags disagreements it feels strongly about, via `submit_debate_review`:

- `dealbreaker_risk` — the primary ranking likely missed a real dealbreaker (e.g. stack similarity masking a seniority or company-type mismatch) → **demoted to the bottom** of the shortlist, `listwise_rank` renumbered accordingly
- `overrated` / `underrated` — surfaced as a note on the card, doesn't reorder anything

Most jobs get no flag at all — the prompt explicitly discourages flagging just to have something to say. Flag + note are stored (`debate_flag` / `debate_note`) and shown as a "Second opinion" callout on the dashboard.

#### 10. Would-apply flag

`ranker/would_apply.py` — phase 1 of an eventual auto-apply feature. Flags jobs the agent would apply to, purely for the candidate to validate — **never sends anything**. Three conditions must all hold: an absolute score floor (`config.WOULD_APPLY["score_floor"]`, currently 7.0, not a relative top-N cut, so a weak ranking run yields zero flagged jobs instead of always flagging "the best of a bad batch"), a rank ceiling on the final post-debate `listwise_rank` (`config.WOULD_APPLY["rank_ceiling"]`, currently 10, so a job whose score alone clears the floor but sits deep in the pool still isn't flagged), and no `dealbreaker_risk` debate flag.

#### Evaluation metrics

`GET /api/eval/report` returns:

- **Precision@5** and **Precision@10** — of the top-K ranked, already-decided jobs (`applied`/`rejected`/`auto_rejected` — `reviewed` doesn't count, it's read-but-undecided), what fraction did the user apply to? Higher = better ranking.
- **Divergence cases** — jobs where ranking and user decision strongly disagree:
  - `rank ≤ 5` + `status = rejected` → false positive (Opus liked it, you didn't)
  - `rank ≥ 16` + `status = applied` → false negative (Opus missed a good one)
- **Would-apply precision** — of jobs flagged would_apply, what fraction were actually applied to (vs rejected)?

Divergence cases are fed back into the next distillation run as high-priority signals.

### Model usage and approximate costs

| Stage | Model | Cost approx. |
|-------|-------|-------------|
| Distillation | Opus 4.8 | ~$0.40/run (50 jobs × 1500 chars) |
| Extraction | Haiku 4.5 | ~$0.001/job |
| Dealbreaker filter | — (deterministic) | free |
| Scoring | Sonnet 4.6 | ~$0.007/job |
| Embedding | Voyage voyage-3-large | $0.18/1M tokens |
| Reranking | Voyage rerank-2 | $0.05/1M tokens |
| Listwise rank | Opus 4.8 | ~$0.40/run (top-20 jobs) |
| Debate / second opinion | Sonnet 4.6 | ~$0.02/run (top-20 jobs, single call) |

**Distillation runs once per Run Agent / Re-score, not on every decision.** This is the most expensive step; the budget is fixed (~50 jobs in context) regardless of total job count. The dealbreaker filter catches some jobs before scoring ever runs, reducing Sonnet spend for a candidate with a firm salary floor or remote-only requirement.

**Listwise ranking + debate are skipped entirely when the top-20 candidate set is identical to what was already ranked last run** (`ranker/rank_cache.py`) — a `Rank jobs (AI)` re-run with nothing new since the last one costs nothing for those two stages, since re-running them against the same 20 jobs would just reproduce the same reasoning.

### Cost tracking

Every API call logs usage through JobAgentWeb. The dashboard's Cost panel shows cost per 100 jobs, today's spend, and all-time spend. The `MODEL_COSTS` dict in `config.py` holds the rates — update it if pricing changes.

---

## Project structure

```
JobAgent/
├── config.py                       # API keys, models, stealth timings, ranking params
├── api_client.py                   # HTTP client for JobAgentWeb — session cookie, retries, error translation
├── collector/
│   ├── base.py                     # JobSource ABC + RawJob dataclass
│   ├── filters.py                  # Title pre-filter (before fetch) + full rejected/required filter (after)
│   ├── language_filter.py          # Detects posting language, auto-rejects against candidate's languages
│   ├── location.py                 # Shared location-matching for API-based (non-LinkedIn) sources
│   ├── query_pruning.py            # Auto-excludes reject-heavy or zero-yield search queries
│   ├── utils.py                    # HTML→text excerpt builder shared by scorer/debate prompts
│   ├── runner.py                   # Orchestrates sources → descriptions → JobAgentWeb
│   └── sources/
│       ├── linkedin.py             # Playwright + system Chrome, stealth delays
│       ├── weworkremotely.py       # RSS feed
│       ├── himalayas.py            # RSS feed
│       ├── remotive.py             # JSON API
│       ├── remoteok.py             # JSON API
│       ├── workingnomads.py        # JSON API
│       ├── justjoin.py             # justjoin.it — embedded JSON + Playwright for descriptions
│       ├── theprotocol.py          # theprotocol.it — Playwright (Cloudflare-gated)
│       ├── itpracuj.py             # it.pracuj.pl — Playwright (Cloudflare-gated)
│       ├── nofluffjobs.py          # NoFluffJobs — plain HTTP, Angular TransferState JSON
│       └── solidjobs.py            # SOLID.Jobs — plain HTTP, vendor Accept headers
├── evaluator/
│   ├── profile.py                  # Load CV profile via api_client
│   ├── scorer.py                   # Sonnet prompt builder + tool-use scoring (sub-scores/pros/cons)
│   ├── dealbreakers.py             # Deterministic pre-LLM salary-floor / remote-only filter
│   └── runner.py                   # Extract → dealbreaker filter → score unscored jobs
├── extractor/
│   └── runner.py                   # Haiku structured extraction per job
├── embeddings/
│   ├── client.py                   # Voyage AI wrapper (embed + rerank + cosine)
│   └── indexer.py                  # Build ideal vector; score by similarity
├── ranker/
│   ├── reranker.py                 # Voyage cross-encoder rerank (top-50 → top-20)
│   ├── listwise.py                 # Opus listwise ranking (top-20 → ordered list)
│   ├── debate.py                   # Sonnet second opinion over the top-20; demotes dealbreaker_risk
│   ├── would_apply.py              # Absolute-floor auto-apply flag (validation only, never sends)
│   └── rank_cache.py               # Skips listwise+debate when the top-20 set is unchanged since last run
├── preference_agent/
│   ├── profile.py                  # ProfileSignal schema + render_signals()
│   └── runner.py                   # Distill apply/reject/dismissal history → JSON profile
├── evaluation/
│   └── harness.py                  # Precision@K, divergence cases, would-apply precision
├── query_expansion/
│   └── runner.py                   # Suggest new search queries from applied jobs
├── db/
│   ├── types.py                    # TypedDicts: JobRow, ScoreResult, etc.
│   └── repositories/                # Thin api_client.py wrappers — no local persistence anywhere here
│       ├── job_repository.py
│       ├── criteria_repository.py
│       ├── candidate_preferences_repository.py  # /questionnaire preferences
│       ├── cv_repository.py
│       ├── preference_repository.py
│       ├── dismissed_item_repository.py
│       ├── excluded_search_queries_repository.py
│       ├── search_stats_repository.py
│       ├── session_repository.py
│       └── usage_repository.py     # API cost tracking
├── scripts/
│   ├── login.py                    # Authenticate against JobAgentWeb once; saves session cookie
│   ├── run_all.py                  # CLI: full pipeline
│   ├── rescore_new.py              # Re-score new jobs only
│   ├── distill_preferences.py      # Run distillation once
│   ├── rank_jobs.py                # Run embed + rerank + listwise + debate only
│   ├── extract_jobs.py             # Backfill structured extraction for all jobs
│   ├── index_embeddings.py         # Backfill embeddings for all jobs
│   ├── reindex_embeddings.py       # Recompute embeddings for jobs that already have one
│   ├── backfill_descriptions.py    # Retry jobs with missing descriptions
│   ├── cleanup_low_score_new.py    # One-off: auto-reject already-scored 'new' jobs below threshold
│   ├── prune_search_queries.py     # Evaluate + auto-exclude reject-heavy/zero-yield search queries
│   └── reevaluate_rejected.py      # Re-run filter + scoring on auto-rejected jobs
├── web/
│   ├── app.py                      # Flask app factory; landing → questionnaire → dashboard routing
│   ├── routes/
│   │   ├── jobs.py                 # /api/jobs, /api/stats, status updates
│   │   ├── jobs_admin.py           # Bulk delete, score-item dismissal, internal counts
│   │   ├── runner.py               # WebSocket streams for all pipeline actions
│   │   ├── criteria.py             # /api/criteria — CRUD used internally by candidate_preferences.py
│   │   ├── candidate_preferences.py# /api/candidate-preferences — the questionnaire, syncs criteria
│   │   ├── preferences.py          # /api/preferences — learned profile + distill trigger
│   │   ├── cv.py
│   │   ├── sources.py              # /api/sources — this machine's list of collector sources
│   │   ├── ranking.py
│   │   ├── query_expansion.py
│   │   ├── search_queries.py       # Excluded/pruned search query management
│   │   └── evaluation.py
│   ├── templates/
│   │   ├── landing.html            # First-visit page when no preferences saved yet
│   │   ├── questionnaire.html      # CV upload + preference sections
│   │   ├── dashboard.html
│   │   ├── how_it_works.html       # /how-it-works explainer page
│   │   └── _footer.html            # Shared footer include
│   └── static/
│       ├── dashboard.js / dashboard.css
│       ├── questionnaire.js / onboarding.css
│       ├── landing.js
│       └── explain.css             # how_it_works.html styling
├── tests/
│   ├── unit/                       # Logic, prompt builders, parsers
│   ├── integration/                # Repository/route tests against a real JobAgentWeb instance
│   └── e2e/                        # Real Anthropic API calls
└── data/                           # gitignored
    ├── chrome_profile/
    └── logs/
```

---

## Running tests

```bash
pytest                  # unit + integration
pytest tests/unit/
pytest tests/integration/
pytest -m e2e           # real Anthropic API calls — requires funded ANTHROPIC_API_KEY
```

JobAgent has no local database, so `tests/conftest.py` spins up a **real** JobAgentWeb instance as a subprocess (from a sibling `../JobAgentWeb` checkout, its own venv) pointed at a dedicated `jobagentweb_test` Postgres database — the same one JobAgentWeb's own test suite uses. This means:

- **The JobAgentWeb Postgres tunnel must be reachable** from wherever you run the suite (see JobAgentWeb's README for the connection details).
- Every test gets a freshly-registered JobAgentWeb user for isolation — no mocks against a fake backend.
- `job_postings`/`job_embeddings` are shared/global and never truncated between tests, so tests that insert jobs use unique URLs to avoid colliding with another test's data.

~800 unit/integration tests, a few minutes total (dominated by starting the JobAgentWeb subprocess once per session). The e2e suite (~9 tests) makes real Claude calls and needs actual Anthropic credit balance — expect these to fail with a billing error, not a code bug, if the account isn't funded.

---

## Troubleshooting

**Agent finds no jobs** — check that criteria (search queries/titles + locations) exist. These are normally auto-generated by saving the questionnaire (`/questionnaire`); if they look empty, resave it.

**LinkedIn login required** — on first run, Chrome opens at the login page. Log in manually; your session is saved to `data/chrome_profile/`. If it expires, delete that directory and log in again.

**Scraping breaks / wrong jobs** — LinkedIn occasionally changes its HTML. Update CSS selectors in `collector/sources/linkedin.py`.

**`NotLoggedInError` / 401s from every script** — your session expired or was never created. Open the dashboard (it redirects to `/login` automatically) or run `python scripts/login.py` again.

**`overloaded` from Anthropic** — the evaluator retries automatically (3×, 30 s / 60 s). If it keeps failing, wait and retry.

**Dashboard shows "Running" with nothing running** — a session was left in `status='running'` after a crash. It auto-clears after 24 hours (sized for a real LinkedIn stealth-paced collector run, which can itself take 4+ hours), or click **Actions → Stop** to cancel it immediately via JobAgentWeb's API.

**Jobs missing descriptions** — open **Actions → Fetch missing descriptions**.

**Ranking badges not showing** — structured extraction (`extract_jobs.py`) hasn't run yet. After adding Anthropic credits, run:
```bash
python scripts/extract_jobs.py
```

**Voyage rate limit errors** — the free tier allows 3 RPM. Add a payment method on [dashboard.voyageai.com](https://dashboard.voyageai.com) to unlock 600 RPM. Then update `BATCH_SIZE=128` and `BATCH_DELAY=1` in `embeddings/indexer.py`.

**Score not changing after re-score** — the distiller skips if `applied_count`/`rejected_count`/`dismissed_count` haven't changed since the last run. Mark at least one job as applied or rejected first.
