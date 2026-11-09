"""Time helpers. Store UTC everywhere; convert to college time only for display and dates."""
from datetime import datetime, timezone


def utcnow():
    """Timezone-aware current time in UTC. Never use naive datetime.now()."""
    return datetime.now(timezone.utc)