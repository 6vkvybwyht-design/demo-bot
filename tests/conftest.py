from datetime import time

import pytest

from bot import texts
from bot.config import DEFAULT_BUSINESS_CONFIG, Settings, load_business_config
from bot.db.models import Base, Master, Service, WorkingHours
from bot.db.session import create_engine, create_session_factory
from bot.handlers import admin, client, fallback, my_bookings
from bot.main import create_dispatcher
from tests.fake_telegram import TelegramUser


@pytest.fixture
def business():
    return load_business_config(DEFAULT_BUSINESS_CONFIG)


@pytest.fixture
async def engine(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'db' / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine):
    async with create_session_factory(engine)() as session:
        yield session


@pytest.fixture(autouse=True)
def _reset_texts():
    # Тексти профілю застосовуються до модуля texts — повертаємо їх після кожного тесту
    yield
    texts.reset()


@pytest.fixture(autouse=True)
def _detach_routers():
    # Модульний router в aiogram можна приєднати лише до одного Dispatcher,
    # а тести створюють новий диспетчер на кожен тест
    yield
    for module in (admin, client, fallback, my_bookings):
        module.router._parent_router = None


# --- Тестовий салон для наскрізних тестів через Dispatcher --------------------

ADMIN_ID = 500
MASTER_TG_ID = 600  # Telegram ID майстра Дмитра — отримує сповіщення


def every_day():
    # Працюють щодня, щоб тест не залежав від поточної дати й часу
    return [WorkingHours(weekday=wd, start_time=time(0), end_time=time(23, 45)) for wd in range(7)]


async def _make_dp(engine, business, *, demo_mode: bool):
    factory = create_session_factory(engine)
    async with factory() as session:
        haircut = Service(name="Стрижка", duration_min=45, price=500)
        beard = Service(name="Борода", duration_min=30, price=350)
        session.add_all([
            haircut, beard,
            Master(name="Олександр", services=[haircut, beard], working_hours=every_day()),
            Master(name="Дмитро", services=[haircut], working_hours=every_day(),
                   telegram_id=MASTER_TG_ID),
            Master(name="Максим", services=[beard], working_hours=every_day()),
        ])
        await session.commit()
    settings = Settings(
        _env_file=None, bot_token="42:TEST", admin_ids=[ADMIN_ID], demo_mode=demo_mode
    )
    return create_dispatcher(settings, business, factory)


@pytest.fixture
async def dp(engine, business):
    return await _make_dp(engine, business, demo_mode=False)


@pytest.fixture
async def demo_dp(engine, business):
    return await _make_dp(engine, business, demo_mode=True)


@pytest.fixture
def user(dp):
    return TelegramUser(dp)


@pytest.fixture
def owner(dp):
    return TelegramUser(dp, user_id=ADMIN_ID, name="Власник")
