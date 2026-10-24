"""Стан FSM у базі: незавершений запис переживає перезапуск бота."""

from aiogram.fsm.storage.base import StorageKey
from sqlalchemy import func, select

from bot import texts
from bot.db.fsm_storage import DbStorage
from bot.db.models import Booking, FsmState
from bot.db.session import create_session_factory
from bot.handlers import admin, client, fallback, my_bookings, spa
from bot.main import create_dispatcher
from bot.services.demo import reset_demo
from bot.states import BookingStates
from tests.fake_telegram import TelegramUser


def _key(user_id: int, bot_id: int = 42) -> StorageKey:
    return StorageKey(bot_id=bot_id, chat_id=user_id, user_id=user_id)


async def _rows(engine) -> int:
    async with create_session_factory(engine)() as session:
        return await session.scalar(select(func.count()).select_from(FsmState))


async def test_state_and_data_roundtrip(engine):
    storage = DbStorage(create_session_factory(engine))
    key = _key(1)
    assert await storage.get_state(key) is None and await storage.get_data(key) == {}

    await storage.set_state(key, BookingStates.time)
    await storage.set_data(key, {"service_id": 3, "master_id": None, "day": "2026-10-20"})
    await storage.update_data(key, {"start": "2026-10-20T07:00:00+00:00"})
    assert await storage.get_state(key) == BookingStates.time.state
    assert await storage.get_data(key) == {
        "service_id": 3, "master_id": None, "day": "2026-10-20", "start": "2026-10-20T07:00:00+00:00",
    }

    # Інші користувачі й інші боти — окремо
    assert await storage.get_state(_key(2)) is None
    assert await storage.get_state(_key(1, bot_id=43)) is None

    # Новий екземпляр сховища (як після перезапуску) бачить те саме
    restarted = DbStorage(create_session_factory(engine))
    assert await restarted.get_state(key) == BookingStates.time.state


async def test_cleared_state_removes_row(engine):
    storage = DbStorage(create_session_factory(engine))
    key = _key(1)
    await storage.set_state(key, BookingStates.date)
    await storage.set_data(key, {"service_id": 1})
    assert await _rows(engine) == 1
    await storage.set_state(key, None)
    assert await _rows(engine) == 1  # дані ще є
    await storage.set_data(key, {})
    assert await _rows(engine) == 0
    await storage.set_state(key, None)  # очищення неіснуючого — без помилок
    assert await _rows(engine) == 0


def _detach_routers() -> None:
    for module in (admin, client, fallback, my_bookings, spa):
        module.router._parent_router = None


async def test_unfinished_booking_survives_restart(dp, engine, business):
    user = TelegramUser(dp)
    await user.send(texts.BTN_BOOK)
    await user.click(user.button("Стрижка · 45 хв · 500 грн"))
    await user.click(user.button("Дмитро"))
    await user.click(user.buttons()[0][1])
    slot = user.slot_buttons()[0][1]

    # «Перезапуск»: новий диспетчер і новий бот, та сама база
    _detach_routers()
    restarted_dp = create_dispatcher(dp["settings"], business, create_session_factory(engine))
    restarted = TelegramUser(restarted_dp)

    await restarted.click(slot)  # кнопка зі старого повідомлення спрацьовує
    assert restarted.last_text() == texts.ASK_PHONE
    await restarted.share_contact("+380501234567")
    assert "Дмитро" in restarted.last_text()  # вибір до перезапуску збережено
    await restarted.click(restarted.button(texts.BTN_CONFIRM))
    assert "Ви записані" in restarted.last_text()
    async with create_session_factory(engine)() as session:
        assert await session.scalar(select(func.count()).select_from(Booking)) == 1
    assert await _rows(engine) == 0  # після завершення стан прибрано


async def test_demo_reset_clears_unfinished_dialogs(engine, business):
    factory = create_session_factory(engine)
    storage = DbStorage(factory)
    await storage.set_state(_key(1), BookingStates.confirm)
    await storage.set_data(_key(1), {"service_id": 999})
    await reset_demo(factory, business)
    assert await storage.get_state(_key(1)) is None
