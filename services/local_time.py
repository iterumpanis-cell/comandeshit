"""Local date/time helpers used by the bot."""

from datetime import date, datetime
from zoneinfo import ZoneInfo


MADRID_TZ = ZoneInfo("Europe/Madrid")


def local_today() -> date:
    """Return today's date in the business timezone, not the host timezone."""
    return datetime.now(MADRID_TZ).date()
