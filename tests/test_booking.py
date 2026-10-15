"""Тести створення записів (bot/services/booking.py)."""

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from bot.config import BookingRules
from bot.db.models import Booking, BookingStatus, Master, Service, WorkingHours
from bot.db.session import create_session_factory
from bot.services import booking as svc
from bot.services.booking import SlotUnavailableError

KYIV = ZoneInfo("Europe/Kyiv")
RULES = BookingRules()
MONDAY = date(2026, 10, 19)
NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)  # неділя, напередодні


def kyiv(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=KYIV).astimezone(UTC)


def _schedule():
    return [
        WorkingHours(weekday=wd, start_time=time(10), end_time=time(20),
                     break_start=time(14), break_end=time(15))
        for wd in range(6)
    ]


@pytest.fixture
async def shop(session):
    haircut = Service(name="Стрижка", duration_min=45, price=500)
    a = Master(name="A", services=[haircut], working_hours=_schedule())
    b = Master(name="B", services=[haircut], working_hours=_schedule())
    session.add_all([haircut, a, b])
    await session.commit()
    client = await svc.get_or_create_client(session, 111, "Іван")
    return {"haircut": haircut, "a": a, "b": b, "client": client}


async def _count_bookings(session) -> int:
    return await session.scalar(select(func.count()).select_from(Booking))


async def test_client_created_once_and_phone_saved(session):
    first = await svc.get_or_create_client(session, 5, "Олег")
    second = await svc.get_or_create_client(session, 5, "Інше ім'я")
    assert first.id == second.id and second.name == "Олег"

    client = await svc.save_client_phone(session, 5, "Олег", "380501234567")
    assert client.phone == "+380501234567"
    assert (await svc.get_client(session, 5)).phone == "+380501234567"


async def test_create_booking_with_master(session, shop):
    booking = await svc.create_booking(
        session, client=shop["client"], service=shop["haircut"], master_id=shop["b"].id,
        start=kyiv(MONDAY, 10), rules=RULES, now=NOW,
    )
    assert booking.master_id == shop["b"].id
    assert booking.end_dt - booking.start_dt == timedelta(minutes=45)
    assert booking.status is BookingStatus.CONFIRMED
    assert booking.created_at == NOW


async def test_taken_slot_rejected(session, shop):
    common = dict(client=shop["client"], service=shop["haircut"], master_id=shop["a"].id,
                  rules=RULES, now=NOW)
    await svc.create_booking(session, start=kyiv(MONDAY, 10), **common)
    with pytest.raises(SlotUnavailableError):
        await svc.create_booking(session, start=kyiv(MONDAY, 10, 30), **common)  # перетин
    assert await _count_bookings(session) == 1


@pytest.mark.parametrize(
    "start",
    [
        kyiv(MONDAY, 14),  # перерва
        kyiv(MONDAY, 10, 5),  # не по сітці
        kyiv(MONDAY - timedelta(days=1), 12),  # неділя
        kyiv(MONDAY + timedelta(days=RULES.horizon_days + 1), 12),  # поза горизонтом
        NOW + timedelta(minutes=30),  # менше мінімального часу до запису
    ],
)
async def test_invalid_start_rejected(session, shop, start):
    with pytest.raises(SlotUnavailableError):
        await svc.create_booking(
            session, client=shop["client"], service=shop["haircut"], master_id=shop["a"].id,
            start=start, rules=RULES, now=NOW,
        )


async def test_any_master_assigns_least_busy(session, shop):
    common = dict(client=shop["client"], service=shop["haircut"], rules=RULES, now=NOW)
    await svc.create_booking(session, master_id=shop["a"].id, start=kyiv(MONDAY, 10), **common)

    # A вже має запис цього дня — призначається B
    booking = await svc.create_booking(session, master_id=None, start=kyiv(MONDAY, 12), **common)
    assert booking.master_id == shop["b"].id

    # О 10:00 вільний лише B, навіть якщо в нього вже є запис
    await svc.create_booking(session, master_id=None, start=kyiv(MONDAY, 16), **common)
    assert (await svc.create_booking(
        session, master_id=None, start=kyiv(MONDAY, 10), **common
    )).master_id == shop["b"].id

    # Обидва зайняті — слот недоступний
    with pytest.raises(SlotUnavailableError):
        await svc.create_booking(session, master_id=None, start=kyiv(MONDAY, 10), **common)


async def test_concurrent_bookings_do_not_double_book(engine, shop):
    """Дві паралельні спроби зайняти той самий слот: одна успішна, друга — відмова."""
    factory = create_session_factory(engine)

    async def attempt(telegram_id: int, master_id: int | None):
        async with factory() as session:
            client = await svc.get_or_create_client(session, telegram_id, "Клієнт")
            service = await session.get(Service, shop["haircut"].id)
            # Обидві спроби спершу бачать вільний слот — як у реальному житті
            assert await svc.slots.is_slot_available(
                session, master_id=shop["a"].id, service=service, start=kyiv(MONDAY, 10),
                rules=RULES, now=NOW,
            )
            await asyncio.sleep(0.05)
            return await svc.create_booking(
                session, client=client, service=service, master_id=master_id,
                start=kyiv(MONDAY, 10), rules=RULES, now=NOW,
            )

    results = await asyncio.gather(
        attempt(201, shop["a"].id), attempt(202, shop["a"].id), return_exceptions=True
    )
    assert sum(isinstance(r, Booking) for r in results) == 1
    assert sum(isinstance(r, SlotUnavailableError) for r in results) == 1

    async with factory() as session:
        assert await _count_bookings(session) == 1
