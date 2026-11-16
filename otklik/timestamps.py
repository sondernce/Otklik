"""UTC timestamps shared by booking operations and legacy data migrations."""
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


def instant(value):
    if not isinstance(value, str):
        raise ValueError('Проверьте дату и время.')
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError('Проверьте дату и время.') from None
    if result.tzinfo is None or result.second or result.microsecond:
        raise ValueError('Время должно содержать часовой пояс и целые минуты.')
    return result.astimezone(timezone.utc)
