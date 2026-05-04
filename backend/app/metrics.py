from prometheus_client import Counter, Histogram

# ── Research pipeline ────────────────────────────────────────────────────────

research_requests_total = Counter(
    "research_requests_total",
    "Research requests by route and final status",
    ["route", "status"],  # status: completed | cancelled | error
)

# Node duration uses only node_name + route as labels — ticker would create
# unbounded cardinality and make Prometheus cardinality explode.
agent_duration_seconds = Histogram(
    "agent_duration_seconds",
    "Agent node wall-clock duration in seconds",
    ["node_name", "route"],
    buckets=[0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 40.0, 60.0],
)

# ── Cache ────────────────────────────────────────────────────────────────────

cache_hit_total = Counter(
    "cache_hit_total",
    "Full-report cache lookups by result",
    ["result"],  # result: hit | miss
)

# ── LLM token usage ──────────────────────────────────────────────────────────

llm_tokens_total = Counter(
    "llm_tokens_total",
    "LLM tokens consumed, split by model and token type",
    ["model", "token_type"],  # token_type: input | output | cache_read
)
