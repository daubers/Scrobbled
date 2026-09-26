"""Federation's own metrics (the core's HTTP metrics already cover rate/latency)."""

from prometheus_client import Counter, Gauge, Histogram

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

inbox_activities_total = Counter(
    "scrobbler_federation_inbox_activities_total",
    "Activities received: queued, duplicate, blocked or invalid at intake; then processed, "
    "rejected or failed",
    ["type", "result"],
)
signatures_total = Counter(
    "scrobbler_federation_signatures_total",
    "HTTP signature checks (in) and signing (out), by scheme and result",
    ["direction", "scheme", "result"],
)
deliveries_total = Counter(
    "scrobbler_federation_deliveries_total",
    "Outgoing deliveries: delivered, retry, abandoned (gave up or refused) or gone (410)",
    ["result"],
)
delivery_seconds = Histogram(
    "scrobbler_federation_delivery_seconds",
    "Time to deliver one activity to one inbox",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20),
)
queue = Gauge(
    "scrobbler_federation_queue",
    "Inbox items and deliveries by status (set by the worker)",
    ["queue", "status"],
    multiprocess_mode="max",
)
followers = Gauge(
    "scrobbler_federation_followers",
    "Followers across all users, by state (set by the worker)",
    ["state"],
    multiprocess_mode="max",
)
posts_total = Counter(
    "scrobbler_federation_posts_total",
    "Posts published: weekly summaries, milestones and now-playing",
    ["kind", "visibility"],
)
post_deletions_total = Counter(
    "scrobbler_federation_post_deletions_total",
    "Posts deleted, by reason",
    ["reason"],
)
milestone_checks_total = Counter(
    "scrobbler_federation_milestone_checks_total",
    "Milestone checks, by result: disabled, none or posted",
    ["result"],
)
weekly_total = Counter(
    "scrobbler_federation_weekly_total",
    "Weekly summary checks, by result: disabled, skipped_empty, already_posted or posted",
    ["result"],
)
profile_updates_total = Counter(
    "scrobbler_federation_profile_updates_total",
    "Now-playing Update(Person) pushes: sent, or throttled (still within the 5-minute "
    "window since the last one)",
    ["result"],
)
