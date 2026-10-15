"""Тести створення записів (bot/services/booking.py)."""

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from bot.config import BookingRules, Reminders
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

    client = await svc.save_client_phone(session, 5, "Олег", "+380501234567")
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


# --- Мої записи ---------------------------------------------------------------


async def _book(session, shop, start, master="a", telegram_id=111):
    client = await svc.get_or_create_client(session, telegram_id, "Клієнт")
    return await svc.create_booking(
        session, client=client, service=shop["haircut"], master_id=shop[master].id,
        start=start, rules=RULES, now=NOW,
    )


async def test_upcoming_bookings_only_own_future_confirmed(session, shop):
    later = await _book(session, shop, kyiv(MONDAY, 16))
    sooner = await _book(session, shop, kyiv(MONDAY, 10))
    cancelled = await _book(session, shop, kyiv(MONDAY, 12))
    await svc.cancel_booking_by_client(
        session, booking_id=cancelled.id, telegram_id=111, rules=RULES, now=NOW
    )
    await _book(session, shop, kyiv(MONDAY, 11), telegram_id=222)  # чужий

    upcoming = await svc.get_upcoming_bookings(session, 111, NOW)
    assert [b.id for b in upcoming] == [sooner.id, later.id]
    assert upcoming[0].service.name == "Стрижка" and upcoming[0].master.name == "A"

    # Запис, що вже почався, не показується
    assert [b.id for b in await svc.get_upcoming_bookings(session, 111, kyiv(MONDAY, 10))] == [later.id]


@pytest.mark.parametrize(
    ("before_visit", "expected"),
    [
        (timedelta(hours=2, minutes=1), True),
        (timedelta(hours=2), False),  # рівно дедлайн — уже не можна
        (timedelta(minutes=30), False),
    ],
)
async def test_can_client_change(session, shop, before_visit, expected):
    booking = await _book(session, shop, kyiv(MONDAY, 12))
    assert svc.can_client_change(booking, RULES, booking.start_dt - before_visit) is expected


async def test_cancel_by_client(session, shop):
    booking = await _book(session, shop, kyiv(MONDAY, 12))
    cancelled = await svc.cancel_booking_by_client(
        session, booking_id=booking.id, telegram_id=111, rules=RULES, now=NOW
    )
    assert cancelled.status is BookingStatus.CANCELLED_BY_CLIENT
    # Слот знову вільний
    assert await svc.slots.is_slot_available(
        session, master_id=shop["a"].id, service=shop["haircut"], start=kyiv(MONDAY, 12),
        rules=RULES, now=NOW,
    )
    with pytest.raises(svc.BookingNotFoundError):  # повторне скасування
        await svc.cancel_booking_by_client(
            session, booking_id=booking.id, telegram_id=111, rules=RULES, now=NOW
        )


async def test_cancel_rejected_for_other_client_and_after_deadline(session, shop):
    booking = await _book(session, shop, kyiv(MONDAY, 12))
    with pytest.raises(svc.BookingNotFoundError):
        await svc.cancel_booking_by_client(
            session, booking_id=booking.id, telegram_id=999, rules=RULES, now=NOW
        )
    with pytest.raises(svc.ChangeDeadlinePassedError):
        await svc.cancel_booking_by_client(
            session, booking_id=booking.id, telegram_id=111, rules=RULES,
            now=kyiv(MONDAY, 10, 30),
        )
    assert (await session.get(Booking, booking.id)).status is BookingStatus.CONFIRMED


async def test_reschedule(session, shop):
    booking = await _book(session, shop, kyiv(MONDAY, 12))
    booking.reminded_24h = True
    await session.commit()

    # Зсув на 15 хв перетинається зі старим часом — власний запис не заважає
    moved = await svc.reschedule_booking(
        session, booking_id=booking.id, telegram_id=111, new_start=kyiv(MONDAY, 12, 15),
        rules=RULES, now=NOW,
    )
    assert moved.id == booking.id and moved.master_id == shop["a"].id
    assert moved.start_dt == kyiv(MONDAY, 12, 15)
    assert moved.end_dt == kyiv(MONDAY, 13)
    assert moved.reminded_24h is False and moved.reminded_2h is False  # до візиту > доби
    assert await _count_bookings(session) == 1


async def test_reschedule_rejections(session, shop):
    booking = await _book(session, shop, kyiv(MONDAY, 12))
    booking_id = booking.id  # після rollback об'єкти сесії застарівають
    await _book(session, shop, kyiv(MONDAY, 16), telegram_id=222)
    common = dict(booking_id=booking_id, telegram_id=111, rules=RULES)

    with pytest.raises(SlotUnavailableError):  # зайнято іншим клієнтом
        await svc.reschedule_booking(session, new_start=kyiv(MONDAY, 16, 30), now=NOW, **common)
    with pytest.raises(SlotUnavailableError):  # перерва
        await svc.reschedule_booking(session, new_start=kyiv(MONDAY, 14), now=NOW, **common)
    with pytest.raises(svc.ChangeDeadlinePassedError):
        await svc.reschedule_booking(
            session, new_start=kyiv(MONDAY, 17), now=kyiv(MONDAY, 11), **common
        )
    with pytest.raises(svc.BookingNotFoundError):
        await svc.reschedule_booking(
            session, booking_id=booking_id, telegram_id=999, new_start=kyiv(MONDAY, 17),
            rules=RULES, now=NOW,
        )
    assert (await session.get(Booking, booking_id)).start_dt == kyiv(MONDAY, 12)


async def test_available_dates_for_reschedule_ignore_own_booking(session, shop):
    # Майстер A у понеділок зайнятий для 4-годинної послуги: 10–14 (наш запис) і 15–19
    long_service = Service(name="Довга", duration_min=240, price=1, masters=[shop["a"]])
    session.add(long_service)
    await session.commit()
    own = await svc.get_or_create_client(session, 111, "Клієнт")
    other = await svc.get_or_create_client(session, 222, "Інший")
    booking = await svc.create_booking(
        session, client=own, service=long_service, master_id=shop["a"].id,
        start=kyiv(MONDAY, 10), rules=RULES, now=NOW,
    )
    await svc.create_booking(
        session, client=other, service=long_service, master_id=shop["a"].id,
        start=kyiv(MONDAY, 15), rules=RULES, now=NOW,
    )
    common = dict(service=long_service, master_id=shop["a"].id, rules=RULES, now=NOW)
    assert MONDAY not in await svc.slots.get_available_dates(session, **common)
    assert MONDAY in await svc.slots.get_available_dates(
        session, exclude_booking_id=booking.id, **common
    )


@pytest.mark.parametrize(
    ("lead", "flags"),
    [
        (timedelta(days=2), (False, False)),  # отримає обидва нагадування
        (timedelta(hours=5), (True, False)),  # лише за 2 год
        (timedelta(hours=1, minutes=30), (True, True)),  # жодного
    ],
)
async def test_reminder_flags_planned_on_create(session, shop, lead, flags):
    start = kyiv(MONDAY, 12)
    booking = await svc.create_booking(
        session, client=shop["client"], service=shop["haircut"], master_id=shop["a"].id,
        start=start, rules=RULES, now=start - lead,
    )
    assert (booking.reminded_24h, booking.reminded_2h) == flags


@pytest.mark.parametrize(
    ("lead", "flags"),
    [
        (timedelta(days=2), (False, False)),
        (timedelta(hours=5), (True, False)),
    ],
)
async def test_reminder_flags_replanned_on_reschedule(session, shop, lead, flags):
    booking = await _book(session, shop, kyiv(MONDAY + timedelta(days=1), 12))
    new_start = kyiv(MONDAY, 12)
    moved = await svc.reschedule_booking(
        session, booking_id=booking.id, telegram_id=111, new_start=new_start,
        rules=RULES, now=new_start - lead,
    )
    assert (moved.reminded_24h, moved.reminded_2h) == flags


async def test_reminders_disabled_in_config(session, shop):
    client = await svc.get_or_create_client(session, 111, "Клієнт")
    booking = await svc.create_booking(
        session, client=client, service=shop["haircut"], master_id=shop["a"].id,
        start=kyiv(MONDAY, 12), rules=RULES, now=NOW,
        reminders=Reminders(before_24h=False, before_2h=False),
    )
    assert booking.reminded_24h and booking.reminded_2h


async def test_concurrent_reschedule_and_create_do_not_double_book(engine, shop, session):
    """Перенесення й новий запис на той самий слот одночасно: успішний лише один."""
    moving = await _book(session, shop, kyiv(MONDAY, 16))
    target = kyiv(MONDAY, 10)
    factory = create_session_factory(engine)

    async def reschedule():
        async with factory() as s:
            assert await svc.slots.is_slot_available(
                s, master_id=shop["a"].id, service=shop["haircut"], start=target,
                rules=RULES, now=NOW,
            )
            await asyncio.sleep(0.05)
            return await svc.reschedule_booking(
                s, booking_id=moving.id, telegram_id=111, new_start=target, rules=RULES, now=NOW
            )

    async def create():
        async with factory() as s:
            client = await svc.get_or_create_client(s, 333, "Інший")
            service = await s.get(Service, shop["haircut"].id)
            assert await svc.slots.is_slot_available(
                s, master_id=shop["a"].id, service=service, start=target, rules=RULES, now=NOW
            )
            await asyncio.sleep(0.05)
            return await svc.create_booking(
                s, client=client, service=service, master_id=shop["a"].id,
                start=target, rules=RULES, now=NOW,
            )

    results = await asyncio.gather(reschedule(), create(), return_exceptions=True)
    assert sum(isinstance(r, Booking) for r in results) == 1, results
    assert sum(isinstance(r, SlotUnavailableError) for r in results) == 1, results
