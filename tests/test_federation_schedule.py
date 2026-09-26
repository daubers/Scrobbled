"""Pure week-boundary math for scheduled posts (protocol/schedule.py)."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from scrobbler.federation.protocol import schedule

# Confirmed with date().isocalendar(): 2025-12-29, 2026-01-05 and 2026-01-12 are Mondays,
# ISO weeks 2026-W01, 2026-W02 and 2026-W03 respectively (the first spans the year
# boundary: Dec 29 2025 is in ISO week 2026-W01).
STABLE_ZONES = ["UTC", "Europe/London", "Pacific/Auckland", "America/Los_Angeles"]


def local(zone: str, *args) -> datetime:
    return datetime(*args, tzinfo=ZoneInfo(zone))


@pytest.mark.parametrize("zone", STABLE_ZONES)
def test_just_before_the_gate_targets_the_week_before_last(zone):
    now = local(zone, 2026, 1, 12, 8, 59).astimezone(UTC)
    start, end, key = schedule.target_week(now, ZoneInfo(zone))
    assert (start, end, key) == (
        local(zone, 2025, 12, 29, 0, 0).astimezone(UTC),
        local(zone, 2026, 1, 5, 0, 0).astimezone(UTC),
        "2026-W01",
    )


@pytest.mark.parametrize("zone", STABLE_ZONES)
def test_at_the_gate_targets_the_week_that_just_ended(zone):
    now = local(zone, 2026, 1, 12, 9, 0).astimezone(UTC)
    start, end, key = schedule.target_week(now, ZoneInfo(zone))
    assert (start, end, key) == (
        local(zone, 2026, 1, 5, 0, 0).astimezone(UTC),
        local(zone, 2026, 1, 12, 0, 0).astimezone(UTC),
        "2026-W02",
    )


@pytest.mark.parametrize("zone", STABLE_ZONES)
def test_any_time_until_the_next_gate_targets_the_same_week(zone):
    """The whole week between two Monday-09:00 gates targets the same week just ended."""
    midweek = local(zone, 2026, 1, 14, 15, 30).astimezone(UTC)  # a Wednesday afternoon
    _, _, key = schedule.target_week(midweek, ZoneInfo(zone))
    assert key == "2026-W02"


def test_target_week_across_a_daylight_saving_change():
    """Europe/London springs forward on 2026-03-29: the week Mon 23 Mar - Mon 30 Mar is
    only 6 days 23 hours of real (UTC) time, even though it's a full 7 local days."""
    tz = ZoneInfo("Europe/London")
    gate = datetime(2026, 3, 30, 9, 0, tzinfo=tz).astimezone(UTC)  # 08:00 UTC (BST = UTC+1)

    start, end, key = schedule.target_week(gate, tz)
    assert key == "2026-W13"
    assert start == datetime(2026, 3, 23, 0, 0, tzinfo=UTC)  # still GMT
    assert end == datetime(2026, 3, 29, 23, 0, tzinfo=UTC)  # BST has started: UTC+1
    assert end - start == timedelta(days=6, hours=23)

    just_before = gate - timedelta(minutes=1)
    _, _, earlier_key = schedule.target_week(just_before, tz)
    assert earlier_key == "2026-W12"


def test_a_worker_down_for_weeks_only_targets_the_most_recent_one():
    """target_week is a pure function of `now`: however much time has passed, it always
    names exactly one week (the latest eligible one), never a backlog of several."""
    tz = ZoneInfo("UTC")
    # A month after 2026-W02 (confirmed above); Feb 2 2026 is the Monday of ISO week 6.
    right_after_an_outage = datetime(2026, 2, 2, 10, 0, tzinfo=UTC)
    _, _, key = schedule.target_week(right_after_an_outage, tz)
    assert key == "2026-W05"  # the week that ended just before "now" (Jan 26 - Feb 2)


@pytest.mark.parametrize("name", ["not/a/zone", "", "Mars/Phobos", "utc but wrong"])
def test_resolve_timezone_falls_back_to_utc_for_bad_names(name):
    assert schedule.resolve_timezone(name) == ZoneInfo("UTC")


def test_resolve_timezone_passes_through_valid_names():
    assert schedule.resolve_timezone("Europe/London") == ZoneInfo("Europe/London")
    assert schedule.resolve_timezone("UTC") == ZoneInfo("UTC")
