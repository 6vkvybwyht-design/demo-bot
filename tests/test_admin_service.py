"""Тести логіки адмінки (bot/services/admin.py)."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from bot.config import BookingRules
from bot.db.models import Booking, BookingStatus, Client, Master, Service, WorkingHours
from bot.services import admin
from bot.services.admin import Hours, InputError
from bot.services.booking import BookingNotFoundError

KYIV = ZoneInfo("Europe/Kyiv")
RULES = BookingRules()
MONDAY = date(2026, 10, 19)
NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)


def kyiv(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=KYIV).astimezone(UTC)


# --- Розбір тексту ------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10:00-20:00", Hours(time(10), time(20))),
        ("9:30 – 18:00", Hours(time(9, 30), time(18))),
        ("10:00-20:00 14:00-15:00", Hours(time(10), time(20), time(14), time(15))),
        ("Вихідний", None),
        ("-", None),
    ],
)
def test_parse_hours(text, expected):
    assert admin.parse_hours(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "10-20", "20:00-10:00", "10:00-20:00 9:00-10:00", "10:00-20:00 14:00-15:00 16:00-17:00",
     "25:00-26:00", "10:00-20:00 обід"],
)
def test_parse_hours_invalid(text):
    with pytest.raises(InputError):
        admin.parse_hours(text)


TODAY = date(2026, 10, 15)


def test_parse_period():
    assert admin.parse_period("20.10 10:00-13:00", TODAY, KYIV) == (
        kyiv(date(2026, 10, 20), 10), kyiv(date(2026, 10, 20), 13)
    )
    start, end = admin.parse_period("20.10", TODAY, KYIV)
    assert start == datetime(2026, 10, 19, 21, tzinfo=UTC) and end - start == timedelta(hours=24)
    start, end = admin.parse_period("20.10-25.10", TODAY, KYIV)
    assert start.astimezone(KYIV).date() == date(2026, 10, 20)
    assert end.astimezone(KYIV) == datetime(2026, 10, 26, 0, tzinfo=KYIV)


def test_parse_period_year_rollover():
    start, end = admin.parse_period("10.01", TODAY, KYIV)  # січень — наступного року
    assert start.astimezone(KYIV).date() == date(2027, 1, 10)
    start, end = admin.parse_period("28.12-03.01", TODAY, KYIV)
    assert end.astimezone(KYIV).date() == date(2027, 1, 4)


@pytest.mark.parametrize(
    "text", ["", "завтра", "32.10", "20.13", "20.10 13:00-10:00", "25.10-20.10", "20.10 10-13"]
)
def test_parse_period_invalid(text):
    with pytest.raises(InputError):
        admin.parse_period(text, TODAY, KYIV)


def test_parse_simple_values():
    assert admin.parse_int(" 45 ", 5, 600) == 45
    for bad in ("0", "abc", "-5", "601", "4.5"):
        with pytest.raises(InputError):
            admin.parse_int(bad, 5, 600)
    assert admin.parse_name("  Нова   послуга ") == "Нова послуга"
    with pytest.raises(InputError):
        admin.parse_name("   ")
    assert admin.parse_telegram_id("123456") == 123456
    assert admin.parse_telegram_id("-") is None
    with pytest.raises(InputError):
        admin.parse_telegram_id("@username")


# --- З базою даних ------------------------------------------------------------


@pytest.fixture
async def shop(session):
    haircut = Service(name="Стрижка", duration_min=45, price=500)
    beard = Service(name="Борода", duration_min=30, price=300)
    a = Master(name="Андрій", services=[haircut, beard], working_hours=[
        WorkingHours(weekday=wd, start_time=time(10), end_time=time(20),
                     break_start=time(14), break_end=time(15)) for wd in range(6)
    ])
    b = Master(name="Богдан", services=[haircut])
    client = Client(telegram_id=1, name="Іван", phone="+380501234567")
    session.add_all([haircut, beard, a, b, client])
    await session.commit()
    return {"haircut": haircut, "beard": beard, "a": a, "b": b, "client": client}


def _booking(shop, start, master="a", service="haircut", status=BookingStatus.CONFIRMED):
    svc = shop[service]
    return Booking(client=shop["client"], master=shop[master], service=svc, start_dt=start,
                   end_dt=start + timedelta(minutes=svc.duration_min), status=status)


async def test_bookings_for_day_and_week(session, shop):
    session.add_all([
        _booking(shop, kyiv(MONDAY, 12), master="b"),
        _booking(shop, kyiv(MONDAY, 10)),
        _booking(shop, kyiv(MONDAY, 11), status=BookingStatus.CANCELLED_BY_CLIENT),
        _booking(shop, kyiv(MONDAY, 16), status=BookingStatus.COMPLETED),
        _booking(shop, kyiv(MONDAY + timedelta(days=1), 0, 30)),  # після опівночі — вже вівторок
    ])
    await session.commit()

    day = await admin.bookings_for_day(session, MONDAY, KYIV)
    assert [(b.master.name, b.start_dt) for b in day] == [
        ("Андрій", kyiv(MONDAY, 10)), ("Андрій", kyiv(MONDAY, 16)), ("Богдан", kyiv(MONDAY, 12)),
    ]
    assert day[0].client.name == "Іван" and day[0].service.name == "Стрижка"

    week = await admin.week_counts(session, MONDAY, KYIV)
    assert week[0] == (MONDAY, 3) and week[1] == (MONDAY + timedelta(days=1), 1)
    assert len(week) == 7 and sum(c for _, c in week) == 4


async def test_cancel_and_mark(session, shop):
    future = _booking(shop, kyiv(MONDAY, 12))
    started = _booking(shop, kyiv(MONDAY, 10))
    session.add_all([future, started])
    await session.commit()
    now = kyiv(MONDAY, 10, 30)

    with pytest.raises(BookingNotFoundError):  # уже почався — скасувати не можна
        await admin.cancel_booking_by_admin(session, started.id, now)
    with pytest.raises(BookingNotFoundError):  # ще не почався — позначати рано
        await admin.mark_booking(session, future.id, BookingStatus.NO_SHOW, now)

    assert (await admin.cancel_booking_by_admin(session, future.id, now)).status is BookingStatus.CANCELLED_BY_ADMIN
    assert (await admin.mark_booking(session, started.id, BookingStatus.COMPLETED, now)).status is BookingStatus.COMPLETED
    with pytest.raises(BookingNotFoundError):  # повторно
        await admin.mark_booking(session, started.id, BookingStatus.NO_SHOW, now)
    with pytest.raises(ValueError):
        await admin.mark_booking(session, started.id, BookingStatus.CONFIRMED, now)


async def test_future_bookings_filters(session, shop):
    session.add_all([
        _booking(shop, kyiv(MONDAY, 10)),
        _booking(shop, kyiv(MONDAY, 12), service="beard"),
        _booking(shop, kyiv(MONDAY, 12), master="b"),
        _booking(shop, NOW - timedelta(hours=1)),  # минулий
        _booking(shop, kyiv(MONDAY, 16), status=BookingStatus.CANCELLED_BY_CLIENT),
    ])
    await session.commit()
    a, b = shop["a"].id, shop["b"].id
    haircut = shop["haircut"].id
    assert len(await admin.future_bookings(session, NOW, master_id=a)) == 2
    assert len(await admin.future_bookings(session, NOW, service_id=haircut)) == 2
    assert len(await admin.future_bookings(session, NOW, master_id=a, service_id=haircut)) == 1
    assert len(await admin.future_bookings(session, NOW, master_id=b)) == 1


async def test_schedule_conflicts(session, shop):
    tuesday = MONDAY + timedelta(days=1)
    early, late, lunch, other_day = (
        _booking(shop, kyiv(MONDAY, 10)),
        _booking(shop, kyiv(MONDAY, 18)),
        _booking(shop, kyiv(MONDAY, 13, 30)),
        _booking(shop, kyiv(tuesday, 10)),
    )
    session.add_all([early, late, lunch, other_day])
    await session.commit()
    bookings = await admin.future_bookings(session, NOW, master_id=shop["a"].id)

    def conflicts(hours):
        return {b.start_dt for b in admin.schedule_conflicts(bookings, 0, hours, KYIV)}

    assert conflicts(Hours(time(10), time(20))) == set()
    assert conflicts(Hours(time(11), time(20))) == {early.start_dt}
    assert conflicts(Hours(time(10), time(18, 30))) == {late.start_dt}  # не вміщається до кінця
    assert conflicts(Hours(time(10), time(20), time(13), time(14))) == {lunch.start_dt}
    assert conflicts(None) == {early.start_dt, late.start_dt, lunch.start_dt}  # вихідний


async def test_time_off_conflicts(session, shop):
    session.add_all([_booking(shop, kyiv(MONDAY, 10)), _booking(shop, kyiv(MONDAY, 13))])
    await session.commit()
    found = await admin.time_off_conflicts(
        session, shop["a"].id, kyiv(MONDAY, 10, 30), kyiv(MONDAY, 12), NOW
    )
    assert [b.start_dt for b in found] == [kyiv(MONDAY, 10)]


async def test_services_and_masters_crud(session, shop):
    svc = await admin.create_service(session, "Укладка", 30, 300)
    await admin.update_service(session, svc.id, price=350, is_active=False)
    assert (await session.get(Service, svc.id)).price == 350
    with pytest.raises(ValueError):
        await admin.update_service(session, svc.id, id=99)

    master = await admin.create_master(session, "Віталій")
    await admin.update_master(session, master.id, telegram_id=777)
    assert await admin.toggle_master_service(session, master.id, svc.id) is True
    assert [s.name for s in (await admin.get_master(session, master.id)).services] == ["Укладка"]
    assert await admin.toggle_master_service(session, master.id, svc.id) is False
    assert (await admin.get_master(session, master.id)).services == []


async def test_set_working_hours(session, shop):
    a = shop["a"].id
    await admin.set_working_hours(session, a, 0, Hours(time(9), time(18)))
    await admin.set_working_hours(session, a, 6, Hours(time(11), time(16)))  # новий день
    await admin.set_working_hours(session, a, 5, None)  # субота — вихідний
    hours = {wh.weekday: wh for wh in (await admin.get_master(session, a)).working_hours}
    assert (hours[0].start_time, hours[0].end_time, hours[0].break_start) == (time(9), time(18), None)
    assert hours[6].start_time == time(11)
    assert 5 not in hours
