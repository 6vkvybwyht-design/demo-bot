"""Спа-комплекс: ресурси (кабінети, термальна зона), гості, ціна, аванс і групові події.

Ядро — чисті функції без БД: перевірка ресурсів (`fits_resources`) і розрахунок ціни
(`quote`). Решта лише завантажує зайнятість із бази для фільтра слотів.
Усі datetime — aware UTC.
"""

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import BookingRules, SpaConfig, SpaGuests, SpaService
from bot.db.models import Booking, BookingResource, BookingStatus, Service
from bot.services.slots import day_bounds_utc, local_to_utc


class GuestsError(ValueError):
    """Кількість гостей поза лімітами. Текст — для користувача."""


# --- Ресурси ------------------------------------------------------------------


@dataclass(frozen=True)
class Use:
    """Ресурс у плані бронювання: хвилини від початку бронювання."""

    resource: str
    units: int
    start_min: int
    end_min: int


@dataclass(frozen=True)
class Occupied:
    """Зайнятий ресурс (UTC), без підготовки після бронювання."""

    resource: str
    units: int
    start: datetime
    end: datetime


def plan_uses(
    spa: SpaConfig, service_name: str, duration_min: int, addon_names: Iterable[str] = ()
) -> list[Use]:
    """Які ресурси й коли займе бронювання тривалістю `duration_min`."""
    plan: list[Use] = []
    for name in [service_name, *addon_names]:
        cfg = spa.service(name)
        for use in cfg.uses if cfg else []:
            if use.last_min is not None:
                start, end = max(0, duration_min - use.last_min), duration_min
            elif use.after_min is not None:
                start, end = duration_min, duration_min + use.after_min
            else:
                start, end = 0, duration_min
            plan.append(Use(use.resource, use.units, start, end))
    return plan


def plan_to_data(plan: Sequence[Use]) -> list[list]:
    """План у вигляді, придатному для стану діалогу (JSON)."""
    return [[u.resource, u.units, u.start_min, u.end_min] for u in plan]


def plan_from_data(data: Sequence[Sequence]) -> list[Use]:
    return [Use(str(r), int(units), int(s), int(e)) for r, units, s, e in data]


def plan_from_booking(booking: Booking) -> list[Use]:
    """План наявного запису (для перенесення): зсуви відносно його початку."""

    def minutes(dt: datetime) -> int:
        return int((dt - booking.start_dt).total_seconds() // 60)

    return [Use(r.resource, r.units, minutes(r.start_dt), minutes(r.end_dt)) for r in booking.resources]


def resource_rows(plan: Sequence[Use], start: datetime) -> list[BookingResource]:
    return [
        BookingResource(
            resource=u.resource, units=u.units,
            start_dt=start + timedelta(minutes=u.start_min),
            end_dt=start + timedelta(minutes=u.end_min),
        )
        for u in plan
    ]


def _within_opening_hours(spa: SpaConfig, start: datetime, end: datetime, rules: BookingRules) -> bool:
    day = start.astimezone(rules.tz).date()
    hours = spa.hours_for(day.weekday())
    if hours is None:
        return False
    return local_to_utc(day, hours[0], rules.tz) <= start and end <= local_to_utc(day, hours[1], rules.tz)


def _peak(occupied: Sequence[Occupied], start: datetime, end: datetime, cleanup: timedelta) -> int:
    """Найбільша кількість зайнятих одиниць у проміжку [start, end), з підготовкою."""
    overlapping = [o for o in occupied if o.start < end and start < o.end + cleanup]
    points = [start] + [o.start for o in overlapping if start <= o.start < end]
    return max(
        sum(o.units for o in overlapping if o.start <= point < o.end + cleanup) for point in points
    )


def fits_resources(
    plan: Sequence[Use],
    start: datetime,
    occupied: Iterable[Occupied],
    spa: SpaConfig,
    rules: BookingRules,
) -> bool:
    """Чи вистачає ресурсів на бронювання з початком `start`.

    Ресурс займається лише в години роботи комплексу. Після кожного бронювання
    ресурсу — підготовка (`cleanup_min`): нове бронювання не може почалися раніше,
    ніж вона закінчиться, і має закінчитися з запасом на неї перед наступним.
    """
    taken = list(occupied)
    for use in plan:
        resource = spa.resources[use.resource]
        cleanup = timedelta(minutes=resource.cleanup_min)
        use_start = start + timedelta(minutes=use.start_min)
        use_end = start + timedelta(minutes=use.end_min)
        if not _within_opening_hours(spa, use_start, use_end, rules):
            return False
        same = [o for o in taken if o.resource == use.resource]
        if _peak(same, use_start, use_end + cleanup, cleanup) + use.units > resource.capacity:
            return False
        # Власні ресурси плану теж займають місце (обидва кабінети + хамам після)
        taken.append(Occupied(use.resource, use.units, use_start, use_end))
    return True


def event_blocks(spa: SpaConfig, day: date, rules: BookingRules) -> list[Occupied]:
    """Ресурси, повністю зайняті груповими подіями цього дня (термальна зона на банний вечір)."""
    blocks = []
    for cfg in spa.services.values():
        event = cfg.event
        if event is None or day.weekday() not in event.weekdays:
            continue
        start, end = local_to_utc(day, event.start, rules.tz), local_to_utc(day, event.end, rules.tz)
        blocks += [Occupied(r, spa.resources[r].capacity, start, end) for r in event.blocks]
    return blocks


async def load_occupied(
    session: AsyncSession, start: datetime, end: datetime, exclude_booking_id: int | None = None
) -> list[Occupied]:
    """Ресурси активних записів (зокрема тих, що чекають на аванс), що перетинають проміжок."""
    query = (
        select(BookingResource)
        .join(Booking, Booking.id == BookingResource.booking_id)
        .where(
            Booking.status == BookingStatus.CONFIRMED,
            BookingResource.start_dt < end,
            BookingResource.end_dt > start,
        )
    )
    if exclude_booking_id is not None:
        query = query.where(Booking.id != exclude_booking_id)
    rows = await session.scalars(query)
    return [Occupied(r.resource, r.units, r.start_dt, r.end_dt) for r in rows]


@dataclass(frozen=True)
class ResourceFilter:
    """Фільтр слотів: лишає початки, на які вистачає ресурсів (див. slots.SlotFilter)."""

    spa: SpaConfig
    plan: tuple[Use, ...]

    async def for_day(
        self, session: AsyncSession, day: date, rules: BookingRules, exclude_booking_id: int | None
    ) -> Callable[[datetime], bool]:
        day_start, day_end = day_bounds_utc(day, rules.tz)
        # Із запасом: підготовка після вчорашнього пізнього бронювання тощо
        margin = timedelta(days=1)
        occupied = await load_occupied(session, day_start - margin, day_end + margin, exclude_booking_id)
        occupied += event_blocks(self.spa, day, rules)
        return lambda start: fits_resources(self.plan, start, occupied, self.spa, rules)


def resource_filter(spa: SpaConfig | None, plan: Sequence[Use] | None) -> ResourceFilter | None:
    """Фільтр для плану; None — ресурси не потрібні (не спа або послуга без ресурсів)."""
    if spa is None or not plan:
        return None
    return ResourceFilter(spa, tuple(plan))


# --- Гості --------------------------------------------------------------------


def check_guests(guests: SpaGuests, counts: dict[str, int], max_first: int | None = None) -> None:
    """GuestsError, якщо кількість гостей поза лімітами.

    `max_first` — додатковий ліміт першого виду (вільні місця на подію).
    """
    for i, kind in enumerate(guests.kinds):
        value = counts.get(kind.key, 0)
        limit = min(kind.max, max_first) if i == 0 and max_first is not None else kind.max
        if value < kind.min:
            raise GuestsError(f"{kind.label}: щонайменше {kind.min}")
        if value > limit:
            raise GuestsError(f"{kind.label}: щонайбільше {limit}")
    if unknown := set(counts) - {k.key for k in guests.kinds}:
        raise GuestsError(f"невідомі гості: {', '.join(sorted(unknown))}")
    total = sum(counts.values())
    if guests.max_total is not None and total > guests.max_total:
        raise GuestsError(f"Разом щонайбільше {guests.max_total} осіб")


def guests_details(guests: SpaGuests, counts: dict[str, int]) -> str:
    """«Дорослі: 3, діти 8–13 років: 1» (без нульових)."""
    parts = [f"{k.label}: {counts[k.key]}" for k in guests.kinds if counts.get(k.key)]
    return ", ".join(parts)


# --- Ціна ---------------------------------------------------------------------


@dataclass(frozen=True)
class Quote:
    """Тривалість, ціна, аванс і ресурси бронювання."""

    duration_min: int
    price: int
    price_from: bool = False
    deposit: int | None = None  # None — аванс не потрібен
    guests: int | None = None
    guests_details: str | None = None
    plan: list[Use] = field(default_factory=list)


def deposit_amount(total: int, percent: int) -> int:
    """Аванс у гривнях, з округленням угору."""
    return math.ceil(total * percent / 100)


def event_duration(cfg: SpaService) -> int:
    event = cfg.event
    return (event.end.hour * 60 + event.end.minute) - (event.start.hour * 60 + event.start.minute)


def quote(
    spa: SpaConfig,
    service: Service,
    *,
    variant: int | None = None,
    counts: dict[str, int] | None = None,
    addons: Sequence[Service] = (),
    max_first: int | None = None,
) -> Quote:
    """Розрахунок бронювання. GuestsError — гостей поза лімітами; IndexError — немає варіанта.

    Базова тривалість і ціна: обраний варіант (60/90 хв) → ціна за кількістю гостей
    (tiers) → сама послуга. Далі додаються гості понад включених у ціну й додаткові
    послуги (їхня ціна — з адмінки, подовження — `extra_min` із профілю).
    """
    cfg = spa.service(service.name) or SpaService()
    duration, price = service.duration_min, service.price
    if cfg.variants:
        chosen = cfg.variants[variant or 0]
        duration, price = chosen.duration_min, chosen.price
    if cfg.event is not None:
        duration = event_duration(cfg)

    guests_total = details = None
    if cfg.guests is not None:
        counts = dict(counts) if counts is not None else cfg.guests.defaults()
        check_guests(cfg.guests, counts, max_first)
        if cfg.guests.tiers:
            tier = cfg.guests.tiers[counts[cfg.guests.kinds[0].key]]
            duration, price = tier.duration_min, tier.price
        price += sum(max(0, counts.get(k.key, 0) - k.included) * k.price for k in cfg.guests.kinds)
        guests_total = sum(counts.values())
        details = guests_details(cfg.guests, counts)

    addon_cfgs = [spa.service(a.name) or SpaService() for a in addons]
    duration += sum(a.extra_min for a in addon_cfgs)
    price += sum(a.price for a in addons)
    needs_deposit = cfg.deposit or any(a.deposit for a in addon_cfgs)
    # Події не займають ресурси бронюваннями: їх блокує сама подія (event.blocks)
    plan = [] if cfg.event is not None else plan_uses(spa, service.name, duration, [a.name for a in addons])
    return Quote(
        duration_min=duration,
        price=price,
        price_from=cfg.price_from or any(a.price_from for a in addon_cfgs),
        deposit=deposit_amount(price, spa.deposit.percent) if needs_deposit else None,
        guests=guests_total,
        guests_details=details,
        plan=plan,
    )


# --- Меню послуг --------------------------------------------------------------


def category_services(services: Sequence[Service], spa: SpaConfig, key: str) -> list[Service]:
    return [s for s in services if (cfg := spa.service(s.name)) and cfg.category == key]


def list_price(service: Service, spa: SpaConfig) -> tuple[int, int, bool]:
    """Тривалість, ціна й «від» для кнопки в списку: найдешевший варіант або кількість гостей."""
    cfg = spa.service(service.name) or SpaService()
    if cfg.variants:
        cheapest = min(cfg.variants, key=lambda v: v.price)
        return cheapest.duration_min, cheapest.price, len(cfg.variants) > 1 or cfg.price_from
    if cfg.event is not None or cfg.guests is not None and not cfg.guests.fixed:
        q = quote(spa, service)
        return q.duration_min, q.price, True
    return service.duration_min, service.price, cfg.price_from


def available_addons(service: Service, spa: SpaConfig, services: Sequence[Service]) -> list[Service]:
    """Активні додаткові послуги, доступні для послуги (у порядку з профілю)."""
    cfg = spa.service(service.name)
    by_name = {s.name: s for s in services}
    names = cfg.addons if cfg else []
    return [by_name[name] for name in names if name in by_name]


def toggle_addon(
    selected: Sequence[int], addon: Service, spa: SpaConfig, services: Sequence[Service]
) -> list[int]:
    """Перемикає додаткову послугу. Із групи (наприклад, «додатковий час») — лише одна."""
    chosen = set(selected)
    if addon.id in chosen:
        return sorted(chosen - {addon.id})
    group = (spa.service(addon.name) or SpaService()).group
    if group is not None:
        chosen -= {
            s.id for s in services if (cfg := spa.service(s.name)) and cfg.group == group
        }
    return sorted(chosen | {addon.id})


def is_staffless(spa: SpaConfig | None, service_name: str) -> bool:
    """Послуга без вибору майстра (відпочинок у термальній зоні, групова подія)."""
    cfg = spa.service(service_name) if spa else None
    return cfg is not None and (not cfg.staff or cfg.event is not None)


def is_event(spa: SpaConfig | None, service_name: str) -> bool:
    cfg = spa.service(service_name) if spa else None
    return cfg is not None and cfg.event is not None


# --- Групові події ------------------------------------------------------------


def event_starts(cfg: SpaService, rules: BookingRules, now: datetime) -> list[datetime]:
    """Початки події в межах горизонту запису, на які ще можна записатися."""
    event = cfg.event
    today = now.astimezone(rules.tz).date()
    earliest = now + timedelta(minutes=rules.min_lead_time_min)
    starts = []
    for offset in range(rules.horizon_days):
        day = today + timedelta(days=offset)
        start = local_to_utc(day, event.start, rules.tz)
        if day.weekday() in event.weekdays and start >= earliest:
            starts.append(start)
    return starts


async def seats_taken(session: AsyncSession, service_id: int, start: datetime) -> int:
    """Скільки місць на подію вже зайнято (разом із тими, що чекають на аванс)."""
    taken = await session.scalar(
        select(func.coalesce(func.sum(func.coalesce(Booking.guests, 1)), 0)).where(
            Booking.service_id == service_id,
            Booking.start_dt == start.astimezone(UTC),
            Booking.status == BookingStatus.CONFIRMED,
        )
    )
    return int(taken or 0)


async def event_dates(
    session: AsyncSession, service: Service, cfg: SpaService, rules: BookingRules, now: datetime
) -> list[tuple[datetime, int]]:
    """(початок, вільні місця) для найближчих дат події з вільними місцями."""
    result = []
    for start in event_starts(cfg, rules, now):
        free = cfg.event.capacity - await seats_taken(session, service.id, start)
        if free > 0:
            result.append((start, free))
    return result
