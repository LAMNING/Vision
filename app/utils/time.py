from datetime import datetime, timezone

__all__ = ['utcnow']


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
