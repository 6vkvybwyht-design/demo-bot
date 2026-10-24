from collections.abc import Sequence

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from bot import texts


def main_menu_kb(demo_mode: bool = False, extra: Sequence[str] = ()) -> ReplyKeyboardMarkup:
    """Головне меню. `extra` — кнопки ніші (спа: сертифікати, правила відвідування)."""
    keyboard = [
        [KeyboardButton(text=texts.BTN_BOOK)],
        [
            KeyboardButton(text=texts.BTN_MY_BOOKINGS),
            KeyboardButton(text=texts.BTN_CONTACTS),
        ],
    ]
    if extra:
        keyboard.append([KeyboardButton(text=text) for text in extra])
    if demo_mode:
        keyboard.append([KeyboardButton(text=texts.BTN_DEMO_OWNER)])
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        input_field_placeholder=texts.MENU_PLACEHOLDER,
    )
