"""Демо-дані барбершопу.

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

from bot.config import BASE_DIR, BaseAppSettings, BusinessConfig, load_business_config
from bot.db.models import (
    Booking,
    BookingStatus,
    Client,
    Master,
    MasterService,
    Service,
    TimeOff,
    WorkingHours,
)
from bot.db.session import create_engine, create_session_factory
from bot.services.demo import DEMO_CLIENT_TG_BASE  # вигадані Telegram ID демо-клієнтів

# Послуги: ключ -> (назва, тривалість у хв, ціна в грн)
SERVICES = {
    "haircut": ("Чоловіча стрижка", 45, 500),
    "beard": ("Стрижка бороди", 30, 350),
    "combo": ("Комплекс (стрижка + борода)", 75, 750),
    "kids": ("Дитяча стрижка", 30, 350),
    "gray": ("Камуфлювання сивини", 30, 400),
}

# Майстри та послуги, які вони виконують
MASTERS = [
    ("Олександр", ["haircut", "beard", "combo", "kids", "gray"]),
    ("Дмитро", ["haircut", "beard", "combo", "kids"]),
    ("Максим", ["haircut", "beard", "combo", "gray"]),
]

# Графік: Пн–Сб 10:00–20:00, перерва 14:00–15:00
WORKDAYS = range(0, 6)
DAY_START, DAY_END = time(10, 0), time(20, 0)
BREAK_START, BREAK_END = time(14, 0), time(15, 0)

CLIENTS = [
    ("Андрій", "+380501112233"),
    ("Богдан", "+380672223344"),
    ("Віктор", "+380633334455"),
    ("Ігор", "+380934445566"),
    ("Сергій", "+380995556677"),
]

# Шаблони зайнятих слотів (година, хвилина, послуга). Усі вміщаються в робочий
# день, не зачіпають перерву й вирівняні по кроку 15 хв.
BOOKING_PATTERNS = [
    [(10, 0, "haircut"), (11, 30, "combo"), (16, 0, "beard"), (18, 15, "haircut")],
    [(10, 30, "beard"), (12, 0, "haircut"), (15, 0, "combo"), (17, 45, "kids")],
    [(11, 0, "haircut"), (13, 0, "beard"), (16, 30, "gray")],
]
BOOKING_DAYS = 7  # на скільки днів уперед (від сьогодні) генеруємо записи

# Блокування часу: (індекс майстра, зсув у днях, початок, кінець, причина)
TIME_OFF = [(1, 2, time(10, 0), time(13, 0), "Особисті справи")]


@dataclass
class SeedSummary:
    services: int
    masters: int
    clients: int
    bookings: int
    time_off: int


async def clear_all(session: AsyncSession) -> None:
    # Порядок важливий через зовнішні ключі
    for model in (Booking, TimeOff, WorkingHours, MasterService, Client, Master, Service):
        await session.execute(delete(model))


def _local_dt(day: date, at: time, business: BusinessConfig) -> datetime:
    return datetime.combine(day, at, tzinfo=business.booking.tz).astimezone(UTC)


async def reset_demo_data(
    session: AsyncSession, business: BusinessConfig, now: datetime | None = None
) -> SeedSummary:
    """Очищає базу й заповнює демо-даними відносно поточного моменту."""
    now = now or datetime.now(UTC)
    today = now.astimezone(business.booking.tz).date()

    await clear_all(session)

    services = {
        key: Service(name=name, duration_min=duration, price=price)
        for key, (name, duration, price) in SERVICES.items()
    }
    session.add_all(services.values())

    masters: list[Master] = []
    for name, service_keys in MASTERS:
        master = Master(name=name, services=[services[k] for k in service_keys])
        master.working_hours = [
            WorkingHours(
                weekday=weekday,
                start_time=DAY_START,
                end_time=DAY_END,
                break_start=BREAK_START,
                break_end=BREAK_END,
            )
            for weekday in WORKDAYS
        ]
        masters.append(master)
    session.add_all(masters)

    clients = [
        Client(telegram_id=DEMO_CLIENT_TG_BASE + i, name=name, phone=phone)
        for i, (name, phone) in enumerate(CLIENTS, start=1)
    ]
    session.add_all(clients)

    time_off = [
        TimeOff(
            master=masters[master_idx],
            start_dt=_local_dt(today + timedelta(days=offset), start, business),
            end_dt=_local_dt(today + timedelta(days=offset), end, business),
            reason=reason,
        )
        for master_idx, offset, start, end, reason in TIME_OFF
    ]
    session.add_all(time_off)

    bookings: list[Booking] = []
    for offset in range(BOOKING_DAYS):
        day = today + timedelta(days=offset)
        if day.weekday() not in WORKDAYS:
            continue
        for m_idx, master in enumerate(masters):
            pattern = BOOKING_PATTERNS[(offset + m_idx) % len(BOOKING_PATTERNS)]
            for hour, minute, service_key in pattern:
                service = services[service_key]
                if service not in master.services:
                    service = services["haircut"]
                start = _local_dt(day, time(hour, minute), business)
                end = start + timedelta(minutes=service.duration_min)
                if any(t.master is master and start < t.end_dt and t.start_dt < end for t in time_off):
                    continue
                bookings.append(
                    Booking(
                        client=clients[len(bookings) % len(clients)],
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
