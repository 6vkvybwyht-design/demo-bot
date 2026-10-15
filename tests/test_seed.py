from datetime import UTC, datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from bot.db.models import Booking, BookingStatus, Client, Master, Service, TimeOff, WorkingHours
from seed.demo_data import BREAK_END, BREAK_START, DAY_END, DAY_START, reset_demo_data

# Середа; наступні 7 днів захоплюють перехід Києва на зимовий час (25.10.2026)
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


async def _count(session, model) -> int:
    return await session.scalar(select(func.count()).select_from(model))


async def test_seed_counts(session, business):
    summary = await reset_demo_data(session, business, now=NOW)
    await session.commit()

    assert await _count(session, Service) == summary.services == 5
    assert await _count(session, Master) == summary.masters == 3
    assert await _count(session, WorkingHours) == 3 * 6  # Пн–Сб
    assert await _count(session, Client) == summary.clients
    assert await _count(session, TimeOff) == summary.time_off == 1
    assert await _count(session, Booking) == summary.bookings > 0


async def test_seed_is_repeatable(session, business):
    first = await reset_demo_data(session, business, now=NOW)
    await session.commit()
    second = await reset_demo_data(session, business, now=NOW)
    await session.commit()

    assert first == second
    assert await _count(session, Booking) == second.bookings


async def test_seed_bookings_are_realistic(session, business):
    await reset_demo_data(session, business, now=NOW)
    await session.commit()

    tz = business.booking.tz
    step = business.booking.slot_step_min
    bookings = (await session.scalars(select(Booking).order_by(Booking.start_dt))).all()
    time_off = (await session.scalars(select(TimeOff))).all()
    # Зв'язки завантажуємо явно: ліниве довантаження в async-сесії не працює
    masters = await session.scalars(select(Master).options(selectinload(Master.services)))
    services_by_master = {m.id: {s.id for s in m.services} for m in masters}

    by_master: dict[int, list[Booking]] = {}
    for b in bookings:
        start, end = b.start_dt.astimezone(tz), b.end_dt.astimezone(tz)
        assert start.date() == end.date()
        assert start.weekday() != 6, "у неділю вихідний"
        assert DAY_START <= start.time() and end.time() <= DAY_END
        assert end.time() <= BREAK_START or start.time() >= BREAK_END, "запис у перерву"
        assert start.minute % step == 0
        assert b.service_id in services_by_master[b.master_id]
        assert not any(
            t.master_id == b.master_id and b.start_dt < t.end_dt and t.start_dt < b.end_dt
            for t in time_off
        )
        expected = BookingStatus.COMPLETED if b.end_dt <= NOW else BookingStatus.CONFIRMED
        assert b.status is expected
        by_master.setdefault(b.master_id, []).append(b)

    for items in by_master.values():
        for prev, nxt in zip(items, items[1:]):
            assert prev.end_dt <= nxt.start_dt, "записи майстра перетинаються"

    # Після переходу на зимовий час 10:00 за Києвом — це 08:00 UTC, а не 07:00
    after_dst = [b for b in bookings if b.start_dt.astimezone(tz).date().day == 26]
    assert any(b.start_dt.astimezone(tz).time() == time(10, 0) for b in after_dst)
    assert all(b.start_dt.hour >= 8 for b in after_dst)
