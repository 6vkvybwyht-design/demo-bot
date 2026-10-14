"""Хендлери клієнтського сценарію."""

from aiogram import Router, html
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot import texts
from bot.config import BusinessConfig
from bot.keyboards.main_menu import main_menu_kb

router = Router(name="client")


@router.message(CommandStart())
async def cmd_start(message: Message, business: BusinessConfig) -> None:
    await message.answer(
        texts.WELCOME.format(name=html.quote(business.business.name)),
        reply_markup=main_menu_kb(),
    )
