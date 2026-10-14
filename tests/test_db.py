from datetime import UTC, datetime, time, timedelta, timezone

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, StatementError

from bot.config import BASE_DIR
from bot.db import repo
from bot.db.models import (
    Base,
    Booking,
    BookingStatus,
    Client,
    Master,
    Service,
    WorkingHours,
)


def test_migrations_match_models(tmp_path):
    db_path = tmp_path / "migrated.db"
    config = Config(BASE_DIR / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    config.attributes["configure_logger"] = False

    command.upgrade(config, "head")

    engine = create_sync_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []

    command.downgrade(config, "base")


async def _make_booking(session, start: datetime) -> Booking:
    service = Service(name="Стрижка", duration_min=45, price=500)
    master = Master(name="Майстер", services=[service])
    client = Client(telegram_id=1, name="Клієнт")
    booking = Booking(
        client=client,
        master=master,
        service=service,
        start_dt=start,
        end_dt=start + timedelta(minutes=45),
    )
    session.add(booking)
    await session.commit()
    return booking


async def test_datetime_stored_and_returned_as_utc(session):
    kyiv_summer = timezone(timedelta(hours=3))
    booking = await _make_booking(session, datetime(2026, 7, 1, 10, 0, tzinfo=kyiv_summer))
    booking_id = booking.id
    session.expire_all()

    loaded = await session.get(Booking, booking_id)
    assert loaded.start_dt == datetime(2026, 7, 1, 7, 0, tzinfo=UTC)
    assert loaded.start_dt.tzinfo is UTC
    assert loaded.status is BookingStatus.CONFIRMED
    assert loaded.reminded_24h is False and loaded.reminded_2h is False
    assert loaded.created_at.tzinfo is UTC


async def test_naive_datetime_rejected(session):
    with pytest.raises(StatementError):
        await _make_booking(session, datetime(2026, 7, 1, 10, 0))


async def test_deleting_master_cascades_working_hours(session):
    master = Master(
        name="Майстер",
        working_hours=[WorkingHours(weekday=0, start_time=time(10), end_time=time(20))],
    )
    session.add(master)
    await session.commit()

    await session.delete(master)
    await session.commit()
    assert (await session.scalars(select(WorkingHours))).all() == []


async def test_working_hours_unique_per_weekday(session):
    master = Master(name="Майстер")
    session.add_all(
        [
            WorkingHours(master=master, weekday=0, start_time=time(10), end_time=time(20)),
            WorkingHours(master=master, weekday=0, start_time=time(11), end_time=time(19)),
        ]
    )
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_repo_queries(session):
    haircut = Service(name="Стрижка", duration_min=45, price=500)
    hidden = Service(name="Прихована", duration_min=30, price=100, is_active=False)
    active = Master(name="Активний", services=[haircut])
    inactive = Master(name="Неактивний", services=[haircut], is_active=False)
    other = Master(name="Інший")
    session.add_all([haircut, hidden, active, inactive, other])
    await session.commit()

    assert [s.name for s in await repo.get_active_services(session)] == ["Стрижка"]
    assert [m.name for m in await repo.get_masters_for_service(session, haircut.id)] == [
        "Активний"
    ]
