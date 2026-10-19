# RAG modernization proposal — should JobAgent move to a "more modern RAG" stack?

Date: 2026-10-19. Analysis-only deliverable; no pipeline code was changed.

## What this analysis is based on

Read in full from this repo: `embeddings/indexer.py`, `embeddings/client.py`, `ranker/fusion.py`,
`ranker/reranker.py`, `ranker/listwise.py`, `ranker/debate.py`, `ranker/rank_cache.py`,
`ranker/exploration.py`, `evaluator/scorer.py`, `evaluator/dealbreakers.py`, `evaluator/profile.py`,
`evaluator/runner.py`, `extractor/runner.py`, `scripts/rank_jobs.py`, `config.py`,
`db/repositories/usage_repository.py`, `db/repositories/job_repository.py`, `evaluation/harness.py`,
`collector/utils.py` (excerpt caps), `README.md`, and `docs/pipeline_review_2026-10-16.md`.

**Access caveat:** every attempt to read the sibling repo (`../JobAgentWeb/embeddings_repo.py`,
`migrations.py`, including via shell and sandbox tools) was denied by this session's approval
callback. Storage-layer statements below therefore rely on the task description plus what the
JobAgent-side code documents about the contract: vectors live in a shared `job_embeddings`
table owned by JobAgentWeb, one JSON-text 1024-dim vector per posting (~22 KB of JSON each,
per the comment in `embeddings/indexer.py`), no vector index, and one
`/api/embeddings/similarity` HTTP call per decision vector, each returning only
`{job_id: float}` for a requested `job_ids` list. Verify the JobAgentWeb-side details before
acting on the pgvector recommendation.

Verified scale facts that drive everything below:

- Ranking pool cap: `RANKING_POOL_LIMIT = 2000` active `new` jobs per run
  (`scripts/rank_jobs.py`), one candidate, one user of the ranking output.
- Ranking funnel: RRF (embedding leg + Sonnet score leg) → top-50 Voyage rerank-2 →
  RRF again with the rerank leg → top-20 listwise Opus + 3 exploration slots → one debate call.
- Models: scorer **and** debate both use `CLAUDE_MODEL = claude-sonnet-5`; listwise uses
  `claude-opus-4-8`; extraction uses Haiku 4.5/5.5; embeddings `voyage-3-large` (1024-dim).
  (The README's "Sonnet 4.6" for scorer/debate is stale; `config.py` is the truth.)
- Embedded text per job is `title + company + location + excerpt[:2000]` — only the first
  2000 chars of a description that is capped at 6000 chars (8000 for LinkedIn) elsewhere.
- Per-run costs, from `MODEL_COSTS` and the README's own estimates: distillation (Opus)
  ~$0.40, listwise (Opus) ~$0.40, scorer (Sonnet 5) ~$0.007/new job, debate ~$0.02–0.06,
  rerank-2 ~$0.0015/run (50 docs), embeddings ~$0.0001/job. A typical daily run is ~$1–2.

---

## (a) Recommended changes, ranked

Judged strictly against the stated priority order: 1) ranking quality for this one candidate,
2) cost, 3) learning-value (explicitly second-class, labeled where it applies).

| # | Change | Justified by | Verdict (one line) |
|---|--------|--------------|--------------------|
| 1 | Offline eval harness with a frozen, labeled replay set | **quality** + **learning-value** | The single highest-leverage change: today you cannot measure *any* other change, so every remaining pick is currently a guess. |
| 2 | Deterministic structured-facet RRF leg (hybrid retrieval using facts you already extracted) | **quality** (+ cost: free) | Real recall gap (exact-match stack/seniority signals dense embeddings are known to blur), zero marginal cost, uses data already in `structured_data`. |
| 3 | Fix the embedding truncation (raise the 2000-char cap or add a tail-vector max-sim) | **quality** | A long posting's actual requirements can fall entirely outside the embedded text; small one-off cost to fix. |
| 4 | Genuinely cross-family debate model | **quality** (weak evidence, cheap) | The "second opinion" is currently the same model that produced each job's score; decorrelation is plausible but unproven — do it as a measured experiment, not a rewrite. |
| 5 | pgvector + HNSW migration of `job_embeddings` | **learning-value** (with a modest engineering side-benefit) | Ranking quality will not improve at this scale; do it for the CV/interview value and the JSON-bloat/HTTP-round-trip cleanup, not for the ranking. |
| 6 | BM25 hybrid leg via Postgres full-text search | **learning-value** (potentially graduating to quality after #1) | The textbook "hybrid search" version of #2; worth doing for learning, and the eval harness will tell you whether it beats the cheap structured-facet leg. |

### 1. Offline eval harness with a labeled replay set — quality + learning-value

Today's only metric is online: Precision@5/@10 and divergence cases computed from the
candidate's actual apply/reject decisions (`/api/eval/report`, mirrored by
`evaluation/harness.py`). That is the right *production* metric, but it cannot answer
"did change X improve the ranking?" — decisions trickle in at human speed, the pipeline
mutates underneath them, and a single candidate's few hundred decisions give almost no
statistical power to compare variants run-over-run.

What to build: a frozen snapshot of one ranking pool (jobs + `structured_data` + scores +
embeddings + decisions as of a date), exported through the existing JobAgentWeb API, plus a
replay harness that re-runs the ranking stack (fusion, rerank, listwise, debate — each stage
individually toggleable) against the frozen set and scores the outcome against the known
labels: past `applied` jobs are the relevant set, past `rejected` (with reasons) the
irrelevant set. Report nDCG@10, P@5/P@10, and recall@50 of known-applied jobs — recall@50
matters most for retrieval-leg changes, since the top-50 rerank pool is where a buried good
job is lost. The divergence cases the dashboard already computes are exactly the kind of
labeled data this needs; no new labeling effort is required to start.

Be honest about the ceiling: a few hundred labels from one candidate will detect gross
differences (does the stack-overlap leg change top-10 composition? does a tail-vector change
recall@50?), not 2% deltas. That is still strictly better than the current situation, where
every proposal in this document is untestable.

- **Library/service:** `ranx` (Python IR-evaluation library, nDCG/P@k/recall out of the box)
  on top of a plain pytest-style replay script; snapshot stored as JSON in `data/` (gitignored).
  No new infrastructure.
- **Effort:** 2–4 days, all JobAgent-side.
- **Cost delta:** retrieval/fusion-only replays are ~$0 (embeddings and scores are cached in
  the snapshot). A full replay including listwise+debate on a frozen top-23 costs ~$0.45
  (one Opus + one debate call); a 5-variant experiment grid is a few dollars, one-off.

### 2. Deterministic structured-facet RRF leg — quality, cost-free

`fuse_by_rrf` already accepts a third leg (`extra_rank_field`), used today only for the
rerank score. Add a deterministic rank over facts the extractor already stores per job
(`stack`, `stack_required`, `seniority`, remote flags, salary band) and the candidate's
already-known wants (CV stack, `extra_tech`, required/avoided tech): stack-overlap count,
required-stack coverage, seniority match. Fuse it as the third leg in the first RRF pass
(the one that selects the top-50 rerank pool).

Why this is the right "hybrid retrieval" for this system: dense embeddings are the classic
weak point for exact-match signals — a posting that requires "Kotlin" among a generic
senior-backend description will not reliably beat a Java posting that reads similarly, but a
deterministic stack-overlap score cannot miss it. You already have the sparse signals;
they are just not in the ranking. The keyword *filters* (title bans, language) run at
collection, and the dealbreaker filter is a hard gate — nothing between those and the
dense+LLM legs rewards a genuine exact skill match. This is hybrid (sparse+dense) retrieval
in substance, without standing up BM25 infrastructure to get most of its value.

- **Library/service:** none — pure Python over `structured_data`, reusing
  `ranker/fusion.py::fuse_by_rrf(extra_rank_field=...)`.
- **Effort:** 0.5–1 day.
- **Cost delta:** $0 per run (no API calls added).
- **Gate it on #1:** measure recall@50 of known-applied jobs with and without the leg.

### 3. Fix the embedding truncation — quality

`embeddings/indexer.py::_job_to_text` embeds `excerpt[:2000]` while every LLM stage
(scorer, reranker documents, listwise) reads the full 6000–8000-char excerpt. A posting with
a long company blurb, benefits list, or DEI section before its requirements section can have
its actual stack entirely outside the embedded text — and that vector is one of the two legs
deciding what reaches the top-50 rerank pool. voyage-3-large's context window is 32k tokens;
the current 2000-char cap is a vestige, not a model constraint.

Two scoped options, in order of preference:

1. Raise the cap to the same excerpt length the other stages see (6000/8000 chars) and
   re-embed. Simple, one line plus a backfill.
2. If you want the "multi-vector" technique explicitly: keep the current vector and add one
   tail-chunk vector (chars 2000+), score with the max-sim pattern that
   `score_pool_by_similarity` already implements for decision vectors.

Do **not** go further into chunked RAG (fixed-size chunk windows, a chunk pool with per-chunk
retrieval). Job postings are short, single-concept documents, not 50-page manuals; see (b).

- **Library/service:** none new (voyageai, already a dependency).
- **Effort:** 0.5 day + a one-off re-embed of the pool (the `text_hash` invalidation in
  `rank_jobs.py` already handles re-indexing automatically).
- **Cost delta:** one-off re-embed of ~2000 jobs at ~1800 tokens each ≈ 3.6M tokens ≈ **$0.65**;
  ongoing per-new-job embedding cost roughly triples to ~$0.0003/job — still negligible.
  Option 2 doubles vector storage and similarity compute for the same ~$0.65 one-off.

### 4. Genuinely cross-family debate model — quality (weak evidence, cheap experiment)

Verified: debate uses `CLAUDE_MODEL` (claude-sonnet-5), which is *also* the scorer, and the
debate prompt shows the reviewer each job's `score_breakdown` — so the "second opinion"
partially reviews its own prior work. It is, however, already a different model from the
primary *listwise* ranker (Opus 4.8), so the task summary's "same model twice" is true at
the scorer↔debate level, not at the listwise↔debate level. The 2026-10-16 pipeline review
found debate "working and effective" (it does change ranks and they persist).

The decorrelation argument for a different family (different pretraining, different failure
modes) is plausible but unproven here, and debate is a ±3-position nudge with a
demote-to-bottom flag — its blast radius is small either way. So: swap in one cross-family
model, keep the exact same tool schema and nudge mechanics, and let harness #1 say whether
it flags better (more true dealbreaker_risk, fewer false flags) than the Sonnet reviewer.
If it doesn't measurably help, keep whatever is cheaper/simpler.

- **Library/service:** `google/gemini-3-flash` or `openai/gpt-5.1-mini` via **OpenRouter**
  (one extra key, one SDK swap in `ranker/debate.py`; direct Google/OpenAI SDKs equally fine).
  Use a forced-tool-call mode equivalent to the current `submit_debate_review` schema.
- **Effort:** 0.5 day.
- **Cost delta:** parity — debate is ~$0.02–0.06/run today; both candidates price at roughly
  $0.01–0.05/run for a ~30k-token prompt. Add the model to `MODEL_COSTS` so usage logging
  stays truthful.

### 5. pgvector + HNSW migration — learning-value (with a real side-benefit)

Straight answer first: **at 2000 vectors per pool, a vector index will not improve ranking
quality.** Exact cosine over 2000×1024-dim floats is ~2M multiply-adds — sub-millisecond in
NumPy or a Postgres exact scan. HNSW is *approximate*; at this scale it can only return
slightly worse neighbors faster. Nothing in criterion 1 justifies it. Even the shared
catalog staying a few tens of thousands of embeddings is exact-scan territory; ANN earns its
complexity at millions.

So this is a **learning-value pick, explicitly**: the classic interview-grade project is
"migrated a JSON-blob vector store to pgvector, chose and tuned an HNSW index, benchmarked
recall/latency against brute force, and knew when ANN was and wasn't worth it." You would get
the full arc — `vector(1024)` column (or `halfvec` for 2× memory/latency wins with negligible
quality loss), `hnsw (vector_cosine_ops)` index, a migration + backfill from the JSON text,
a recall-vs-latency benchmark proving the trade-off — plus one genuine engineering
side-benefit: today the ranking run issues one `/api/embeddings/similarity` HTTP call *per
decision vector* (potentially dozens per run), each shipping and JSON-parsing up to ~2000×22 KB
of vectors server-side. A single SQL query with an inner join over `job_ids` replaces that
protocol and the 5.5× storage bloat (22 KB JSON → 4 KB binary). That is a robustness and
latency win, not a ranking-quality win — say so on the CV.

- **Library/service:** **pgvector with HNSW** (extension in the existing Postgres — no new
  service; `CREATE INDEX ... USING hnsw (embedding vector_cosine_ops)`).
- **Effort:** 1–2 days in JobAgentWeb (migration, backfill, rewrite of the similarity
  endpoint), plus ~0.5 day on the JobAgent side if the endpoint contract changes.
- **Cost delta:** ~$0 (embedding API spend unchanged; server CPU and memory go *down*).
- **Explicitly not:** Qdrant/Milvus/Weaviate — see (b).

### 6. BM25 hybrid leg via Postgres full-text search — learning-value (may graduate to quality)

The textbook version of #2: `tsvector` column + GIN index over title+description in
JobAgentWeb, a BM25-style score as the sparse leg, fused with the dense leg (RRF again, or
weighted z-score). Worth doing *after* #1 and #2: it is the standard "hybrid search"
implementation interviewers ask about, and the harness will answer the genuinely open
question of whether corpus-statistics BM25 beats the hand-built structured-facet leg on this
corpus — postings are heavy on boilerplate ("benefits", "agile", "CI/CD"), which BM25's IDF
handles differently than your facet scoring. Either result is a good story.

- **Library/service:** Postgres built-in FTS (`to_tsvector`/`ts_rank_cd`, GIN index) —
  no new service; `pg_trgm` as a lighter alternative is *not* recommended (no IDF).
  If you want a standalone BM25 to compare: the `rank_bm25` Python package over the
  exported snapshot, inside the eval harness only.
- **Effort:** 1–2 days in JobAgentWeb (tsvector column, backfill, sparse-score endpoint),
  0.5 day JobAgent-side to fuse the leg.
- **Cost delta:** ~$0 per run; a few hours of one-off backfill CPU.

---

## (b) Considered and explicitly NOT recommended

| Idea | Why not |
|------|---------|
| **Dedicated vector DB (Qdrant, Milvus, Weaviate, Pinecone)** | A second stateful service, deployment, backup story, and client — for a corpus that fits in a few hundred MB inside the Postgres you already run. Zero quality gain at this scale, real operational cost. The only honest reason would be "I want Qdrant on my CV", and pgvector gives you the same learning value with none of the ops burden. |
| **HNSW for performance, as a quality-agnostic change** | There is no performance problem to solve. Brute-force over ≤2000×1024 is sub-millisecond; the actual latency in the retrieval leg is the N HTTP round-trips per decision vector, and that is fixed by the pgvector/query-shape cleanup in #5, not by ANN. Approximate search at this scale strictly loses a little quality to save time nobody is spending. |
| **Full chunked RAG (fixed-size chunk windows, per-chunk retrieval, chunk pool)** | Job postings are short, single-topic documents. The RAG chunking problem exists because a 200-page PDF can't fit in one embedding and its relevant passage must be isolated; a posting's "passage" is the posting. The one real truncation issue (the 2000-char cap) is fixed by #3, not by a chunk framework. A chunk pool would also multiply vectors ×5 for the similarity loop that already makes one HTTP call per decision vector. |
| **A different/additional cross-encoder (Cohere rerank-v4, Jina, BGE locally)** | No evidence of headroom. rerank-2 runs on 50 documents for ~$0.0015, feeds an RRF leg (not a hard cut), and the final ordering is made by the Opus listwise pass, which sees the full excerpt plus scores plus sub-scores. Swapping cross-encoders to chase the 50→20 selection would be churn with no measured defect behind it. Revisit only if harness #1 shows low recall@20 of known-applied jobs attributable specifically to the rerank leg. |
| **Agentic/graph RAG, retrieval loops, query decomposition over the corpus** | Solves problems this system doesn't have (multi-hop reasoning over long documents). The "query" is one candidate profile, stable day to day; the corpus is homogeneous and small. |
| **Replacing max-sim kNN against applied/rejected vectors with a single query embedding** | Already correctly designed — the comment in `score_pool_by_similarity` documents why max-sim beats a centroid over multi-modal applied history, and the −0.3× rejected max-sim penalty and HyDE cold-start are sound. Leave it. |
| **Replacing or reweighting RRF** | The k=60 RRF with an id-based tiebreaker is the standard, correct choice for fusing heterogeneous legs, and `rank_cache`/fingerprinting already depend on its stability. Adding more legs (#2, #6) is the right extension point; changing the fusion math itself is not. |
| **A vector index or dense-retrieval work on the *scorer/listwise* side** | The expensive legs are per-job Sonnet scoring (~$0.007/new job) and the two Opus calls (~$0.80/run). Retrieval infrastructure changes don't touch those numbers; if cost ever becomes the concern, the levers are batch limits, model tiering, and the existing `rank_cache` — not vector math. |

---

## (c) Summary table — additions, named services, effort, cost

| Change | Named service/library | Effort | Cost delta |
|-------|----------------------|--------|-----------|
| 1. Eval harness | `ranx` (metrics) + pytest-style replay, snapshot via existing API | 2–4 days | ~$0 per retrieval replay; ~$0.45 per full listwise+debate replay; a few $ one-off per experiment grid |
| 2. Structured-facet RRF leg | none (pure Python, reuses `fuse_by_rrf`) | 0.5–1 day | $0/run |
| 3. Embedding truncation fix | voyageai (existing), one-off re-embed | 0.5 day | ~$0.65 one-off; ~$0.0003/job ongoing |
| 4. Cross-family debate | `gemini-3-flash` or `gpt-5.1-mini` via OpenRouter | 0.5 day | parity (~$0.01–0.05/run) |
| 5. pgvector + HNSW *(learning-value)* | pgvector extension, HNSW (`vector_cosine_ops`), in existing Postgres | 1–2 days (JobAgentWeb) | ~$0; server resources decrease |
| 6. BM25 hybrid leg *(learning-value)* | Postgres FTS (`tsvector` + GIN, `ts_rank_cd`); `rank_bm25` for harness-only comparison | 1–2 days (JobAgentWeb) + 0.5 day | ~$0/run; one-off backfill CPU |

Recommended order: **1 → 2 → 3 → 4 → (5, 6 as learning-value side-quests)**. Every item
after #1 should be validated by #1 before it ships; #1 is also the only item that improves
the system even if every subsequent experiment fails.

---

## (d) Is "RAG" even the right frame?

Honestly: no, not as the primary frame. RAG is a pattern for *answering* with
retrieved evidence — query in, grounded generated answer out, with retrieval quality
determining answer fidelity. JobAgent generates no answer over retrieved content at all. What
it actually is, structurally, is a single-user **learning-to-rank recommendation system**: an
item corpus (postings), a user profile enriched by explicit feedback (applied/rejected
decisions distilled into a preference profile — this is straight collaborative-filtering
vocabulary, just with an LLM as the distiller), a recall stage (dense similarity + LLM
scoring fused by RRF), and a re-ranking cascade (cross-encoder → listwise LLM → adversarial
reviewer). The online Precision@k / divergence-case metric is a recommender's metric, not a
RAG metric; nobody measures Precision@10 of a chatbot's citations. That framing matters
practically: it says where the quality leverage lives — in the ranking cascade, the feedback
loop, and the evaluation, which is where this proposal's top picks (#1, #2, #4) all land —
and it is also the more honest and more interesting CV story than "I built a RAG pipeline":
"I built a personal learning-to-rank system with an LLM-in-the-loop feedback loop" describes
something rarer than chunk-and-embed. The retrieval component is real and worth modernizing
in the small ways above, but treating the system as "a RAG app" would push effort toward
scale problems it doesn't have (vector DBs, chunking, ANN) and away from the ranking and
measurement problems that actually decide whether this candidate sees their best-fit jobs
at rank 1 or rank 17.
