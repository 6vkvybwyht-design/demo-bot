"""Seed демо-даних — для кожного профілю бізнесу."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from bot.config import list_profiles, load_business_config, profile_path
from bot.db.models import Booking, BookingStatus, Client, Master, Service, TimeOff, WorkingHours
from bot.services import spa as spa_service
from seed.demo_data import reset_demo_data

# Середа; наступні 7 днів захоплюють перехід Києва на зимовий час (25.10.2026)
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
PROFILES = ["barber", "groom", "nails", "spa", "spa_demo"]


@pytest.fixture(params=PROFILES)
def profile(request):
    return load_business_config(profile_path(request.param))


def test_all_profiles_are_tested():
    assert set(list_profiles()) == set(PROFILES)


async def _count(session, model) -> int:
    return await session.scalar(select(func.count()).select_from(model))


async def test_seed_counts(session, profile):
    demo = profile.demo
    summary = await reset_demo_data(session, profile, now=NOW)
    await session.commit()

    assert await _count(session, Service) == summary.services == len(demo.services)
    assert await _count(session, Master) == summary.masters == len(demo.masters)
    assert await _count(session, WorkingHours) == len(demo.masters) * len(demo.schedule.weekdays)
    assert await _count(session, Client) == summary.clients == len(demo.clients)
    assert await _count(session, TimeOff) == summary.time_off == len(demo.time_off)
    assert await _count(session, Booking) == summary.bookings > 0


async def test_seed_is_repeatable(session, profile):
    first = await reset_demo_data(session, profile, now=NOW)
    await session.commit()
    second = await reset_demo_data(session, profile, now=NOW)
    await session.commit()

    assert first == second
    assert await _count(session, Booking) == second.bookings


async def test_seed_bookings_are_realistic(session, profile):
    await reset_demo_data(session, profile, now=NOW)
    await session.commit()

    tz = profile.booking.tz
    step = profile.booking.slot_step_min
    schedule = profile.demo.schedule
    bookings = (
        await session.scalars(
            select(Booking).options(selectinload(Booking.service)).order_by(Booking.start_dt)
        )
    ).all()
    time_off = (await session.scalars(select(TimeOff))).all()
    # Зв'язки завантажуємо явно: ліниве довантаження в async-сесії не працює
    masters = await session.scalars(select(Master).options(selectinload(Master.services)))
    services_by_master = {m.id: {s.id for s in m.services} for m in masters}

    by_master: dict[int, list[Booking]] = {}
    for b in bookings:
        start, end = b.start_dt.astimezone(tz), b.end_dt.astimezone(tz)
        assert start.date() == end.date()
        assert start.weekday() in schedule.weekdays, "запис у вихідний"
        day_start, day_end = schedule.hours_for(start.weekday())
        assert day_start <= start.time() and end.time() <= day_end
        if schedule.break_start is not None:
            assert end.time() <= schedule.break_start or start.time() >= schedule.break_end, "запис у перерву"
        assert start.minute % step == 0
        assert b.service_id in services_by_master[b.master_id]
        assert not any(
            t.master_id == b.master_id and b.start_dt < t.end_dt and t.start_dt < b.end_dt
            for t in time_off
        )
        expected = BookingStatus.COMPLETED if b.end_dt <= NOW else BookingStatus.CONFIRMED
        assert b.status is expected
        # Учасники групової події (спа) записані на той самий час — це не перетин
        if not spa_service.is_event(profile.spa, b.service.name):
            by_master.setdefault(b.master_id, []).append(b)
            if b.second_master_id is not None:  # масаж для двох — обидва майстри зайняті
                assert b.second_master_id != b.master_id
                assert b.service_id in services_by_master[b.second_master_id]
                by_master.setdefault(b.second_master_id, []).append(b)

    for items in by_master.values():
        items.sort(key=lambda b: b.start_dt)
        for prev, nxt in zip(items, items[1:]):
            assert prev.end_dt <= nxt.start_dt, "записи майстра перетинаються"

    # Після переходу на зимовий час графік — за київським часом, а не зсунутий на годину
    after_dst = [b for b in bookings if b.start_dt.astimezone(tz).date().day == 26]
    assert after_dst
    first_after_dst = min(b.start_dt.astimezone(tz) for b in after_dst)
    assert first_after_dst.time() >= schedule.hours_for(first_after_dst.weekday())[0]


async def test_every_master_gets_bookings(session, profile):
    await reset_demo_data(session, profile, now=NOW)
    await session.commit()
    busy = set((await session.scalars(select(Booking.master_id))).all())
    assert busy == set((await session.scalars(select(Master.id))).all())


async def test_profile_without_demo_section(session, profile):
    with pytest.raises(ValueError, match="demo"):
        await reset_demo_data(session, profile.model_copy(update={"demo": None}), now=NOW)


async def test_nails_profile_data(session):
    nails = load_business_config(profile_path("nails"))
    await reset_demo_data(session, nails, now=NOW)
    await session.commit()

    services = {s.name: (s.duration_min, s.price) for s in (await session.scalars(select(Service))).all()}
    assert services == {
        "Манікюр класичний": (60, 350),
        "Манікюр з покриттям гель-лак": (90, 550),
        "Нарощування нігтів": (150, 900),
        "Корекція нарощених нігтів": (120, 700),
        "Педикюр без покриття": (60, 450),
        "Педикюр з покриттям гель-лак": (90, 650),
        "Зняття покриття": (20, 100),
    }
    masters = {
        m.name: {s.name for s in m.services}
        for m in await session.scalars(select(Master).options(selectinload(Master.services)))
    }
    assert masters["Олена"] == set(services)
    assert not any("Педикюр" in s for s in masters["Ірина"])
    assert "Нарощування нігтів" in masters["Ірина"]
    assert not any(s in masters["Марта"] for s in ("Нарощування нігтів", "Корекція нарощених нігтів"))
    assert {"Педикюр без покриття", "Манікюр класичний"} <= masters["Марта"]

    hours = (await session.scalars(select(WorkingHours))).all()
    assert {(h.start_time.hour, h.end_time.hour, h.break_start.hour, h.break_end.hour) for h in hours} == {
        (9, 20, 13, 14)
    }
    assert 6 not in {h.weekday for h in hours}  # неділя — вихідний
    clients = (await session.scalars(select(Client.name))).all()
    assert set(clients) == {"Анна", "Катерина", "Софія", "Марія", "Юлія"}


async def test_seed_relative_to_today(session, profile):
    later = NOW + timedelta(days=30)
    await reset_demo_data(session, profile, now=later)
    await session.commit()
    first = await session.scalar(select(func.min(Booking.start_dt)))
    assert first.astimezone(profile.booking.tz).date() >= later.astimezone(profile.booking.tz).date()
