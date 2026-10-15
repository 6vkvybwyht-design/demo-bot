"""Логіка адмінки: перегляд записів, керування послугами, майстрами, графіком."""

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.config import Settings
from bot.db.models import Booking, BookingStatus, Master, Service, TimeOff, WorkingHours
from bot.services.booking import BookingNotFoundError
from bot.services.slots import day_bounds_utc, local_to_utc

# Статуси, які показуємо в списках адміна (скасовані — ні)
VISIBLE_STATUSES = (BookingStatus.CONFIRMED, BookingStatus.COMPLETED, BookingStatus.NO_SHOW)


def is_admin(user_id: int, settings: Settings) -> bool:
    return user_id in settings.admin_ids


def _with_details(query):
    return query.options(
        selectinload(Booking.client), selectinload(Booking.master), selectinload(Booking.service)
    )


# --- Перегляд записів ---------------------------------------------------------


async def bookings_for_day(session: AsyncSession, day: date, tz: ZoneInfo) -> list[Booking]:
    """Записи на місцеву дату, впорядковані за майстром і часом."""
    start, end = day_bounds_utc(day, tz)
    result = await session.scalars(
        _with_details(
            select(Booking)
            .join(Master, Master.id == Booking.master_id)
            .where(
                Booking.start_dt >= start,
                Booking.start_dt < end,
                Booking.status.in_(VISIBLE_STATUSES),
            )
            .order_by(Master.name, Booking.start_dt)
        )
    )
    return list(result.all())


async def week_counts(session: AsyncSession, first_day: date, tz: ZoneInfo) -> list[tuple[date, int]]:
    """Кількість записів на кожен із 7 днів, починаючи з `first_day`."""
    start, _ = day_bounds_utc(first_day, tz)
    _, end = day_bounds_utc(first_day + timedelta(days=6), tz)
    starts = await session.scalars(
        select(Booking.start_dt).where(
            Booking.start_dt >= start, Booking.start_dt < end, Booking.status.in_(VISIBLE_STATUSES)
        )
    )
    counts = Counter(dt.astimezone(tz).date() for dt in starts)
    return [(first_day + timedelta(days=i), counts[first_day + timedelta(days=i)]) for i in range(7)]


async def get_booking(session: AsyncSession, booking_id: int) -> Booking | None:
    return await session.scalar(
        _with_details(select(Booking).where(Booking.id == booking_id)).execution_options(
            populate_existing=True
        )
    )


def can_cancel(booking: Booking, now: datetime) -> bool:
    return booking.status is BookingStatus.CONFIRMED and booking.start_dt > now


def can_mark(booking: Booking, now: datetime) -> bool:
    """Позначити «відбувся» / «не прийшов» можна, коли візит уже почався."""
    return booking.status is BookingStatus.CONFIRMED and booking.start_dt <= now


async def cancel_booking_by_admin(session: AsyncSession, booking_id: int, now: datetime) -> Booking:
    booking = await get_booking(session, booking_id)
    if booking is None or not can_cancel(booking, now):
        raise BookingNotFoundError
    booking.status = BookingStatus.CANCELLED_BY_ADMIN
    await session.commit()
    return booking


async def mark_booking(
    session: AsyncSession, booking_id: int, status: BookingStatus, now: datetime
) -> Booking:
    if status not in (BookingStatus.COMPLETED, BookingStatus.NO_SHOW):
        raise ValueError(status)
    booking = await get_booking(session, booking_id)
    if booking is None or not can_mark(booking, now):
        raise BookingNotFoundError
    booking.status = status
    await session.commit()
    return booking


# --- Майбутні записи й конфлікти ----------------------------------------------


async def future_bookings(
    session: AsyncSession,
    now: datetime,
    *,
    master_id: int | None = None,
    service_id: int | None = None,
) -> list[Booking]:
    """Майбутні підтверджені записи майстра та/або послуги."""
    query = select(Booking).where(
        Booking.status == BookingStatus.CONFIRMED, Booking.start_dt > now
    )
    if master_id is not None:
        query = query.where(Booking.master_id == master_id)
    if service_id is not None:
        query = query.where(Booking.service_id == service_id)
    result = await session.scalars(_with_details(query.order_by(Booking.start_dt)))
    return list(result.all())


@dataclass(frozen=True)
class Hours:
    start: time
    end: time
    break_start: time | None = None
    break_end: time | None = None


def _fits(booking: Booking, hours: Hours, tz: ZoneInfo) -> bool:
    day = booking.start_dt.astimezone(tz).date()
    work_start = local_to_utc(day, hours.start, tz)
    work_end = local_to_utc(day, hours.end, tz)
    if not (work_start <= booking.start_dt and booking.end_dt <= work_end):
        return False
    if hours.break_start is not None:
        br_start = local_to_utc(day, hours.break_start, tz)
        br_end = local_to_utc(day, hours.break_end, tz)
        if booking.start_dt < br_end and br_start < booking.end_dt:
            return False
    return True


def schedule_conflicts(
    bookings: Iterable[Booking], weekday: int, hours: Hours | None, tz: ZoneInfo
) -> list[Booking]:
    """Записи на цей день тижня, які не вміщаються в новий графік (None — вихідний)."""
    return [
        b
        for b in bookings
        if b.start_dt.astimezone(tz).weekday() == weekday and (hours is None or not _fits(b, hours, tz))
    ]


async def time_off_conflicts(
    session: AsyncSession, master_id: int, start: datetime, end: datetime, now: datetime
) -> list[Booking]:
    return [
        b
        for b in await future_bookings(session, now, master_id=master_id)
        if b.start_dt < end and start < b.end_dt
    ]


# --- Послуги ------------------------------------------------------------------


async def list_services(session: AsyncSession) -> list[Service]:
    return list((await session.scalars(select(Service).order_by(Service.id))).all())


async def create_service(session: AsyncSession, name: str, duration_min: int, price: int) -> Service:
    service = Service(name=name, duration_min=duration_min, price=price)
    session.add(service)
    await session.commit()
    return service


async def update_service(session: AsyncSession, service_id: int, **fields) -> Service:
    service = await session.get(Service, service_id)
    for key, value in fields.items():
        if key not in {"name", "duration_min", "price", "is_active"}:
            raise ValueError(key)
        setattr(service, key, value)
    await session.commit()
    return service


# --- Майстри ------------------------------------------------------------------


async def list_masters(session: AsyncSession) -> list[Master]:
    return list((await session.scalars(select(Master).order_by(Master.id))).all())


async def get_master(session: AsyncSession, master_id: int) -> Master | None:
    return await session.scalar(
        select(Master)
        .where(Master.id == master_id)
        .options(selectinload(Master.services), selectinload(Master.working_hours))
        .execution_options(populate_existing=True)
    )


async def create_master(session: AsyncSession, name: str) -> Master:
    master = Master(name=name)
    session.add(master)
    await session.commit()
    return master


async def update_master(session: AsyncSession, master_id: int, **fields) -> Master:
    master = await session.get(Master, master_id)
    for key, value in fields.items():
        if key not in {"name", "telegram_id", "is_active"}:
            raise ValueError(key)
        setattr(master, key, value)
    await session.commit()
    return master


async def toggle_master_service(session: AsyncSession, master_id: int, service_id: int) -> bool:
    """Прив'язує або відв'язує послугу. Повертає True, якщо тепер прив'язана."""
    master = await get_master(session, master_id)
    service = await session.get(Service, service_id)
    if service in master.services:
        master.services.remove(service)
        linked = False
    else:
        master.services.append(service)
        linked = True
    await session.commit()
    return linked


# --- Графік і блокування часу -------------------------------------------------


async def set_working_hours(
    session: AsyncSession, master_id: int, weekday: int, hours: Hours | None
) -> None:
    """Задає робочі години на день тижня; None — вихідний."""
    current = await session.scalar(
        select(WorkingHours).where(WorkingHours.master_id == master_id, WorkingHours.weekday == weekday)
    )
    if hours is None:
        if current is not None:
            await session.delete(current)
    else:
        if current is None:
            current = WorkingHours(master_id=master_id, weekday=weekday)
            session.add(current)
        current.start_time, current.end_time = hours.start, hours.end
        current.break_start, current.break_end = hours.break_start, hours.break_end
    await session.commit()


async def create_time_off(
    session: AsyncSession, master_id: int, start: datetime, end: datetime, reason: str | None
) -> TimeOff:
    time_off = TimeOff(master_id=master_id, start_dt=start, end_dt=end, reason=reason)
    session.add(time_off)
    await session.commit()
    return time_off


# --- Розбір введеного адміном тексту ------------------------------------------


class InputError(ValueError):
    """Текст адміна не вдалося розібрати."""


_DASH = r"\s*[-–—]\s*"
_TIME = r"(\d{1,2}):(\d{2})"
_RANGE = re.compile(rf"{_TIME}{_DASH}{_TIME}")
DAY_OFF_WORDS = {"вихідний", "-"}


def _time(hour: str, minute: str) -> time:
    try:
        return time(int(hour), int(minute))
    except ValueError as exc:
        raise InputError from exc


def parse_hours(text: str) -> Hours | None:
    """«10:00-20:00» або «10:00-20:00 14:00-15:00» (з перервою); «вихідний» → None."""
    text = text.strip().lower()
    if text in DAY_OFF_WORDS:
        return None
    ranges = _RANGE.findall(text)
    if not ranges or len(ranges) > 2 or _RANGE.sub("", text).strip():
        raise InputError
    start, end = _time(*ranges[0][:2]), _time(*ranges[0][2:])
    if start >= end:
        raise InputError
    if len(ranges) == 1:
        return Hours(start, end)
    br_start, br_end = _time(*ranges[1][:2]), _time(*ranges[1][2:])
    if not (start < br_start < br_end < end):
        raise InputError
    return Hours(start, end, br_start, br_end)


MAX_TIME_OFF_DAYS = 62
_DATE = r"(\d{1,2})\.(\d{1,2})"
_PERIOD_DAY = re.compile(rf"{_DATE}")
_PERIOD_DAYS = re.compile(rf"{_DATE}{_DASH}{_DATE}")
_PERIOD_HOURS = re.compile(rf"{_DATE}\s+{_TIME}{_DASH}{_TIME}")


def _date(day: str, month: str, today: date) -> date:
    """Дата без року: найближча в майбутньому (сьогодні включно)."""
    try:
        candidate = date(today.year, int(month), int(day))
    except ValueError as exc:
        raise InputError from exc
    if candidate < today:
        candidate = candidate.replace(year=today.year + 1)
    return candidate


def parse_period(text: str, today: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Період блокування в UTC: «20.10 10:00-13:00», «20.10» (весь день), «20.10-25.10»."""
    text = text.strip()
    if match := _PERIOD_HOURS.fullmatch(text):
        day = _date(match[1], match[2], today)
        start, end = _time(match[3], match[4]), _time(match[5], match[6])
        if start >= end:
            raise InputError
        return local_to_utc(day, start, tz), local_to_utc(day, end, tz)
    if match := _PERIOD_DAYS.fullmatch(text):
        first = _date(match[1], match[2], today)
        last = _date(match[3], match[4], first)
        # «25.10-20.10» інакше означало б блокування майже на рік
        if (last - first).days > MAX_TIME_OFF_DAYS:
            raise InputError
        return day_bounds_utc(first, tz)[0], day_bounds_utc(last, tz)[1]
    if match := _PERIOD_DAY.fullmatch(text):
        return day_bounds_utc(_date(match[1], match[2], today), tz)
    raise InputError


def parse_int(text: str, minimum: int, maximum: int) -> int:
    text = text.strip()
    if not text.isdigit() or not (minimum <= int(text) <= maximum):
        raise InputError
    return int(text)


def parse_name(text: str, max_length: int = 100) -> str:
    name = " ".join(text.split())
    if not name or len(name) > max_length:
        raise InputError
    return name


def parse_telegram_id(text: str) -> int | None:
    """Числовий Telegram ID або «-», щоб прибрати."""
    text = text.strip()
    if text == "-":
        return None
    if not text.isdigit():
        raise InputError
    return int(text)
