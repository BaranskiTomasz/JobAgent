# JobAgent retrieval and ranking modernization proposal

## Executive decision

JobAgent should modernize, but not by replacing its small exact scan with an approximate vector database. At this system's scale, the highest-value move is to build a reproducible offline evaluation loop, then use it to test two plausible retrieval improvements: better job representations and a lexical retrieval leg. The current pipeline is already substantially more mature than the one-paragraph summary suggests: it applies evidence-backed deterministic dealbreakers before paid scoring; scores each job with Sonnet; retrieves from at most 2,000 active jobs; combines dense similarity and the scorer with RRF; cross-encodes 50 jobs; listwise-ranks 20 plus three exploration samples; reviews that list; and caches listwise/debate output when the candidate set and ranking fingerprint are unchanged. Those details make measurement and representation more important than retrieval infrastructure.

Cost estimates below use the repository's configured rates: Sonnet 5 at $2/$10 per million input/output tokens, Opus 4.8 at $5/$25, `voyage-3-large` at $0.18 per million embedding tokens, and Voyage reranking at $0.05 per million processed tokens. They are directional because actual description lengths and model outputs vary. Current official Voyage pricing still lists `rerank-2` at $0.05/M tokens, while newer `rerank-2.5` and `rerank-3` are also $0.05/M; the latter should therefore be evaluated rather than assumed better ([Voyage pricing](https://docs.voyageai.com/docs/pricing)).

## (a) Recommended changes, ranked

### 1. Build a frozen, stage-aware offline evaluation harness

**Tags: quality, cost**
**Verdict: do this first; no other ranking change is defensible until JobAgent can measure incremental lift.**

The current evaluation is useful online monitoring, not an experiment harness. `evaluation/harness.py` merely proxies JobAgentWeb's applied/rejected report. Precision@5/@10 selects the best-ranked decided rows, apply-rate buckets mix many historical pipeline versions, and divergence cases cover only top-5 rejects and rank-16+ applies. These outcomes also have exposure and selection bias: the candidate mostly sees high-ranked jobs, and the same decisions are fed back into scorer prompts. There is no frozen corpus, label version, run manifest, stage ablation, or metric that tests whether top-50 retrieval retained relevant jobs. `force_rescore` is not a substitute because it writes into live state.

Create a read-only experiment command and versioned dataset, separate from production tables. Start with 150–300 postings sampled from multiple dates and deliberately include: top-ranked jobs, random jobs, exploration jobs, low-ranked jobs, auto-rejects, and near-duplicate postings. Have the candidate assign a small explicit label (`0` reject, `1` weak, `2` apply, `3` exceptional), hard-constraint violations, and preferably pairwise choices within ambiguous top-20 sets. Preserve "not seen/not judged" separately from rejection. Freeze the posting text, structured facts, candidate profile/preferences, timestamp, and all model/prompt identifiers.

Use **`ranx`** for Recall@50, nDCG@10, MRR, MAP, and paired significance tests, plus a small bootstrap module (NumPy/SciPy) for confidence intervals. Report at least:

- hard-dealbreaker false-negative and false-positive rates;
- dense retrieval Recall@50 and hybrid Recall@50;
- candidate-set recall after RRF and after the Voyage top-50 gate;
- nDCG@5/@10 and pairwise accuracy after scorer, reranker, listwise, and debate;
- stability across repeated stochastic listwise/debate runs;
- dollars per judged relevant job surfaced and latency per stage.

Run leave-future-out evaluation: tune only on older decisions and test on a newer frozen slice. Store every stage's ordered IDs so `dense only`, `dense + scorer RRF`, `+ cross-encoder`, `+ listwise`, and `+ debate` are replayable ablations. With only one candidate, confidence intervals and qualitative error slices matter more than leaderboard-style decimal differences.

**Specific technology:** `ranx`, Parquet files via `pyarrow`, and a lightweight experiment manifest in YAML/JSON; no experiment service is necessary.
**Effort:** 3–5 days for the harness and reports, plus 2–4 hours of candidate labeling initially.
**Cost delta:** near $0 for replaying cached stage outputs. A fresh 200-job Sonnet scoring experiment is roughly $2–$4 under plausible 4k–8k input and 100–250 output tokens per job; a baseline/challenger doubles that portion. Embedding and reranking tests are generally cents. Opus listwise reruns are likely tens of cents per 20-job batch, not per job. Track experiment usage under distinct run/module IDs; add every challenger model's exact price because unknown models currently calculate as $0.

### 2. Fix the job representation before adding generic chunks

**Tags: quality, learning-value**
**Verdict: test full/field-aware representations in shadow mode; the current first-2,000-character embedding is a credible recall weakness.**

The stored "one vector per posting" is not actually based on the full normalized excerpt: `embeddings/indexer.py` embeds title/company/location plus only `excerpt[:2000]`, even though `build_excerpt` retains 6,000 characters (8,000 for LinkedIn) and the later scorer/reranker/listwise stages see that longer text. Requirements, eligibility, on-call duties, or stack details commonly appear after the first 2,000 characters. This is more likely to hurt fit recall than the lack of ANN indexing.

Evaluate these representations side by side:

1. a single full cleaned posting vector;
2. a field-aware document assembled as title/role + responsibilities + required skills + location/eligibility + company/compensation, using source facts and the existing extractor output;
3. only if (2) still misses relevant jobs, three named vectors per posting: overview, requirements/skills, and constraints/offer, aggregated by max or a tuned weighted RRF at job level.

This is **multi-representation retrieval**, not arbitrary fixed-size chunking. A job is one decision object; splitting every 500 tokens can over-promote a posting because one attractive paragraph matches while a contradictory requirement elsewhere is ignored. Preserve job-level grouping and let deterministic dealbreakers and downstream holistic rankers see the whole posting.

Use **`voyage-4-large`** as a shadow challenger to the existing `voyage-3-large`, not an immediate migration. Voyage documents a 32k-token context and 1,024-dimensional default for `voyage-4-large`, so a full posting fits comfortably ([Voyage embeddings](https://docs.voyageai.com/docs/embeddings)). Never overwrite the current vector while comparing: the present `job_embeddings(job_id PRIMARY KEY, ...)` schema supports only one model/representation and would destroy the baseline. Key shadow embeddings by `(job_id, model, representation, representation_version)`.

**Specific technology:** Voyage `voyage-4-large`; PostgreSQL **pgvector** `vector(1024)` for typed shadow storage and exact cosine/dot-product queries. Voyage says its embeddings are normalized, making dot product and cosine rank-equivalent ([Voyage FAQ](https://docs.voyageai.com/docs/faq)).
**Effort:** 2–3 days for shadow schema, backfill, aggregation, and evaluation integration; another 1–2 days if three representations win.
**Cost delta:** embedding cost remains tiny. At the current $0.18/M rate, even 2,000 postings × 2,000 tokens is about $0.72 for a full backfill; three representations cost approximately the same if they partition the same text, with a small repeated-header overhead. Ongoing cost is fractions of a cent per new posting. Storage rises from roughly one to three 1,024-float vectors per job (about 4 KB each before row/index overhead), still trivial here.

### 3. Add a lexical candidate-generation leg, then fuse it with dense retrieval

**Tags: quality, learning-value**
**Verdict: a low-cost, testable recall hedge for exact technologies and role terms; ship only if it improves frozen-set Recall@50/nDCG.**

Dense similarity is good for semantic role equivalence but can blur exact discriminators such as `.NET`, `C#`, `Kafka`, versioned frameworks, security clearances, language requirements, or specific cloud products. JobAgent already has canonical extracted skills and direct questionnaire terms, so lexical retrieval can add genuinely different evidence. It also gives exploration a systematic source of candidates rather than relying only on random samples outside the current fused top 20.

Add PostgreSQL **`tsvector` + GIN** search over weighted title, canonical skills, role family, and cleaned description. Add **`pg_trgm`** matching for punctuation-heavy and variant technology names that PostgreSQL tokenization handles poorly. Generate the lexical query from positive desired fields only; do not include `avoided_tech` as positive search terms. Retrieve a lexical top 100 and dense top 100, union them, and use the existing RRF approach before the scorer/reranker gate. Tune leg weights only on the training split. pgvector itself recommends combining Postgres full-text search with vectors and RRF or a cross-encoder ([pgvector documentation](https://github.com/pgvector/pgvector)).

**Specific technology:** built-in PostgreSQL full-text search (`tsvector`, `websearch_to_tsquery`, GIN) plus `pg_trgm`, fused with the repository's RRF.
**Effort:** 1–2 days including indexes, canonical-term query construction, instrumentation, and eval.
**Cost delta:** no new model/API cost; modest database CPU and a few MB of indexes. Cross-encoder cost stays flat if the fused pool remains capped at 50. If the eval finds no rescued relevant postings, remove or leave the leg disabled.

### 4. Turn debate into an evaluated challenger, not an assumed improvement

**Tags: quality, cost**
**Verdict: first ablate it; if a reviewer helps, use an actually independent provider and only on uncertain/high-impact cases.**

The reviewer is different from the primary listwise model (`claude-sonnet-5` versus `claude-opus-4-8`), but it is not independent in provider/model-family terms. It sees the primary rank and reason, the same candidate context, overlapping job text, and the scorer's Anthropic-produced breakdown. Correlated judgment and anchoring are therefore likely. Its deterministic three-place nudge or bottom demotion can improve a miss, but it can also turn a reviewer hallucination into a large ranking change. No current metric isolates that delta.

On the frozen set compare: no debate; current debate; and repeated current debate to measure variance. If review adds statistically credible nDCG/pairwise lift or catches hard constraints without unacceptable false demotions, test an independent **OpenAI `gpt-5.4-mini` snapshot** with structured output. Use it only for top-10 jobs, jobs near the would-apply threshold, or jobs where scorer/listwise/reranker disagree strongly. The current model is a more honest independence test than merely changing the Anthropic prompt; OpenAI lists `gpt-5.4-mini` at $0.75/M input and $4.50/M output ([OpenAI announcement](https://openai.com/index/introducing-gpt-5.4-mini-and-nano/)). Pin a snapshot and add its rate to `MODEL_COSTS` before running it.

**Specific technology:** OpenAI Responses API with a pinned `gpt-5.4-mini` snapshot and JSON Schema structured output.
**Effort:** 1–2 days for an adapter, accounting, replay, and conditional gate.
**Cost delta:** at roughly 20 jobs × 1,000 input tokens plus 500 output tokens for the batch, about $0.017 per review; larger descriptions may put it around $0.02–$0.06. Disabling the current debate saves its Sonnet input/output cost; conditional review should be cheaper than reviewing every cached-set change. Provider diversification is not worth its operational complexity unless the eval shows lift.

### 5. Upgrade the Voyage reranker only as a same-price A/B test

**Tags: quality, cost**
**Verdict: cheap experiment, not a redesign.**

The existing cross-encoder already addresses the most common dense-retrieval limitation and processes only 50 jobs. Test `rerank-3` against `rerank-2` on exactly the same stored query/document pairs, measuring Recall@20 and nDCG@10 before listwise ranking. Current official pricing lists both at $0.05/M processed tokens, so a winner has no steady-state price penalty ([Voyage pricing](https://docs.voyageai.com/docs/pricing)). Do not add a second simultaneous cross-encoder: two highly related rerankers increase cost and complexity without necessarily adding independent signal.

**Specific technology:** Voyage **`rerank-3`**.
**Effort:** 4–8 hours including shadow outputs and evaluation.
**Cost delta:** no steady-state delta at the published rate; the one-time A/B duplicates a rerank call, normally fractions of a cent to a few cents for the 50-document pool.

## Learning-value pick, explicitly separate

After the quality work above, migrate embedding storage from JSON text to **pgvector `vector(1024)`**, but retain exact search at the present scale. This is worthwhile hands-on experience with typed vector storage, distance operators, migrations, metadata filtering, and hybrid SQL, and it removes JSON parsing/large Python matrix allocation from JobAgentWeb. It is not justified as a ranking-quality improvement: exact NumPy cosine over at most 2,000 postings already has perfect retrieval recall for the supplied vector scores. pgvector performs exact search by default; HNSW and IVFFlat deliberately trade recall for speed ([pgvector documentation](https://github.com/pgvector/pgvector)).

**Specific technology:** pgvector with exact `ORDER BY embedding <#> query LIMIT ...`; no ANN index initially.
**Effort:** 1–2 days including extension enablement, dual-write/backfill, validation against NumPy rankings, cutover, and rollback path.
**Cost delta:** no API cost; small database storage/CPU savings are likely, but immaterial in dollars. Add HNSW only after measured latency or corpus growth justifies it, and validate ANN recall against exact queries.

If the goal is specifically to learn a dedicated vector database, implement a short-lived **Qdrant** shadow spike with dense + sparse named vectors and server-side RRF, not a production migration. Qdrant supports dense/sparse prefetch and RRF/DBSF fusion ([Qdrant hybrid queries](https://qdrant.tech/documentation/search/hybrid-queries/)). Budget 1–2 days and $0 locally with Docker, but treat the result as portfolio/learning work: another production datastore is not warranted for 2,000 records.

## (b) Changes explicitly not recommended

### Do not add HNSW or IVFFlat now

An ANN index solves latency/scale, not ranking quality. The current operation is at most a 2,000 × 1,024 matrix calculation per query and exact search cannot miss a neighbor due to index approximation. Worse, current max-sim uses up to 50 applied and 50 rejected vectors, so an ANN implementation would need careful candidate-union logic; filtering by user state can further reduce ANN recall. Keep exact pgvector search even if storage is modernized. Revisit HNSW around sustained tens/hundreds of thousands of eligible vectors or a measured latency SLO breach. IVFFlat is especially unattractive for a tiny, changing corpus because it needs training and probe tuning.

### Do not move production retrieval to Qdrant/Pinecone/Weaviate

A dedicated vector service introduces synchronization, backup, access control, filtering, monitoring, and another bill/service boundary while Postgres already owns jobs and user states. It would not improve semantic relevance by itself. Qdrant is reasonable only as the labeled learning spike described above.

### Do not generically chunk every posting

Job postings are short, coherent decision units, and downstream judgment needs cross-section contradictions. Naive chunking multiplies vectors and lets a matching benefits/stack paragraph hide a disqualifying requirement. First remove the arbitrary 2,000-character truncation; then test structured field representations. Use chunking only if error analysis proves that long postings still lose relevant evidence.

### Do not replace RRF with an uncalibrated weighted sum

Embedding cosine, LLM 0–10 scores, and cross-encoder relevance scores are not calibrated to one another and change distribution across queries. RRF is a sensible robust default. Its current `k=60` and equal leg weights should be tuned only after the labeled set exists. A learned-to-rank model is premature with one candidate and a few hundred noisy decisions.

### Do not add another reranker on top of Voyage + Opus

The pipeline already has per-job Sonnet scoring, Voyage cross-encoding, Opus comparative ranking, and Sonnet review. An additional Cohere/Jina cross-encoder would add correlated complexity. A replacement A/B is sensible; an extra permanent pass is not.

### Do not assume a different debate provider is automatically better

Provider diversity is not the same as accuracy or useful error independence. The reviewer also inherits anchoring from the primary rank and reason. Only the ablation can establish whether review helps; the independent-provider trial is conditional on that result.

### Do not discard deterministic dealbreakers or replace them with embeddings

Salary floors, remote eligibility, seniority, company type, and language are constraint checks, not semantic-similarity tasks. The existing evidence-confidence and fail-open design is appropriate. Improve extractor coverage and measure constraint errors, but keep deterministic enforcement ahead of paid ranking.

### Do not optimize only the online precision number

Applied/rejected feedback remains the ultimate behavioral signal, but current precision is exposure-biased and mixes changing models, prompts, preferences, and pools. Keep it as longitudinal monitoring alongside apply-rate buckets and would-apply precision; do not use it alone to declare an architecture winner.

## Suggested execution order

1. Freeze and label the evaluation corpus; add stage-output replay and cost manifests.
2. Establish ablations for every existing stage, especially listwise and debate.
3. Shadow-test full/field-aware `voyage-4-large` representations and `rerank-3`.
4. Add the PostgreSQL lexical leg and compare recall/error slices.
5. Promote only changes that improve held-out candidate outcomes; then optionally migrate JSON vectors to exact pgvector for maintainability and learning value.

## (d) Is this really RAG?

Only partly. JobAgent does retrieve job postings using a candidate query and then supplies a shortlist to generative models, so "RAG" describes one mechanism. But the product is more accurately a single-user recommendation and learning-to-rank system with constraint filtering, candidate generation, multi-stage reranking, preference feedback, exploration, and a generative judge. It does not primarily retrieve knowledge so an LLM can synthesize a factual answer—the classic RAG objective. Framing it as recommendation/ranking is useful because it points to the right success measures: candidate-set recall, nDCG, pairwise preference accuracy, constraint violations, calibration, exposure bias, exploration, and cost per good match, rather than vector-database sophistication or answer faithfulness.
