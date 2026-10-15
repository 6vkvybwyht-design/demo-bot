"""Форматування даних для показу користувачу (час — у часовому поясі бізнесу)."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import html

from bot import texts
from bot.config import BusinessInfo
from bot.db.models import Service
from bot.services.booking import PlannedReminders


def fmt_time(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%H:%M")


def fmt_date(day: date) -> str:
    return f"{texts.WEEKDAYS_SHORT[day.weekday()]}, {day:%d.%m}"


def service_button(service: Service) -> str:
    return texts.SERVICE_BUTTON.format(
        name=service.name, duration=service.duration_min, price=service.price
    )


def progress_text(
    prompt: str,
    *,
    service: Service | None = None,
    master_name: str | None = None,
    any_master: bool = False,
    day: date | None = None,
    start: datetime | None = None,
    tz: ZoneInfo | None = None,
) -> str:
    """Підказка кроку з наростаючим підсумком над нею: послуга · майстер · дата · час."""
    parts = []
    if service is not None:
        parts.append(html.quote(service.name))
    if any_master:
        parts.append(texts.PROGRESS_ANY_MASTER)
    elif master_name:
        parts.append(html.quote(master_name))
    if day is not None:
        parts.append(fmt_date(day))
    if start is not None:
        parts.append(fmt_time(start, tz))
    if not parts:
        return prompt
    return texts.PROGRESS.format(line=texts.PROGRESS_SEPARATOR.join(parts), prompt=prompt)


def reminders_note(reminders: PlannedReminders) -> str:
    if reminders.before_24h and reminders.before_2h:
        return texts.REMINDERS_BOTH
    if reminders.before_24h:
        return texts.REMINDER_24H
    if reminders.before_2h:
        return texts.REMINDER_2H
    return ""


def _after_booking(reminders: PlannedReminders) -> str:
    return " ".join(filter(None, [reminders_note(reminders), texts.MANAGE_BOOKING]))


def booking_done_text(*, summary: str, address: str, reminders: PlannedReminders) -> str:
    return texts.BOOKING_DONE.format(
        summary=summary, address=html.quote(address), after=_after_booking(reminders)
    )


def booking_moved_text(*, summary: str, address: str, reminders: PlannedReminders) -> str:
    return texts.BOOKING_MOVED.format(
        summary=summary, address=html.quote(address), after=_after_booking(reminders)
    )


def fmt_when(start: datetime, tz: ZoneInfo) -> str:
    return texts.MOVE_WHEN.format(date=fmt_date(start.astimezone(tz).date()), time=fmt_time(start, tz))


def contacts_text(info: BusinessInfo) -> str:
    return texts.CONTACTS.format(
        name=html.quote(info.name), address=html.quote(info.address), phone=html.quote(info.phone)
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
