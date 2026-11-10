"""Time helpers. Store UTC everywhere; convert to college time only for display and dates.

On Windows, Python has no built-in timezone database; the `tzdata` package
(in requirements.txt) provides it. Without it ZoneInfo("Asia/Kolkata") fails.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import current_app


def utcnow():
    """Timezone-aware current time in UTC. Never use naive datetime.now()."""
    return datetime.now(timezone.utc)


def college_tz():
    return ZoneInfo(current_app.config["APP_TIMEZONE"])


def to_local(moment):
    """A UTC datetime shown in college time."""
    return moment.astimezone(college_tz()) if moment is not None else None


def local_date(moment=None):
    """The calendar date in college time. 01:00 IST on 11 Nov is still 10 Nov in UTC."""
    return (moment or utcnow()).astimezone(college_tz()).date()