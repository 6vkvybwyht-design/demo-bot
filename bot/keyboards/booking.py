"""Клавіатури сценарію запису."""

from collections.abc import Sequence
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts
from bot.db.models import Master, Service
from bot.config import BusinessInfo
from bot.keyboards.callbacks import (
    ANY_MASTER,
    DateCb,
    MasterCb,
    MyBookingCb,
    NavCb,
    NoopCb,
    ServiceCb,
    TimeCb,
)
from bot.services.formatting import fmt_date, fmt_time, service_button
from bot.services.slots import DayPart, group_by_day_part


def _with_back(builder: InlineKeyboardBuilder) -> InlineKeyboardBuilder:
    builder.button(text=texts.BTN_BACK, callback_data=NavCb(action="back"))
    return builder


def back_kb() -> InlineKeyboardMarkup:
    return _with_back(InlineKeyboardBuilder()).as_markup()


def services_kb(services: Sequence[Service]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for service in services:
        builder.button(text=service_button(service), callback_data=ServiceCb(service_id=service.id))
    _with_back(builder).adjust(1)
    return builder.as_markup()


def masters_kb(masters: Sequence[Master]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_ANY_MASTER, callback_data=MasterCb(master_id=ANY_MASTER))
    for master in masters:
        builder.button(text=master.name, callback_data=MasterCb(master_id=master.id))
    _with_back(builder).adjust(1)
    return builder.as_markup()


def dates_kb(dates: Sequence[date]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for day in dates:
        builder.button(text=fmt_date(day), callback_data=DateCb(day=day.isoformat()))
    _with_back(builder).adjust(*([3] * ((len(dates) + 2) // 3)), 1)
    return builder.as_markup()


DAY_PART_LABELS = {
    DayPart.MORNING: texts.DAY_PART_MORNING,
    DayPart.AFTERNOON: texts.DAY_PART_AFTERNOON,
    DayPart.EVENING: texts.DAY_PART_EVENING,
}
TIMES_PER_ROW = 4


def times_kb(starts: Sequence[datetime], tz: ZoneInfo) -> InlineKeyboardMarkup:
    """Слоти, згруповані за частинами дня, із заголовком над кожною групою."""
    builder = InlineKeyboardBuilder()
    for part, items in group_by_day_part(starts, tz):
        builder.row(InlineKeyboardButton(text=DAY_PART_LABELS[part], callback_data=NoopCb().pack()))
        buttons = [
            InlineKeyboardButton(text=fmt_time(start, tz), callback_data=TimeCb(ts=int(start.timestamp())).pack())
            for start in items
        ]
        for i in range(0, len(buttons), TIMES_PER_ROW):
            builder.row(*buttons[i : i + TIMES_PER_ROW])
    builder.row(InlineKeyboardButton(text=texts.BTN_BACK, callback_data=NavCb(action="back").pack()))
    return builder.as_markup()


def confirm_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_CONFIRM, callback_data=NavCb(action="confirm"))
    _with_back(builder).adjust(1)
    return builder.as_markup()


def share_phone_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=texts.BTN_SHARE_PHONE, request_contact=True)],
            [KeyboardButton(text=texts.BTN_BACK)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def move_confirm_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_MOVE_CONFIRM, callback_data=NavCb(action="confirm"))
    _with_back(builder).adjust(1)
    return builder.as_markup()


def booking_card_kb(booking_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_CANCEL_BOOKING, callback_data=MyBookingCb(action="cancel", booking_id=booking_id)
    )
    builder.button(
        text=texts.BTN_MOVE_BOOKING, callback_data=MyBookingCb(action="move", booking_id=booking_id)
    )
    return builder.as_markup()


def cancel_confirm_kb(booking_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_CANCEL_YES, callback_data=MyBookingCb(action="cancel_yes", booking_id=booking_id)
    )
    builder.button(
        text=texts.BTN_CANCEL_NO, callback_data=MyBookingCb(action="cancel_no", booking_id=booking_id)
    )
    builder.adjust(1)
    return builder.as_markup()


def contacts_kb(info: BusinessInfo) -> InlineKeyboardMarkup | None:
    builder = InlineKeyboardBuilder()
    if info.map_url:
        builder.button(text=texts.BTN_MAP, url=info.map_url)
    if info.instagram:
        builder.button(text=texts.BTN_INSTAGRAM, url=info.instagram)
    builder.adjust(1)
    return builder.as_markup() if info.map_url or info.instagram else None


def reminder_kb(booking_id: int, *, can_cancel: bool) -> InlineKeyboardMarkup:
    """Кнопки в нагадуванні: «Буду» і (якщо ще не минув дедлайн) «Скасувати»."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_WILL_COME, callback_data=MyBookingCb(action="attend", booking_id=booking_id)
    )
    if can_cancel:
        builder.button(
            text=texts.BTN_CANCEL_BOOKING,
            callback_data=MyBookingCb(action="cancel", booking_id=booking_id),
        )
    return builder.as_markup()
