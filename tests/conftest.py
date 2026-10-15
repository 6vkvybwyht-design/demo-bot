import pytest

from bot.config import DEFAULT_BUSINESS_CONFIG, load_business_config
from bot.db.models import Base
from bot.db.session import create_engine, create_session_factory


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
def _detach_routers():
    # Модульний router в aiogram можна приєднати лише до одного Dispatcher,
    # а тести створюють новий диспетчер на кожен тест
    yield
    from bot.handlers import client, fallback, my_bookings

    for module in (client, fallback, my_bookings):
        module.router._parent_router = None
