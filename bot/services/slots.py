"""Розрахунок вільних слотів.

Ядро (`compute_free_slots`) — чиста функція без БД. Решта функцій лише
завантажують дані з бази й викликають її. Усі datetime на вході й виході — aware UTC.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import BookingRules
from bot.db.models import (
    Booking,
    BookingStatus,
    Master,
    MasterService,
    Service,
    TimeOff,
    WorkingHours,
)

Interval = tuple[datetime, datetime]


# --- Чиста логіка -------------------------------------------------------------


def local_to_utc(day: date, at: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, at, tzinfo=tz).astimezone(UTC)


def day_bounds_utc(day: date, tz: ZoneInfo) -> Interval:
    """Початок і кінець місцевої доби в UTC (з урахуванням переходу на літній/зимовий час)."""
    return local_to_utc(day, time(0), tz), local_to_utc(day + timedelta(days=1), time(0), tz)


def subtract_intervals(base: Interval, busy: Iterable[Interval]) -> list[Interval]:
    """Віднімає зайняті проміжки від базового. Повертає впорядковані вільні проміжки."""
    free = [base]
    for b_start, b_end in sorted(busy):
        result: list[Interval] = []
        for f_start, f_end in free:
            if b_end <= f_start or b_start >= f_end:
                result.append((f_start, f_end))
                continue
            if f_start < b_start:
                result.append((f_start, b_start))
            if b_end < f_end:
                result.append((b_end, f_end))
        free = result
    return free


def compute_free_slots(
    *,
    day: date,
    schedule: WorkingHours | None,
    busy: Iterable[Interval],
    duration_min: int,
    rules: BookingRules,
    now: datetime,
) -> list[datetime]:
    """Вільні початки слотів для одного майстра на місцеву дату `day`.

    `schedule` — робочі години на день тижня `day` (None — вихідний).
    `busy` — TimeOff і підтверджені записи майстра (UTC).
    """
    if schedule is None:
        return []

    tz = rules.tz
    work_start = local_to_utc(day, schedule.start_time, tz)
    work_end = local_to_utc(day, schedule.end_time, tz)

    blocked = list(busy)
    if schedule.break_start is not None and schedule.break_end is not None:
        blocked.append(
            (local_to_utc(day, schedule.break_start, tz), local_to_utc(day, schedule.break_end, tz))
        )
    free = subtract_intervals((work_start, work_end), blocked)

    step = timedelta(minutes=rules.slot_step_min)
    duration = timedelta(minutes=duration_min)
    earliest = now + timedelta(minutes=rules.min_lead_time_min)

    slots: list[datetime] = []
    start = work_start
    while start + duration <= work_end:
        end = start + duration
        if start >= earliest and any(f_start <= start and end <= f_end for f_start, f_end in free):
            slots.append(start)
        start += step
    return slots


def pick_least_busy_master(candidate_ids: Sequence[int], bookings_per_master: Counter) -> int:
    """Майстер із найменшою кількістю записів за день; за рівності — з меншим id."""
    return min(candidate_ids, key=lambda m_id: (bookings_per_master[m_id], m_id))


# --- Робота з БД --------------------------------------------------------------


@dataclass(frozen=True)
class _MasterDay:
    schedule: WorkingHours | None
    busy: list[Interval]


async def _load_master_day(
    session: AsyncSession,
    master_id: int,
    day: date,
    tz: ZoneInfo,
    exclude_booking_id: int | None = None,
) -> _MasterDay:
    schedule = await session.scalar(
        select(WorkingHours).where(
            WorkingHours.master_id == master_id, WorkingHours.weekday == day.weekday()
        )
    )
    if schedule is None:
        return _MasterDay(None, [])

    day_start, day_end = day_bounds_utc(day, tz)
    time_off = await session.scalars(
        select(TimeOff).where(
            TimeOff.master_id == master_id,
            TimeOff.start_dt < day_end,
            TimeOff.end_dt > day_start,
        )
    )
    bookings_query = select(Booking).where(
        Booking.master_id == master_id,
        Booking.status == BookingStatus.CONFIRMED,
        Booking.start_dt < day_end,
        Booking.end_dt > day_start,
    )
    if exclude_booking_id is not None:
        # Для перенесення: власний запис клієнта не займає слот
        bookings_query = bookings_query.where(Booking.id != exclude_booking_id)
    bookings = await session.scalars(bookings_query)

    busy = [(t.start_dt, t.end_dt) for t in time_off] + [(b.start_dt, b.end_dt) for b in bookings]
    return _MasterDay(schedule, busy)


async def _master_does_service(session: AsyncSession, master_id: int, service_id: int) -> bool:
    found = await session.scalar(
        select(Master.id)
        .join(MasterService, MasterService.master_id == Master.id)
        .join(Service, Service.id == MasterService.service_id)
        .where(
            Master.id == master_id,
            Master.is_active,
            Service.id == service_id,
            Service.is_active,
        )
    )
    return found is not None


async def masters_for_service(session: AsyncSession, service_id: int) -> list[Master]:
    """Активні майстри, які виконують активну послугу."""
    result = await session.scalars(
        select(Master)
        .join(MasterService, MasterService.master_id == Master.id)
        .join(Service, Service.id == MasterService.service_id)
        .where(MasterService.service_id == service_id, Master.is_active, Service.is_active)
        .order_by(Master.id)
    )
    return list(result.all())


async def get_free_slots(
    session: AsyncSession,
    *,
    master_id: int,
    service: Service,
    day: date,
    rules: BookingRules,
    now: datetime,
    exclude_booking_id: int | None = None,
) -> list[datetime]:
    """Вільні слоти конкретного майстра на місцеву дату."""
    if not await _master_does_service(session, master_id, service.id):
        return []
    master_day = await _load_master_day(session, master_id, day, rules.tz, exclude_booking_id)
    return compute_free_slots(
        day=day,
        schedule=master_day.schedule,
        busy=master_day.busy,
        duration_min=service.duration_min,
        rules=rules,
        now=now,
    )


async def get_free_slots_any_master(
    session: AsyncSession,
    *,
    service: Service,
    day: date,
    rules: BookingRules,
    now: datetime,
) -> dict[datetime, list[int]]:
    """«Будь-який майстер»: об'єднання слотів. Ключ — початок слоту, значення — id вільних майстрів."""
    slots: dict[datetime, list[int]] = {}
    for master_id in [m.id for m in await masters_for_service(session, service.id)]:
        master_day = await _load_master_day(session, master_id, day, rules.tz)
        for start in compute_free_slots(
            day=day,
            schedule=master_day.schedule,
            busy=master_day.busy,
            duration_min=service.duration_min,
            rules=rules,
            now=now,
        ):
            slots.setdefault(start, []).append(master_id)
    return dict(sorted(slots.items()))


async def pick_master_for_slot(
    session: AsyncSession, *, candidate_ids: Sequence[int], day: date, rules: BookingRules
) -> int:
    """Із вільних на слот майстрів обирає того, хто має найменше записів у цей день."""
    day_start, day_end = day_bounds_utc(day, rules.tz)
    result = await session.scalars(
        select(Booking.master_id).where(
            Booking.master_id.in_(candidate_ids),
            Booking.status == BookingStatus.CONFIRMED,
            Booking.start_dt >= day_start,
            Booking.start_dt < day_end,
        )
    )
    return pick_least_busy_master(candidate_ids, Counter(result.all()))


async def get_available_dates(
    session: AsyncSession,
    *,
    service: Service,
    master_id: int | None,
    rules: BookingRules,
    now: datetime,
) -> list[date]:
    """Дати в межах горизонту запису, на які є хоча б один вільний слот.

    `master_id=None` — «будь-який майстер».
    """
    today = now.astimezone(rules.tz).date()
    dates: list[date] = []
    for offset in range(rules.horizon_days):
        day = today + timedelta(days=offset)
        if master_id is None:
            has_slots = bool(
                await get_free_slots_any_master(
                    session, service=service, day=day, rules=rules, now=now
                )
            )
        else:
            has_slots = bool(
                await get_free_slots(
                    session, master_id=master_id, service=service, day=day, rules=rules, now=now
                )
            )
        if has_slots:
            dates.append(day)
    return dates


async def is_slot_available(
    session: AsyncSession,
    *,
    master_id: int,
    service: Service,
    start: datetime,
    rules: BookingRules,
    now: datetime,
    exclude_booking_id: int | None = None,
) -> bool:
    """Повторна перевірка слоту перед підтвердженням запису (захист від подвійного запису)."""
    day = start.astimezone(rules.tz).date()
    slots = await get_free_slots(
        session,
        master_id=master_id,
        service=service,
        day=day,
        rules=rules,
        now=now,
        exclude_booking_id=exclude_booking_id,
    )
    return start.astimezone(UTC) in slots
