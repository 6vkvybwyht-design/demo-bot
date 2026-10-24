"""Підставна сесія Bot API: записує запити замість відправки в Telegram."""

from datetime import datetime
from itertools import count

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import EditMessageText, GetMe, SendMessage, TelegramMethod
from aiogram.types import (
    CallbackQuery,
    Chat,
    Contact,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
    Update,
    User,
)

BOT_USER = User(id=42, is_bot=True, first_name="Demo", username="demo_bot")


class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod] = []
        self.blocked_chats: set[int] = set()  # чати, що заблокували бота
        self._ids = count(1000)

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if isinstance(method, GetMe):
            return BOT_USER
        if isinstance(method, SendMessage) and method.chat_id in self.blocked_chats:
            raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
        if isinstance(method, (SendMessage, EditMessageText)):
            return Message(
                message_id=next(self._ids),
                date=datetime.now(),
                chat=Chat(id=method.chat_id or 1, type="private"),
                from_user=BOT_USER,
                text=method.text,
                # У відповіді Telegram повертає лише inline-клавіатуру
                reply_markup=method.reply_markup
                if isinstance(method.reply_markup, InlineKeyboardMarkup)
                else None,
            )
        return True

    async def stream_content(self, *args, **kwargs):
        raise NotImplementedError

    async def close(self) -> None:
        pass


class TelegramUser:
    """Імітує користувача: надсилає повідомлення й тисне кнопки."""

    def __init__(self, dp: Dispatcher, user_id: int = 777, name: str = "Іван") -> None:
        self.dp = dp
        self.session = FakeSession()
        self.bot = Bot(
            token="42:TEST",
            session=self.session,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self.user = User(id=user_id, is_bot=False, first_name=name)
        self.chat = Chat(id=user_id, type="private")
        self._ids = count(1)

    def _message(self, **kwargs) -> Message:
        return Message(
            message_id=next(self._ids), date=datetime.now(), chat=self.chat,
            from_user=self.user, **kwargs,
        )

    async def _feed(self, **kwargs) -> list[TelegramMethod]:
        before = len(self.session.requests)
        await self.dp.feed_update(self.bot, Update(update_id=next(self._ids), **kwargs))
        return self.session.requests[before:]

    async def send(self, text: str) -> list[TelegramMethod]:
        return await self._feed(message=self._message(text=text))

    async def share_contact(self, phone: str, user_id: int | None = None) -> list[TelegramMethod]:
        contact = Contact(
            phone_number=phone, first_name=self.user.first_name,
            user_id=self.user.id if user_id is None else user_id,
        )
        return await self._feed(message=self._message(contact=contact))

    async def click(self, callback_data: str) -> list[TelegramMethod]:
        message = Message(
            message_id=1, date=datetime.now(), chat=self.chat, from_user=BOT_USER, text="..."
        )
        query = CallbackQuery(
            id=str(next(self._ids)), from_user=self.user, chat_instance="test",
            message=message, data=callback_data,
        )
        return await self._feed(callback_query=query)

    # --- Розбір відповідей бота ---

    def _own(self) -> list[TelegramMethod]:
        """Запити в чат самого користувача (без сповіщень іншим людям)."""
        return [r for r in self.session.requests if getattr(r, "chat_id", self.chat.id) == self.chat.id]

    def sent_to(self, chat_id: int) -> list[str]:
        """Тексти, які бот надіслав у чат `chat_id` під час дій цього користувача."""
        return [r.text for r in self.session.requests if isinstance(r, SendMessage) and r.chat_id == chat_id]

    def all_texts(self) -> list[str]:
        """Усі тексти, надіслані й відредаговані в чаті користувача."""
        return [r.text for r in self._own() if isinstance(r, (SendMessage, EditMessageText))]

    def last_text(self) -> str:
        texts = [r.text for r in self._own() if isinstance(r, (SendMessage, EditMessageText))]
        return texts[-1]

    def last_inline(self) -> InlineKeyboardMarkup:
        for request in reversed(self._own()):
            markup = getattr(request, "reply_markup", None)
            if isinstance(markup, InlineKeyboardMarkup):
                return markup
        raise AssertionError("бот не надсилав inline-кнопок")

    def last_reply_keyboard(self) -> ReplyKeyboardMarkup:
        for request in reversed(self._own()):
            markup = getattr(request, "reply_markup", None)
            if isinstance(markup, ReplyKeyboardMarkup):
                return markup
        raise AssertionError("бот не надсилав звичайної клавіатури")

    def buttons(self) -> list[tuple[str, str]]:
        return [(b.text, b.callback_data) for row in self.last_inline().inline_keyboard for b in row]

    def slot_buttons(self) -> list[tuple[str, str]]:
        """Кнопки часу (без заголовків частин дня й «Назад»)."""
        return [(label, data) for label, data in self.buttons() if data.startswith("tm:")]

    def button(self, text: str) -> str:
        for label, data in self.buttons():
            if label == text:
                return data
        raise AssertionError(f"немає кнопки «{text}» серед {[t for t, _ in self.buttons()]}")
