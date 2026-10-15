import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot import texts
from bot.services.admin import is_admin

logger = logging.getLogger(__name__)


class AdminOnlyMiddleware(BaseMiddleware):
    """Пропускає до хендлерів адмінки лише ADMIN_IDS.

    Вішається на роутер адмінки як inner-middleware, тож спрацьовує перед
    кожним його хендлером: командою, введенням тексту й кожною кнопкою.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is not None and is_admin(user.id, data["settings"]):
            return await handler(event, data)

        logger.warning("Спроба доступу до адмінки без прав: user_id=%s", user.id if user else None)
        if isinstance(event, CallbackQuery):
            # На callback відповідає CallbackAnswerMiddleware — лише задаємо текст
            callback_answer = data["callback_answer"]
            callback_answer.text = texts.ADMIN_NO_ACCESS
            callback_answer.show_alert = True
        elif isinstance(event, Message):
            await event.answer(texts.ADMIN_ONLY)
        return None
