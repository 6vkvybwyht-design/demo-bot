"""Форматування даних для показу користувачу (час — у часовому поясі бізнесу)."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import html

from bot import texts
from bot.db.models import Service


def fmt_time(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%H:%M")


def fmt_date(day: date) -> str:
    return f"{texts.WEEKDAYS_SHORT[day.weekday()]}, {day:%d.%m}"


def service_button(service: Service) -> str:
    return texts.SERVICE_BUTTON.format(
        name=service.name, duration=service.duration_min, price=service.price
    )


def booking_summary(
    *, service: Service, master_name: str | None, start: datetime, tz: ZoneInfo
) -> str:
    """Деталі запису. `master_name=None` — «будь-який майстер»."""
    return texts.SUMMARY.format(
        service=html.quote(service.name),
        master=html.quote(master_name) if master_name else texts.ANY_MASTER_LABEL,
        date=fmt_date(start.astimezone(tz).date()),
        time=fmt_time(start, tz),
        duration=service.duration_min,
        price=service.price,
    )
