"""Створення записів і робота з клієнтами."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import BookingRules, Reminders
from bot.db.models import Booking, Client, Master, Service
from bot.services import slots


class SlotUnavailableError(Exception):
    """Обраний час уже зайнятий або недоступний."""


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
) -> Booking:
    """Створює запис, повторно перевіряючи слот у межах однієї транзакції.

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
                session, service=service, day=day, rules=rules, now=now
            )
            if start not in free:
                raise SlotUnavailableError
            master_id = await slots.pick_master_for_slot(
                session, candidate_ids=free[start], day=day, rules=rules
            )
        else:
            await _lock_masters(session, [master_id])
            if not await slots.is_slot_available(
                session, master_id=master_id, service=service, start=start, rules=rules, now=now
            ):
                raise SlotUnavailableError

        booking = Booking(
            client_id=client.id,
            master_id=master_id,
            service_id=service.id,
            start_dt=start,
            end_dt=start + timedelta(minutes=service.duration_min),
            created_at=now,
        )
        session.add(booking)
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return booking
