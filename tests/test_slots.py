"""Тести розрахунку слотів (розділ 5 SPEC)."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from bot.config import BookingRules
from bot.db.models import Booking, BookingStatus, Client, Master, Service, TimeOff, WorkingHours
from bot.services.slots import (
    compute_free_slots,
    get_available_dates,
    get_free_slots,
    get_free_slots_any_master,
    is_slot_available,
    pick_master_for_slot,
    subtract_intervals,
)

KYIV = ZoneInfo("Europe/Kyiv")
RULES = BookingRules()  # крок 15 хв, горизонт 14 днів, мінімум 60 хв до запису
MONDAY = date(2026, 10, 19)  # літній час, UTC+3
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)  # «зараз» далеко в минулому


def kyiv(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=KYIV).astimezone(UTC)


def hours(start=10, end=20, break_=(14, 15)) -> WorkingHours:
    return WorkingHours(
        weekday=0,
        start_time=time(start),
        end_time=time(end),
        break_start=time(break_[0]) if break_ else None,
        break_end=time(break_[1]) if break_ else None,
    )


def slots(schedule=None, busy=(), duration=45, now=LONG_AGO, day=MONDAY, rules=RULES):
    return compute_free_slots(
        day=day, schedule=schedule, busy=busy, duration_min=duration, rules=rules, now=now
    )


# --- Чиста логіка -------------------------------------------------------------


def test_day_off_has_no_slots():
    assert slots(schedule=None) == []


def test_full_day_grid_and_utc():
    result = slots(hours(break_=None), duration=60)
    assert result[0] == kyiv(MONDAY, 10) == datetime(2026, 10, 19, 7, 0, tzinfo=UTC)
    assert result[-1] == kyiv(MONDAY, 19)
    assert all(b - a == timedelta(minutes=15) for a, b in zip(result, result[1:]))


def test_break_in_the_middle_of_day():
    result = slots(hours(), duration=45)
    assert kyiv(MONDAY, 13, 15) in result  # закінчується рівно о 14:00
    for h, m in [(13, 30), (13, 45), (14, 0), (14, 30), (14, 45)]:
        assert kyiv(MONDAY, h, m) not in result
    assert kyiv(MONDAY, 15) in result


def test_service_does_not_fit_before_end_of_day():
    result = slots(hours(), duration=75)
    assert result[-1] == kyiv(MONDAY, 18, 45)  # 18:45 + 75 хв = 20:00
    assert kyiv(MONDAY, 19) not in result
    assert kyiv(MONDAY, 12, 45) in result and kyiv(MONDAY, 13) not in result  # перед перервою


def test_overlap_with_existing_booking():
    busy = [(kyiv(MONDAY, 12), kyiv(MONDAY, 12, 45))]
    result = slots(hours(), busy=busy, duration=45)
    assert kyiv(MONDAY, 11, 15) in result  # закінчується рівно на початку запису
    for h, m in [(11, 30), (11, 45), (12, 0), (12, 30)]:
        assert kyiv(MONDAY, h, m) not in result
    assert kyiv(MONDAY, 12, 45) in result  # починається рівно після запису


def test_time_off_for_part_of_day():
    busy = [(kyiv(MONDAY, 10), kyiv(MONDAY, 13))]
    result = slots(hours(), busy=busy, duration=30)
    assert result[0] == kyiv(MONDAY, 13)


def test_time_off_spanning_several_days_blocks_whole_day():
    busy = [(kyiv(MONDAY - timedelta(days=2), 0), kyiv(MONDAY + timedelta(days=3), 0))]
    assert slots(hours(), busy=busy) == []


def test_min_lead_time():
    now = kyiv(MONDAY, 12, 10)  # найраніше можна о 13:10
    result = slots(hours(), duration=30, now=now)
    assert result[0] == kyiv(MONDAY, 13, 15)


def test_min_lead_time_boundary_is_inclusive():
    now = kyiv(MONDAY, 12)
    result = slots(hours(), duration=30, now=now)
    assert result[0] == kyiv(MONDAY, 13)


def test_today_in_the_past_has_no_slots():
    assert slots(hours(), now=kyiv(MONDAY, 21)) == []


def test_kyiv_date_differs_from_utc_date_after_midnight():
    # Робота з 00:00 до 02:00 за Києвом — це ще неділя за UTC
    result = slots(hours(0, 2, break_=None), duration=30)
    assert result[0] == datetime(2026, 10, 18, 21, 0, tzinfo=UTC)
    assert result[-1] == datetime(2026, 10, 18, 22, 30, tzinfo=UTC)


def test_after_switch_to_winter_time():
    # 26.10.2026 — перший понеділок після переходу на зимовий час (UTC+2)
    day = date(2026, 10, 26)
    result = slots(hours(), day=day)
    assert result[0] == datetime(2026, 10, 26, 8, 0, tzinfo=UTC)


def test_subtract_intervals():
    t = lambda h: datetime(2026, 1, 1, h, tzinfo=UTC)  # noqa: E731
    assert subtract_intervals((t(10), t(20)), [(t(12), t(13)), (t(9), t(11)), (t(19), t(21))]) == [
        (t(11), t(12)),
        (t(13), t(19)),
    ]


# --- З базою даних ------------------------------------------------------------


def _week_schedule(weekdays=range(6), start=10, end=20) -> list[WorkingHours]:
    return [
        WorkingHours(
            weekday=wd,
            start_time=time(start),
            end_time=time(end),
            break_start=time(14),
            break_end=time(15),
        )
        for wd in weekdays
    ]


@pytest.fixture
async def shop(session):
    """Послуга «стрижка» (45 хв); майстри A і B її роблять, C — ні."""
    haircut = Service(name="Стрижка", duration_min=45, price=500)
    beard = Service(name="Борода", duration_min=30, price=300)
    a = Master(name="A", services=[haircut, beard], working_hours=_week_schedule())
    b = Master(name="B", services=[haircut], working_hours=_week_schedule())
    c = Master(name="C", services=[beard], working_hours=_week_schedule())
    client = Client(telegram_id=1, name="Клієнт")
    session.add_all([haircut, beard, a, b, c, client])
    await session.commit()
    return {"haircut": haircut, "beard": beard, "a": a, "b": b, "c": c, "client": client}


def _booking(shop, master, start, status=BookingStatus.CONFIRMED, service="haircut"):
    svc = shop[service]
    return Booking(
        client=shop["client"],
        master=master,
        service=svc,
        start_dt=start,
        end_dt=start + timedelta(minutes=svc.duration_min),
        status=status,
    )


async def test_db_booking_blocks_and_cancelled_does_not(session, shop):
    session.add_all(
        [
            _booking(shop, shop["a"], kyiv(MONDAY, 10)),
            _booking(shop, shop["a"], kyiv(MONDAY, 11), BookingStatus.CANCELLED_BY_CLIENT),
        ]
    )
    await session.commit()

    result = await get_free_slots(
        session, master_id=shop["a"].id, service=shop["haircut"], day=MONDAY, rules=RULES, now=LONG_AGO
    )
    assert kyiv(MONDAY, 10) not in result
    assert kyiv(MONDAY, 11) in result


async def test_db_time_off_for_part_of_day(session, shop):
    session.add(TimeOff(master=shop["a"], start_dt=kyiv(MONDAY, 10), end_dt=kyiv(MONDAY, 13)))
    await session.commit()

    result = await get_free_slots(
        session, master_id=shop["a"].id, service=shop["haircut"], day=MONDAY, rules=RULES, now=LONG_AGO
    )
    assert result[0] == kyiv(MONDAY, 13)


async def test_db_sunday_is_day_off(session, shop):
    sunday = MONDAY - timedelta(days=1)
    result = await get_free_slots(
        session, master_id=shop["a"].id, service=shop["haircut"], day=sunday, rules=RULES, now=LONG_AGO
    )
    assert result == []


async def test_db_master_without_service_or_inactive_has_no_slots(session, shop):
    common = dict(service=shop["haircut"], day=MONDAY, rules=RULES, now=LONG_AGO)
    assert await get_free_slots(session, master_id=shop["c"].id, **common) == []

    shop["a"].is_active = False
    await session.commit()
    assert await get_free_slots(session, master_id=shop["a"].id, **common) == []

    shop["haircut"].is_active = False
    await session.commit()
    assert await get_free_slots(session, master_id=shop["b"].id, **common) == []


async def test_db_any_master_union(session, shop):
    session.add(_booking(shop, shop["a"], kyiv(MONDAY, 10)))
    session.add(TimeOff(master=shop["b"], start_dt=kyiv(MONDAY, 15), end_dt=kyiv(MONDAY, 20)))
    await session.commit()

    result = await get_free_slots_any_master(
        session, service=shop["haircut"], day=MONDAY, rules=RULES, now=LONG_AGO
    )
    a, b = shop["a"].id, shop["b"].id
    assert result[kyiv(MONDAY, 10)] == [b]  # A зайнятий
    assert result[kyiv(MONDAY, 16)] == [a]  # B у TimeOff
    assert result[kyiv(MONDAY, 12)] == [a, b]
    assert shop["c"].id not in {m for ids in result.values() for m in ids}  # C не робить послугу
    assert list(result) == sorted(result)


async def test_db_any_master_picks_least_busy(session, shop):
    a, b = shop["a"].id, shop["b"].id
    assert await pick_master_for_slot(session, candidate_ids=[a, b], day=MONDAY, rules=RULES) == a

    session.add_all(
        [
            _booking(shop, shop["a"], kyiv(MONDAY, 10)),
            _booking(shop, shop["a"], kyiv(MONDAY, 11), service="beard"),
            _booking(shop, shop["b"], kyiv(MONDAY, 10)),
            # Інший день і скасований запис не враховуються
            _booking(shop, shop["b"], kyiv(MONDAY + timedelta(days=1), 10)),
            _booking(shop, shop["b"], kyiv(MONDAY, 16), BookingStatus.CANCELLED_BY_ADMIN),
        ]
    )
    await session.commit()
    assert await pick_master_for_slot(session, candidate_ids=[a, b], day=MONDAY, rules=RULES) == b


async def test_db_midnight_in_kyiv(session):
    """Зміна дати через північ: день тижня й межі доби — за Києвом, не за UTC."""
    svc = Service(name="Нічна стрижка", duration_min=30, price=500)
    night = Master(
        name="Нічний",
        services=[svc],
        working_hours=[  # лише понеділок 00:00–02:00 за Києвом
            WorkingHours(weekday=0, start_time=time(0), end_time=time(2))
        ],
    )
    client = Client(telegram_id=2, name="Клієнт")
    # Понеділок 00:00 за Києвом = неділя 21:00 UTC
    booked = datetime(2026, 10, 18, 21, 0, tzinfo=UTC)
    session.add(
        Booking(client=client, master=night, service=svc, start_dt=booked, end_dt=booked + timedelta(minutes=30))
    )
    await session.commit()
    now = datetime(2026, 10, 18, 19, 0, tzinfo=UTC)  # неділя 22:00 за Києвом

    monday = await get_free_slots(
        session, master_id=night.id, service=svc, day=MONDAY, rules=RULES, now=now
    )
    assert monday[0] == datetime(2026, 10, 18, 21, 30, tzinfo=UTC)
    assert monday[-1] == datetime(2026, 10, 18, 22, 30, tzinfo=UTC)

    sunday = await get_free_slots(
        session, master_id=night.id, service=svc, day=date(2026, 10, 18), rules=RULES, now=now
    )
    assert sunday == []

    # «Сьогодні» для доступних дат — теж за Києвом: о 22:30 UTC неділі в Києві вже понеділок
    dates = await get_available_dates(
        session, service=svc, master_id=night.id, rules=RULES,
        now=datetime(2026, 10, 18, 22, 0, tzinfo=UTC),
    )
    assert dates[0] == date(2026, 10, 26)  # сьогоднішні слоти вже минули, наступний понеділок


async def test_db_available_dates(session, shop):
    # Вівторок повністю заблокований
    tuesday = MONDAY + timedelta(days=1)
    session.add(TimeOff(master=shop["a"], start_dt=kyiv(tuesday, 0), end_dt=kyiv(tuesday + timedelta(days=1), 0)))
    await session.commit()
    now = kyiv(MONDAY, 8)

    dates = await get_available_dates(
        session, service=shop["haircut"], master_id=shop["a"].id, rules=RULES, now=now
    )
    assert len(dates) == 14 - 2 - 1  # 14 днів, мінус 2 неділі, мінус вівторок
    assert tuesday not in dates
    assert all(d.weekday() != 6 for d in dates)
    assert dates[0] == MONDAY and dates[-1] <= MONDAY + timedelta(days=13)

    any_dates = await get_available_dates(
        session, service=shop["haircut"], master_id=None, rules=RULES, now=now
    )
    assert tuesday in any_dates  # майстер B у вівторок вільний


async def test_db_is_slot_available(session, shop):
    common = dict(master_id=shop["a"].id, service=shop["haircut"], rules=RULES, now=LONG_AGO)
    assert await is_slot_available(session, start=kyiv(MONDAY, 10), **common)
    assert not await is_slot_available(session, start=kyiv(MONDAY, 10, 5), **common)  # не по сітці
    assert not await is_slot_available(session, start=kyiv(MONDAY, 14), **common)  # перерва

    booking = _booking(shop, shop["a"], kyiv(MONDAY, 10))
    session.add(booking)
    await session.commit()
    assert not await is_slot_available(session, start=kyiv(MONDAY, 10), **common)
    assert not await is_slot_available(session, start=kyiv(MONDAY, 10, 30), **common)
    # Під час перенесення власний запис клієнта слот не займає
    assert await is_slot_available(
        session, start=kyiv(MONDAY, 10, 30), exclude_booking_id=booking.id, **common
    )
