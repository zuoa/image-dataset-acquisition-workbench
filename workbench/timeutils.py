"""Use a fixed UTC+08:00 timezone for user-facing dates and times."""

from datetime import datetime, timedelta, timezone

DISPLAY_TIMEZONE = timezone(timedelta(hours=8))


def display_now() -> datetime:
    return datetime.now(DISPLAY_TIMEZONE)


def format_datetime(value: str, fmt: str = "%Y-%m-%d %H:%M:%S UTC+08:00") -> str:
    timestamp = datetime.fromisoformat(value)
    # Archive timestamps are UTC, including legacy values without an offset.
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(DISPLAY_TIMEZONE).strftime(fmt)
