"""The owner's answer times and how waiting is counted (services/support_windows.py): pure."""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.support_windows import (
    DEFAULT_NORMAL_HOURS, DEFAULT_URGENT_HOURS, URGENT_CATEGORIES, Windows, is_overdue, windows_from_profile, working_elapsed)

UTC_ZONE = ZoneInfo("UTC")


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)   # 2026-11-23 is a Monday


def test_the_defaults_are_four_hours_for_urgent_kinds_and_one_working_day_for_the_rest():
    windows = Windows()
    assert (windows.urgent_hours, windows.normal_hours) == (DEFAULT_URGENT_HOURS, DEFAULT_NORMAL_HOURS) == (4.0, 24.0)
    assert URGENT_CATEGORIES == {"urgent", "angry", "at_risk", "blocked"}
    for category in URGENT_CATEGORIES:
        assert windows.hours_for(category) == 4.0
    for category in ("billing", "bug", "question", "other", None, "something_new"):
        assert windows.hours_for(category) == 24.0


@pytest.mark.parametrize("text, urgent, normal", [
    (None, 4.0, 24.0), ("", 4.0, 24.0), (5, 4.0, 24.0), ("Mon-Fri 9 to 5 in Lisbon", 4.0, 24.0),
    ("Answer within 2 days. Urgent threads within 2 hours.", 2.0, 48.0),
    ("We answer within 8 working hours; urgent ones in 30 minutes", 0.5, 8.0),
    ("answering in 1 day, urgent 1 hr", 1.0, 24.0),
    ("Answer within 400 days", 4.0, 24.0),               # out of range: the default stays
    ("Answer within 3 hours. Urgent within 6 hours.", 3.0, 3.0),   # urgent can never be longer than the normal time
    ("Answer within 0 hours", 4.0, 24.0),
])
def test_the_profile_hours_text_is_read_tolerantly(text, urgent, normal):
    windows = windows_from_profile(text)
    assert (windows.urgent_hours, windows.normal_hours) == (urgent, normal)


def test_only_working_time_counts():
    friday_night = at(27, 22)    # Friday 22:00
    assert working_elapsed(friday_night, at(30, 1), UTC_ZONE) == timedelta(hours=3)   # 2h of Friday + 1h of Monday
    assert working_elapsed(at(28, 9), at(29, 20), UTC_ZONE) == timedelta(0)            # all of it is a weekend
    assert working_elapsed(at(25, 9), at(26, 9), UTC_ZONE) == timedelta(hours=24)
    assert working_elapsed(at(26, 9), at(25, 9), UTC_ZONE) == timedelta(0)             # now before since


def test_the_owners_time_zone_decides_where_the_weekend_starts():
    lisbon_like = ZoneInfo("Pacific/Auckland")           # +13 in November: Friday 12:00 UTC is already Saturday there
    assert working_elapsed(at(27, 12), at(27, 20), lisbon_like) == timedelta(0)
    assert working_elapsed(at(27, 12), at(27, 20), UTC_ZONE) == timedelta(hours=8)


def test_overdue_is_strictly_longer_than_the_window_for_that_category():
    windows = Windows()
    since = at(24, 8)
    assert not is_overdue("angry", since, at(24, 12), UTC_ZONE, windows)                 # exactly 4h: not yet
    assert is_overdue("angry", since, at(24, 12, 1), UTC_ZONE, windows)
    assert not is_overdue("question", since, at(24, 12, 1), UTC_ZONE, windows)
    assert is_overdue("question", since, at(25, 8, 1), UTC_ZONE, windows)
