"""Демо-дані з профілю бізнесу (розділ `demo` у config/profiles/<профіль>.yaml).

Запуск: python -m seed.demo_data
Застосовує міграції, ВИДАЛЯЄ всі дані й заповнює базу заново. Тому працює
лише при DEMO_MODE=true (або з прапорцем --force).
"""

import asyncio
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from alembic import command
from alembic.config import Config
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import BASE_DIR, BaseAppSettings, BusinessConfig, DemoData, load_business_config
from bot.db.models import (
    Booking,
    BookingStatus,
    Client,
    FsmState,
    Master,
    MasterService,
    Pet,
    Service,
    TimeOff,
    WorkingHours,
)
from bot.db.session import create_engine, create_session_factory
from bot.services.demo import DEMO_CLIENT_TG_BASE  # вигадані Telegram ID демо-клієнтів
from bot.services.pets import offer_for

@dataclass
class SeedSummary:
    services: int
    masters: int
    clients: int
    bookings: int
    time_off: int


async def clear_all(session: AsyncSession) -> None:
    # Порядок важливий через зовнішні ключі
    # FsmState — незавершені діалоги посилаються на дані, яких після скидання не буде
    for model in (
        FsmState, Booking, Pet, TimeOff, WorkingHours, MasterService, Client, Master, Service
    ):
        await session.execute(delete(model))


def _local_dt(day: date, at: time, business: BusinessConfig) -> datetime:
    return datetime.combine(day, at, tzinfo=business.booking.tz).astimezone(UTC)


def _fits(start: datetime, end: datetime, day: date, schedule, business: BusinessConfig) -> bool:
    """Запис у межах робочого дня й не заходить на перерву."""
    if start < _local_dt(day, schedule.start, business) or end > _local_dt(day, schedule.end, business):
        return False
    if schedule.break_start is None:
        return True
    br_start = _local_dt(day, schedule.break_start, business)
    br_end = _local_dt(day, schedule.break_end, business)
    return end <= br_start or start >= br_end


def _service_for(
    master: Master, wanted: Service, services: dict[str, Service], demo: DemoData
) -> Service | None:
    """Послуга шаблону, якщо майстер її робить; інакше — заміна, що вміщається в той самий час."""
    if wanted in master.services:
        return wanted
    fallback = services.get(demo.fallback_service) if demo.fallback_service else None
    if fallback is not None and fallback in master.services:
        return fallback
    fitting = [s for s in master.services if s.duration_min <= wanted.duration_min]
    return max(fitting, key=lambda s: s.duration_min, default=None)


async def reset_demo_data(
    session: AsyncSession, business: BusinessConfig, now: datetime | None = None
) -> SeedSummary:
    """Очищає базу й заповнює демо-даними профілю відносно поточного моменту."""
    demo = business.demo
    if demo is None:
        raise ValueError("У профілі бізнесу немає розділу demo — демо-дані взяти нізвідки")
    now = now or datetime.now(UTC)
    today = now.astimezone(business.booking.tz).date()
    schedule = demo.schedule

    await clear_all(session)

    services = {
        s.key: Service(name=s.name, duration_min=s.duration_min, price=s.price)
        for s in demo.services
    }
    session.add_all(services.values())

    masters: dict[str, Master] = {}
    for item in demo.masters:
        master = Master(name=item.name, services=[services[k] for k in item.services])
        master.working_hours = [
            WorkingHours(
                weekday=weekday,
                start_time=schedule.start,
                end_time=schedule.end,
                break_start=schedule.break_start,
                break_end=schedule.break_end,
            )
            for weekday in schedule.weekdays
        ]
        masters[item.name] = master
    session.add_all(masters.values())

    clients = [
        Client(telegram_id=DEMO_CLIENT_TG_BASE + i, name=c.name, phone=c.phone)
        for i, c in enumerate(demo.clients, start=1)
    ]
    session.add_all(clients)
    await session.flush()  # id клієнтів потрібні для тварин

    # Тварини демо-клієнтів (профілі з розділом pets)
    pets: dict[int, Pet] = {}
    for client, item in zip(clients, demo.clients):
        if item.pet is not None:
            pets[client.id] = Pet(
                client_id=client.id, name=item.pet.name, species=item.pet.species,
                breed=item.pet.breed, size=item.pet.size,
            )
    session.add_all(pets.values())

    time_off = [
        TimeOff(
            master=masters[t.master],
            start_dt=_local_dt(today + timedelta(days=t.day_offset), t.start, business),
            end_dt=_local_dt(today + timedelta(days=t.day_offset), t.end, business),
            reason=t.reason,
        )
        for t in demo.time_off
    ]
    session.add_all(time_off)

    bookings: list[Booking] = []
    busy: dict[str, list[tuple[datetime, datetime]]] = {name: [] for name in masters}
    patterns = demo.booking_patterns
    for offset in range(demo.booking_days):
        day = today + timedelta(days=offset)
        if day.weekday() not in schedule.weekdays:
            continue
        for m_idx, (master_name, master) in enumerate(masters.items()):
            for slot in patterns[(offset + m_idx) % len(patterns)] if patterns else []:
                service = _service_for(master, services[slot.service], services, demo)
                if service is None:
                    continue
                client = clients[len(bookings) % len(clients)]
                pet = pets.get(client.id)
                # Тривалість і ціна — за розміром тварини, якщо вона є
                offer = offer_for(service, pet.size if pet else None, business.pets)
                if offer is None:
                    continue
                start = _local_dt(day, slot.time, business)
                end = start + timedelta(minutes=offer.duration_min)
                if not _fits(start, end, day, schedule, business) or any(
                    start < b_end and b_start < end for b_start, b_end in busy[master_name]
                ) or any(t.master is master and start < t.end_dt and t.start_dt < end for t in time_off):
                    # Довша за шаблон послуга (великий пес) не вмістилася — пропускаємо
                    continue
                busy[master_name].append((start, end))
                bookings.append(
                    Booking(
                        client=client,
                        pet=pet,
                        price=offer.price,
                        master=master,
                        service=service,
                        start_dt=start,
                        end_dt=end,
                        status=BookingStatus.COMPLETED if end <= now else BookingStatus.CONFIRMED,
                        created_at=now,
                        # Демо-клієнти вигадані — нагадування їм не надсилаємо
                        reminded_24h=True,
                        reminded_2h=True,
                    )
                )
    session.add_all(bookings)
    await session.flush()

    return SeedSummary(
        services=len(services),
        masters=len(masters),
        clients=len(clients),
        bookings=len(bookings),
        time_off=len(time_off),
    )


def run_migrations() -> None:
    config = Config(BASE_DIR / "alembic.ini")
    command.upgrade(config, "head")


async def _seed(settings: BaseAppSettings) -> SeedSummary:
    business = load_business_config(settings.business_config_path)
    engine = create_engine(settings.database_url)
    try:
        async with create_session_factory(engine)() as session, session.begin():
            return await reset_demo_data(session, business)
    finally:
        await engine.dispose()


def main() -> None:
    settings = BaseAppSettings()
    if not settings.demo_mode and "--force" not in sys.argv[1:]:
        sys.exit(
            "Seed видаляє всі дані з бази. Запуск дозволено лише при DEMO_MODE=true "
            "або з прапорцем --force."
        )
    run_migrations()
    summary = asyncio.run(_seed(settings))
    print(
        f"Демо-дані завантажено: послуг {summary.services}, майстрів {summary.masters}, "
        f"клієнтів {summary.clients}, записів {summary.bookings}, "
        f"блокувань часу {summary.time_off}."
    )


if __name__ == "__main__":
    main()
