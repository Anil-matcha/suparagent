"""How long a support thread may wait before the owner is told (BL-176).

Two answer times, counted in WORKING time (Monday to Friday, in the owner's time zone), so a thread that arrives on
Friday evening is not called late on Sunday:

  urgent   for the threads the worker ranked urgent, angry, at_risk or blocked   default 4 hours
  normal   for every other category                                              default 1 working day (24 hours)

The owner can change both in the business profile. The profile has fixed fields and no place for numbers, so the
worker is asked the question when it is set up and proposes adding the owner's times to the free-text Hours field in
plain words, for example "Answer within 2 days. Urgent threads within 2 hours." `windows_from_profile` reads that
sentence back, tolerantly, and keeps the defaults for anything it does not understand or that is out of range.

Pure module: no I/O. Used by services/support_unanswered.py.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

URGENT_CATEGORIES = frozenset({"urgent", "angry", "at_risk", "blocked"})
DEFAULT_URGENT_HOURS = 4.0
DEFAULT_NORMAL_HOURS = 24.0           # one working day
MIN_URGENT_HOURS = 0.25
MAX_NORMAL_HOURS = 24.0 * 14          # two working weeks; anything longer is not an answer time

_ANSWER = re.compile(r"answer(?:ing|s)?\s+(?:all\s+\w+\s+)?(?:within|in)\s+(\d+(?:\.\d+)?)\s*(?:working\s+)?(hours?|hrs?|days?)", re.I)
_URGENT = re.compile(r"urgent[^.;\n]{0,40}?(?:within|in)?\s*(\d+(?:\.\d+)?)\s*(hours?|hrs?|minutes?|mins?)", re.I)


@dataclass(frozen=True)
class Windows:
    urgent_hours: float = DEFAULT_URGENT_HOURS
    normal_hours: float = DEFAULT_NORMAL_HOURS

    def hours_for(self, category: str | None) -> float:
        return self.urgent_hours if category in URGENT_CATEGORIES else self.normal_hours


def windows_from_profile(hours_text: object) -> Windows:
    """The owner's answer times from the profile's Hours text, or the defaults. Never raises."""
    if not isinstance(hours_text, str) or not hours_text.strip():
        return Windows()
    normal, urgent = DEFAULT_NORMAL_HOURS, DEFAULT_URGENT_HOURS
    found = _ANSWER.search(hours_text)
    if found:
        amount = float(found.group(1)) * (24.0 if found.group(2).lower().startswith("d") else 1.0)
        if 1.0 <= amount <= MAX_NORMAL_HOURS:
            normal = amount
    found = _URGENT.search(hours_text)
    if found:
        amount = float(found.group(1)) / (60.0 if found.group(2).lower().startswith("m") else 1.0)
        if MIN_URGENT_HOURS <= amount <= normal:
            urgent = amount
    return Windows(urgent_hours=min(urgent, normal), normal_hours=normal)


def working_elapsed(since: datetime, now: datetime, zone: ZoneInfo) -> timedelta:
    """Time between `since` and `now` that falls on Monday to Friday in `zone`. Zero when `now` is not after `since`."""
    if now <= since:
        return timedelta(0)
    start, end = since.astimezone(zone), now.astimezone(zone)
    total = timedelta(0)
    cursor = start
    while cursor < end:
        next_midnight = (cursor + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        # A wall-clock midnight in the zone: DST days keep their real length because both ends are zone-aware.
        stop = min(next_midnight, end)
        if cursor.weekday() < 5:
            total += stop.astimezone(ZoneInfo("UTC")) - cursor.astimezone(ZoneInfo("UTC"))
        cursor = stop
    return total


def is_overdue(category: str | None, since: datetime, now: datetime, zone: ZoneInfo, windows: Windows) -> bool:
    return working_elapsed(since, now, zone) > timedelta(hours=windows.hours_for(category))
