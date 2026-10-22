"""Створення записів і робота з клієнтами."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.config import BookingRules, Reminders
from bot.db.models import Booking, BookingStatus, Client, Master, Service
from bot.services import slots


class SlotUnavailableError(Exception):
    """Обраний час уже зайнятий або недоступний."""


class BookingNotFoundError(Exception):
    """Запис не знайдено, він чужий, уже скасований або минув."""


class ChangeDeadlinePassedError(Exception):
    """До візиту лишилося менше, ніж дедлайн скасування/перенесення."""


async def get_client(session: AsyncSession, telegram_id: int) -> Client | None:
    return await session.scalar(select(Client).where(Client.telegram_id == telegram_id))


async def get_or_create_client(session: AsyncSession, telegram_id: int, name: str) -> Client:
    client = await get_client(session, telegram_id)
    if client is None:
        client = Client(telegram_id=telegram_id, name=name)
        session.add(client)
        await session.commit()
    return client


async def save_client_phone(
    session: AsyncSession, telegram_id: int, name: str, phone: str
) -> Client:
    """Зберігає вже нормалізований номер (див. bot/services/phone.py)."""
    client = await get_or_create_client(session, telegram_id, name)
    client.phone = phone
    await session.commit()
    return client


@dataclass(frozen=True)
class PlannedReminders:
    before_24h: bool
    before_2h: bool


def planned_reminders(start: datetime, created_at: datetime, reminders: Reminders) -> PlannedReminders:
    """Які нагадування отримає запис.

    Записи, створені менше ніж за 2 год до візиту, нагадувань не отримують;
    нагадування за добу має сенс, лише якщо до візиту більше доби.
    """
    lead = start - created_at
    if lead < timedelta(hours=2):
        return PlannedReminders(before_24h=False, before_2h=False)
    return PlannedReminders(
        before_24h=reminders.before_24h and lead > timedelta(hours=24),
        before_2h=reminders.before_2h,
    )


def is_within_horizon(day: date, rules: BookingRules, now: datetime) -> bool:
    today = now.astimezone(rules.tz).date()
    return today <= day < today + timedelta(days=rules.horizon_days)


def _plan_reminders(booking: Booking, now: datetime, reminders: Reminders) -> None:
    """Нагадування, яких запис не отримає, одразу позначаємо як оброблені.

    Задача нагадувань шукає записи з reminded_* = False, тож надішле рівно те,
    що клієнт побачив в описі після запису.
    """
    plan = planned_reminders(booking.start_dt, now, reminders)
    booking.reminded_24h = not plan.before_24h
    booking.reminded_2h = not plan.before_2h


async def _lock_masters(session: AsyncSession, master_ids: list[int]) -> None:
    """Блокує майстрів до кінця транзакції, щоб паралельні записи не перетнулися.

    PostgreSQL: блокування рядків майстрів. SQLite: перший запис у транзакції
    бере блокування бази на запис, тож інші записи чекають на commit.
    """
    await session.execute(
        update(Master)
        .where(Master.id.in_(sorted(master_ids)))
        .values(id=Master.id)
        .execution_options(synchronize_session=False)
    )


async def create_booking(
    session: AsyncSession,
    *,
    client: Client,
    service: Service,
    master_id: int | None,
    start: datetime,
    rules: BookingRules,
    now: datetime,
    reminders: Reminders = Reminders(),
    duration_min: int | None = None,
    price: int | None = None,
    pet_id: int | None = None,
) -> Booking:
    """Створює запис, повторно перевіряючи слот у межах однієї транзакції.

    `duration_min` і `price` — для тварини конкретного розміру (за замовчуванням з послуги).

    `master_id=None` — «будь-який майстер»: призначаємо вільного майстра
    з найменшою кількістю записів у цей день.
    Кидає SlotUnavailableError, якщо час уже зайнятий.
    """
    day = start.astimezone(rules.tz).date()
    if not is_within_horizon(day, rules, now):
        raise SlotUnavailableError

    # Завершуємо можливу транзакцію з попередніх читань, щоб перевірка
    # йшла вже після блокування
    await session.commit()
    try:
        if master_id is None:
            candidates = [m.id for m in await slots.masters_for_service(session, service.id)]
            if not candidates:
                raise SlotUnavailableError
            await _lock_masters(session, candidates)
            free = await slots.get_free_slots_any_master(
                session, service=service, day=day, rules=rules, now=now, duration_min=duration_min
            )
            if start not in free:
                raise SlotUnavailableError
            master_id = await slots.pick_master_for_slot(
                session, candidate_ids=free[start], day=day, rules=rules
            )
        else:
            await _lock_masters(session, [master_id])
            if not await slots.is_slot_available(
                session, master_id=master_id, service=service, start=start, rules=rules, now=now,
                duration_min=duration_min,
            ):
                raise SlotUnavailableError

        booking = Booking(
            client_id=client.id,
            master_id=master_id,
            service_id=service.id,
            start_dt=start,
            end_dt=start + timedelta(minutes=duration_min or service.duration_min),
            created_at=now,
            price=price if price is not None else service.price,
            pet_id=pet_id,
        )
        _plan_reminders(booking, now, reminders)
        session.add(booking)
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return booking


# --- Мої записи: перегляд, скасування, перенесення -----------------------------


async def get_upcoming_bookings(
    session: AsyncSession, telegram_id: int, now: datetime
) -> list[Booking]:
    """Майбутні підтверджені записи клієнта (з послугою й майстром)."""
    result = await session.scalars(
        select(Booking)
        .join(Client, Client.id == Booking.client_id)
        .where(
            Client.telegram_id == telegram_id,
            Booking.status == BookingStatus.CONFIRMED,
            Booking.start_dt > now,
        )
        .options(selectinload(Booking.service), selectinload(Booking.master), selectinload(Booking.pet))
        .order_by(Booking.start_dt)
    )
    return list(result.all())


def can_client_change(booking: Booking, rules: BookingRules, now: datetime) -> bool:
    """Клієнт може скасувати чи перенести запис, якщо до візиту більше за дедлайн."""
    return booking.start_dt - now > timedelta(hours=rules.cancel_deadline_hours)


async def get_own_booking(
    session: AsyncSession, booking_id: int, telegram_id: int, now: datetime
) -> Booking:
    """Майбутній підтверджений запис цього клієнта, інакше BookingNotFoundError."""
    booking = await session.scalar(
        select(Booking)
        .join(Client, Client.id == Booking.client_id)
        .where(
            Booking.id == booking_id,
            Client.telegram_id == telegram_id,
            Booking.status == BookingStatus.CONFIRMED,
            Booking.start_dt > now,
        )
        .options(selectinload(Booking.service), selectinload(Booking.master), selectinload(Booking.pet))
        .execution_options(populate_existing=True)
    )
    if booking is None:
        raise BookingNotFoundError
    return booking


async def cancel_booking_by_client(
    session: AsyncSession, *, booking_id: int, telegram_id: int, rules: BookingRules, now: datetime
) -> Booking:
    booking = await get_own_booking(session, booking_id, telegram_id, now)
    if not can_client_change(booking, rules, now):
        raise ChangeDeadlinePassedError
    booking.status = BookingStatus.CANCELLED_BY_CLIENT
    await session.commit()
    return booking


async def reschedule_booking(
    session: AsyncSession,
    *,
    booking_id: int,
    telegram_id: int,
    new_start: datetime,
    rules: BookingRules,
    now: datetime,
    reminders: Reminders = Reminders(),
) -> Booking:
    """Переносить запис до того ж майстра на новий час.

    Як і при створенні, слот перевіряється в межах однієї транзакції після
    блокування майстра. Власний запис клієнта слот не займає.
    """
    await session.commit()
    try:
        booking = await get_own_booking(session, booking_id, telegram_id, now)
        if not can_client_change(booking, rules, now):
            raise ChangeDeadlinePassedError
        if not is_within_horizon(new_start.astimezone(rules.tz).date(), rules, now):
            raise SlotUnavailableError

        await _lock_masters(session, [booking.master_id])
        # Після блокування перечитуємо запис: його могли скасувати паралельно
        booking = await get_own_booking(session, booking_id, telegram_id, now)
        # Тривалість — самого запису (вона могла залежати від розміру тварини)
        duration = booking.duration_min
        if not await slots.is_slot_available(
            session,
            master_id=booking.master_id,
            service=booking.service,
            start=new_start,
            rules=rules,
            now=now,
            exclude_booking_id=booking.id,
            duration_min=duration,
        ):
            raise SlotUnavailableError

        booking.start_dt = new_start
        booking.end_dt = new_start + timedelta(minutes=duration)
        # Новий час — нагадування плануються заново, підтвердження клієнта скидається
        _plan_reminders(booking, now, reminders)
        booking.client_confirmed_at = None
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return booking


async def confirm_attendance(
    session: AsyncSession, *, booking_id: int, telegram_id: int, now: datetime
) -> Booking:
    """Клієнт натиснув «Буду» в нагадуванні."""
    booking = await get_own_booking(session, booking_id, telegram_id, now)
    if booking.client_confirmed_at is None:
        booking.client_confirmed_at = now
        await session.commit()
    return booking
