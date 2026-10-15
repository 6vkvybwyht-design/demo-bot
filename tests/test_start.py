from unittest.mock import AsyncMock

from aiogram.types import ReplyKeyboardMarkup

from bot import texts
from bot.config import DEFAULT_BUSINESS_CONFIG, Settings, load_business_config
from bot.handlers.client import cmd_start
from bot.db.session import create_engine, create_session_factory
from bot.main import create_dispatcher


async def test_start_greets_with_business_name_and_menu():
    business = load_business_config(DEFAULT_BUSINESS_CONFIG)
    message = AsyncMock()

    await cmd_start(message, business=business, state=AsyncMock())

    message.answer.assert_awaited_once()
    text = message.answer.await_args.args[0]
    markup = message.answer.await_args.kwargs["reply_markup"]
    assert "Demo Barber" in text
    assert isinstance(markup, ReplyKeyboardMarkup)
    buttons = [b.text for row in markup.keyboard for b in row]
    assert buttons == [texts.BTN_BOOK, texts.BTN_MY_BOOKINGS, texts.BTN_CONTACTS]


async def test_dispatcher_builds():
    settings = Settings(_env_file=None, bot_token="123:abc")
    business = load_business_config(DEFAULT_BUSINESS_CONFIG)
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    dp = create_dispatcher(settings, business, create_session_factory(engine))
    await engine.dispose()
    assert dp["business"] is business
    assert dp["settings"] is settings
