"""Розрахунок вільних слотів.

Ядро (`compute_free_slots`) — чиста функція без БД. Решта функцій лише
завантажують дані з бази й викликають її. Усі datetime на вході й виході — aware UTC.
"""

from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import Enum
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
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


class DayPart(Enum):
    MORNING = "morning"  # до 12:00
    AFTERNOON = "afternoon"  # 12:00–17:00
    EVENING = "evening"  # після 17:00


AFTERNOON_START = time(12, 0)
EVENING_START = time(17, 0)


def day_part(start: datetime, tz: ZoneInfo) -> DayPart:
    local = start.astimezone(tz).time()
    if local < AFTERNOON_START:
        return DayPart.MORNING
    if local < EVENING_START:
        return DayPart.AFTERNOON
    return DayPart.EVENING


def group_by_day_part(
    starts: Iterable[datetime], tz: ZoneInfo
) -> list[tuple[DayPart, list[datetime]]]:
    """Групує слоти за частинами дня (за місцевим часом). Порожні групи пропускає."""
    groups: dict[DayPart, list[datetime]] = {part: [] for part in DayPart}
    for start in sorted(starts):
        groups[day_part(start, tz)].append(start)
    return [(part, items) for part, items in groups.items() if items]


def pick_least_busy_master(candidate_ids: Sequence[int], bookings_per_master: Counter) -> int:
    """Майстер із найменшою кількістю записів за день; за рівності — з меншим id."""
    return min(candidate_ids, key=lambda m_id: (bookings_per_master[m_id], m_id))


# --- Робота з БД --------------------------------------------------------------


class SlotFilter(Protocol):
    """Додаткова умова для слотів, крім графіка майстра (наприклад, ресурси спа)."""

    async def for_day(
        self, session: AsyncSession, day: date, rules: BookingRules, exclude_booking_id: int | None
    ) -> Callable[[datetime], bool]: ...


async def _apply_filter(
    starts: list[datetime],
    resources: SlotFilter | None,
    session: AsyncSession,
    day: date,
    rules: BookingRules,
    exclude_booking_id: int | None,
) -> list[datetime]:
    if resources is None or not starts:
        return starts
    allowed = await resources.for_day(session, day, rules, exclude_booking_id)
    return [start for start in starts if allowed(start)]


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
        # Майстер зайнятий і в записах, де він другий (масаж для двох)
        or_(Booking.master_id == master_id, Booking.second_master_id == master_id),
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
    duration_min: int | None = None,
    resources: SlotFilter | None = None,
    staff_count: int = 1,
    partner_id: int | None = None,
) -> list[datetime]:
    """Вільні слоти конкретного майстра на місцеву дату.

    `duration_min` — якщо тривалість не з послуги (розмір тварини, перенесення запису).
    `resources` — додатково лишає слоти, на які вистачає ресурсів (кабінети спа тощо).
    `staff_count=2` — послугу виконують двоє: лишаємо слоти, де вільний ще хтось
    із майстрів послуги, а якщо задано `partner_id` — саме цей майстер (перенесення).
    """
    if not await _master_does_service(session, master_id, service.id):
        return []
    master_day = await _load_master_day(session, master_id, day, rules.tz, exclude_booking_id)
    duration = duration_min or service.duration_min
    starts = compute_free_slots(
        day=day,
        schedule=master_day.schedule,
        busy=master_day.busy,
        duration_min=duration,
        rules=rules,
        now=now,
    )
    if staff_count > 1 and starts:
        if partner_id is not None:
            partner = set(
                await get_free_slots(
                    session, master_id=partner_id, service=service, day=day, rules=rules, now=now,
                    exclude_booking_id=exclude_booking_id, duration_min=duration,
                )
            )
            starts = [start for start in starts if start in partner]
        else:
            others = await get_free_slots_any_master(
                session, service=service, day=day, rules=rules, now=now, duration_min=duration,
                exclude_booking_id=exclude_booking_id,
            )
            starts = [
                start for start in starts
                if len(set(others.get(start, [])) - {master_id}) >= staff_count - 1
            ]
    return await _apply_filter(starts, resources, session, day, rules, exclude_booking_id)


async def get_free_slots_any_master(
    session: AsyncSession,
    *,
    service: Service,
    day: date,
    rules: BookingRules,
    now: datetime,
    duration_min: int | None = None,
    resources: SlotFilter | None = None,
    staff_count: int = 1,
    exclude_booking_id: int | None = None,
) -> dict[datetime, list[int]]:
    """«Будь-який майстер»: об'єднання слотів. Ключ — початок слоту, значення — id вільних майстрів.

    `staff_count=2` — лише слоти, де вільні щонайменше двоє майстрів послуги.
    """
    slots: dict[datetime, list[int]] = {}
    for master_id in [m.id for m in await masters_for_service(session, service.id)]:
        master_day = await _load_master_day(session, master_id, day, rules.tz, exclude_booking_id)
        for start in compute_free_slots(
            day=day,
            schedule=master_day.schedule,
            busy=master_day.busy,
            duration_min=duration_min or service.duration_min,
            rules=rules,
            now=now,
        ):
            slots.setdefault(start, []).append(master_id)
    slots = {start: ids for start, ids in slots.items() if len(ids) >= staff_count}
    allowed = set(
        await _apply_filter(list(slots), resources, session, day, rules, exclude_booking_id)
    )
    return {start: ids for start, ids in sorted(slots.items()) if start in allowed}


async def _bookings_per_master(
    session: AsyncSession, candidate_ids: Sequence[int], day: date, rules: BookingRules
) -> Counter:
    """Скільки записів у кожного майстра цього дня (і як основного, і як другого)."""
    day_start, day_end = day_bounds_utc(day, rules.tz)
    result = await session.execute(
        select(Booking.master_id, Booking.second_master_id).where(
            or_(Booking.master_id.in_(candidate_ids), Booking.second_master_id.in_(candidate_ids)),
            Booking.status == BookingStatus.CONFIRMED,
            Booking.start_dt >= day_start,
            Booking.start_dt < day_end,
        )
    )
    return Counter(m_id for row in result.all() for m_id in row if m_id is not None)


async def pick_master_for_slot(
    session: AsyncSession, *, candidate_ids: Sequence[int], day: date, rules: BookingRules
) -> int:
    """Із вільних на слот майстрів обирає того, хто має найменше записів у цей день."""
    counts = await _bookings_per_master(session, candidate_ids, day, rules)
    return pick_least_busy_master(candidate_ids, counts)


async def pick_masters_for_slot(
    session: AsyncSession, *, candidate_ids: Sequence[int], day: date, rules: BookingRules, count: int
) -> list[int]:
    """`count` найменш завантажених цього дня майстрів (за рівності — з меншим id)."""
    counts = await _bookings_per_master(session, candidate_ids, day, rules)
    return sorted(candidate_ids, key=lambda m_id: (counts[m_id], m_id))[:count]


async def get_available_dates(
    session: AsyncSession,
    *,
    service: Service,
    master_id: int | None,
    rules: BookingRules,
    now: datetime,
    exclude_booking_id: int | None = None,
    duration_min: int | None = None,
    resources: SlotFilter | None = None,
    staff_count: int = 1,
) -> list[date]:
    """Дати в межах горизонту запису, на які є хоча б один вільний слот.

    `master_id=None` — «будь-який майстер». `exclude_booking_id` — для перенесення.
    """
    today = now.astimezone(rules.tz).date()
    dates: list[date] = []
    for offset in range(rules.horizon_days):
        day = today + timedelta(days=offset)
        if master_id is None:
            has_slots = bool(
                await get_free_slots_any_master(
                    session, service=service, day=day, rules=rules, now=now,
                    duration_min=duration_min, resources=resources, staff_count=staff_count,
                )
            )
        else:
            has_slots = bool(
                await get_free_slots(
                    session,
                    master_id=master_id,
                    service=service,
                    day=day,
                    rules=rules,
                    now=now,
                    exclude_booking_id=exclude_booking_id,
                    duration_min=duration_min,
                    resources=resources,
                    staff_count=staff_count,
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
    duration_min: int | None = None,
    resources: SlotFilter | None = None,
    staff_count: int = 1,
    partner_id: int | None = None,
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
        duration_min=duration_min,
        resources=resources,
        staff_count=staff_count,
        partner_id=partner_id,
    )
    return start.astimezone(UTC) in slots
