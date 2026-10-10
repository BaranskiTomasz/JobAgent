import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_MODEL = "claude-sonnet-4-6"          # CV parsing, legacy scorer
CLAUDE_EXTRACT_MODEL  = "claude-haiku-4-5-20251001"
CLAUDE_CATALOG_EXTRACT_MODEL = os.getenv("CLAUDE_CATALOG_EXTRACT_MODEL", "claude-haiku-5-5")
CLAUDE_RANK_MODEL     = "claude-opus-4-8"
CLAUDE_DISTILL_MODEL  = "claude-opus-4-8"

VOYAGE_API_KEY      = os.getenv("VOYAGE_API_KEY")
VOYAGE_EMBED_MODEL  = "voyage-3-large"
VOYAGE_RERANK_MODEL = "rerank-2"

SCORING = {
    # Below this, evaluator/scorer.py auto-rejects instead of leaving it in 'new'. Reversible.
    "auto_reject_at_or_below": 1.0,
}

QUERY_PRUNING = {
    "source": "linkedin",  # only source slow/paced enough for wasted queries to matter
    "min_terminal_sample": 20,  # terminally-decided jobs needed before a reject rate is trusted
    "reject_rate_threshold": 0.95,  # LinkedIn's baseline reject rate is already ~0.65
    "max_success_rate": 0.05,  # above this share applied/reviewed, protected regardless of reject rate
    "min_searches_for_zero_yield": 5,  # never excluded if it's produced any applied/reviewed job
    # Lower, human-reviewed bar: a query with zero applied jobs and a reject rate at or
    # above this is surfaced as a suggestion when Run Agent starts, even though its
    # reviewed jobs keep max_success_rate from auto-excluding it (see
    # collector.query_pruning.suggest_queries_for_review).
    "suggestion_reject_rate_threshold": 0.70,
}

RANKING = {
    "top_n_rerank":        50,
    "top_n_listwise":      20,
    "min_score_for_ranking": 2.0,  # below scorer's own near-dealbreaker cutoff, skip paid ranking
}

EXPLORATION = {
    # Randomly-sampled jobs from outside the top-N pool, given a real Opus +
    # debate look anyway, tests whether the deterministic pipeline (embedding
    # similarity, RRF, cross-encoder rerank) is systematically burying good
    # jobs lower down. Extends the listwise pool (top_n_listwise + slots) on
    # exploration days rather than displacing real top-N candidates, so no
    # candidate loses a slot to make room. See ranker/exploration.py.
    "slots_per_day": 3,
}

WOULD_APPLY = {
    # Phase 1 of auto-apply: flag-and-validate only, not a live auto-apply threshold yet.
    "score_floor": 7.0,
    # A job's score alone used to be the whole gate, a job scoring just above the
    # floor but ranked deep in the listwise pool (weaker on the holistic judgment
    # Opus/debate apply, stack fit, growth potential, company quality, and any
    # overrated/underrated nudge) is a weaker candidate than the score suggests on
    # its own. top_n_listwise itself is the outer bound (every ranked job already
    # has rank <= top_n_listwise); this is a tighter inner bound on top of that.
    "rank_ceiling": 10,
}

# Pricing, verified live against docs.anthropic.com / docs.voyageai.com, July 2026.
# Update if Anthropic/Voyage change their rates. Format: (input $/1M tokens, output $/1M tokens)
MODEL_COSTS: dict[str, tuple[float, float]] = {
    "claude-opus-4-8":            (5.00,  25.00),   # was (15, 75), Opus 4.8 launched at a lower rate
    "claude-sonnet-4-6":          (3.00,  15.00),
    "claude-haiku-4-5-20251001":  (1.00,   5.00),   # was (0.25, 1.25), undercounted actual Haiku 4.5 rate
    "claude-haiku-5-5":            (0.10,   0.50),
    "voyage-3-large":             (0.18,   0.00),   # was 0.06, per 1M embed tokens
    "rerank-2":                   (0.05,   0.00),   # per 1M tokens (rerank billing is now token-based, not per-query)
}

# Prompt-cache pricing is a fixed multiplier of a model's base input rate, same across
# every Claude model (docs.anthropic.com). Only the default 5-minute ephemeral TTL is
# used anywhere in this codebase (evaluator/scorer.py's cache_control), so there's no
# 1-hour-TTL rate to track.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER  = 0.1

_ROOT = os.path.dirname(os.path.abspath(__file__))

LINKEDIN = {
    "search_url": "https://www.linkedin.com/jobs/search/",
}

AGENT = {
    "headless": False,
    "reports_path": "data/reports",
    "seed_urls_path": "data/seed_urls.txt",
    "chrome_profile": "data/chrome_profile",
}

# WireGuard tunnel address, not the public HTTPS URL, JobAgentWeb's own per-user
# session login is the access control (Caddy just reverse-proxies), but routing API
# traffic over a private tunnel instead of the public domain is a deliberate choice,
# not a requirement. See ../JobAgentWeb/README.md#deployment.
JOBAGENTWEB_BASE_URL = os.getenv("JOBAGENTWEB_BASE_URL", "http://10.66.0.1:8000")

# When set, api_client.py authenticates every request with this static key
# (X-JobAgent-Api-Key header) instead of the browser-style session cookie,
# this JobAgent installation never needs an interactive `scripts/login.py` run
# again, and isn't affected by JobAgentWeb's session_epoch logout mechanism.
# Must match JOBAGENT_API_KEY in JobAgentWeb's own .env on the server; traffic
# still only ever crosses the private WireGuard tunnel (JOBAGENTWEB_BASE_URL
# above), never the public HTTPS domain. Leave unset to keep using session-
# cookie login instead.
JOBAGENT_API_KEY = os.getenv("JOBAGENT_API_KEY")

STEALTH = {
    "desc_delay_min": 8,   # reading-time delay after a description loads, clamped to this range
    "desc_delay_max": 45,
    "desc_reading_wpm": 200,
    "search_glance_min": 5,   # flat "glance at results" pause, plus below per newly-found job
    "search_glance_max": 20,
    "search_new_min": 10,
    "search_new_max": 30,
    "batch_size": 10,          # descriptions fetched per browser session
    "batch_pause_min": 120,    # pause between description batches, seconds
    "batch_pause_max": 600,
    "distract_every_n_batches": 2,  # visit LinkedIn feed every N batches (0 = never)
}

if not ANTHROPIC_API_KEY:
    raise ValueError("ANTHROPIC_API_KEY not found. Add it to .env.")
if not VOYAGE_API_KEY:
    raise ValueError("VOYAGE_API_KEY not found. Add it to .env. Get one at voyageai.com.")
