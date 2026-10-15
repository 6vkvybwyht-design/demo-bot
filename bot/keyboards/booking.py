"""Клавіатури сценарію запису."""

from collections.abc import Sequence
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts
from bot.db.models import Master, Service
from bot.keyboards.callbacks import ANY_MASTER, DateCb, MasterCb, NavCb, ServiceCb, TimeCb
from bot.services.formatting import fmt_date, fmt_time, service_button


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


def times_kb(starts: Sequence[datetime], tz: ZoneInfo) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for start in starts:
        builder.button(text=fmt_time(start, tz), callback_data=TimeCb(ts=int(start.timestamp())))
    _with_back(builder).adjust(*([4] * ((len(starts) + 3) // 4)), 1)
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
