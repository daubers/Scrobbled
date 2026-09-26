"""Pure date/time math for scheduled posts: which week is due, in a user's own zone.

Weeks run Monday 00:00 to Sunday 24:00 *local time*, computed in the zone itself (not as
a fixed UTC offset), so the window is correct across a daylight-saving change.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

POST_HOUR = 9  # local time a week's summary becomes postable, on the following Monday


def target_week(now_utc: datetime, tz: ZoneInfo) -> tuple[datetime, datetime, str]:
    """The [start, end) UTC window, and ISO year-week key (e.g. "2026-W02"), of the most
    recent Monday-Sunday week whose Monday-POST_HOUR-local posting gate has passed.

    Always resolves to exactly one week, so a worker that was down for a while catches up
    with a single post (the latest eligible week), not one per week it missed.
    """
    local_now = now_utc.astimezone(tz)
    gate_monday = local_now.date() - timedelta(days=local_now.weekday())
    gate = datetime(gate_monday.year, gate_monday.month, gate_monday.day, POST_HOUR, tzinfo=tz)
    if local_now < gate:
        gate_monday -= timedelta(days=7)
    week_start = gate_monday - timedelta(days=7)

    start_local = datetime(week_start.year, week_start.month, week_start.day, tzinfo=tz)
    end_local = start_local + timedelta(days=7)  # wall-clock +7 days: DST-correct on conversion
    year, week, _ = week_start.isocalendar()
    return start_local.astimezone(UTC), end_local.astimezone(UTC), f"{year}-W{week:02d}"


def resolve_timezone(name: str) -> ZoneInfo:
    """A user's timezone setting, or UTC for anything that isn't a valid IANA name (a
    bad or since-removed name in one user's settings must never crash the task for
    everyone else)."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def is_valid_timezone(name: str) -> bool:
    """For validating user input (reject a bad name with an error), as opposed to
    resolve_timezone's silent fallback (never crash the periodic task over one user's
    stale setting)."""
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True
