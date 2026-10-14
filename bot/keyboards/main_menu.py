from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from bot import texts


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=texts.BTN_BOOK)],
            [
                KeyboardButton(text=texts.BTN_MY_BOOKINGS),
                KeyboardButton(text=texts.BTN_CONTACTS),
            ],
        ],
        resize_keyboard=True,
        input_field_placeholder=texts.MENU_PLACEHOLDER,
    )
