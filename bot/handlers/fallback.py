"""Обробники за замовчуванням. Роутер підключається останнім."""

from aiogram import Router
from aiogram.types import CallbackQuery
from aiogram.utils.callback_answer import CallbackAnswer

from bot import texts

router = Router(name="fallback")


@router.callback_query()
async def stale_button(callback: CallbackQuery, callback_answer: CallbackAnswer) -> None:
    # Кнопка зі старого повідомлення або з іншого кроку
    callback_answer.text = texts.STALE_BUTTON
    callback_answer.show_alert = True
