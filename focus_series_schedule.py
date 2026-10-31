"""Fixed weekly viewing windows; only the protected system clock is used."""
import datetime as dt
import math
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo('Europe/Moscow')
WEEKDAYS = frozenset((0, 2, 4, 6))  # Monday, Wednesday, Friday, Sunday.
START = dt.time(20, 30)
END = dt.time(22, 0)
LABEL = 'Пн, Ср, Пт, Вс · 20:30–22:00 · Москва'


def window_at(now):
    if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
        raise ValueError('Некорректное системное время.')
    local = dt.datetime.fromtimestamp(now, TIMEZONE)
    for offset in range(8):
        day = local.date() + dt.timedelta(days=offset)
        if day.weekday() not in WEEKDAYS:
            continue
        start = dt.datetime.combine(day, START, TIMEZONE).timestamp()
        end = dt.datetime.combine(day, END, TIMEZONE).timestamp()
        if now < end:
            return {'open': start <= now < end, 'start': start, 'end': end}
    raise RuntimeError('Не найдено следующее разрешённое окно сериала.')
