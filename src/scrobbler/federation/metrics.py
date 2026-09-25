"""Federation's own metrics (the core's HTTP metrics already cover rate/latency)."""

from prometheus_client import Counter

lookups_total = Counter(
    "scrobbler_federation_lookups_total",
    "Fediverse lookups answered: WebFinger, actor, NodeInfo, profile",
    ["kind", "result"],
)
sharing_changes_total = Counter(
    "scrobbler_federation_sharing_changes_total",
    "Users switching fediverse sharing on or off",
    ["change"],
)
inbox_requests_total = Counter(
    "scrobbler_federation_inbox_requests_total",
    "Deliveries received at an inbox (not yet processed: phase 2)",
    ["inbox"],
)

# Every scrobbler_federation_* series name, for the dashboard consistency test.
SERIES = (
    "scrobbler_federation_lookups_total",
    "scrobbler_federation_sharing_changes_total",
    "scrobbler_federation_inbox_requests_total",
)
