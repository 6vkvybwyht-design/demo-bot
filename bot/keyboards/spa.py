"""Клавіатури спа: розділи, картка послуги, гості, додаткові послуги, події, сертифікати."""

from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts
from bot.config import SpaCategory, SpaConfig, SpaGuests, SpaService
from bot.db.models import Service
from bot.keyboards.callbacks import (
    AddonCb,
    CategoryCb,
    CertCb,
    EventDateCb,
    GuestCb,
    NavCb,
    NoopCb,
    ServiceCb,
    SpaCardCb,
)
from bot.services.formatting import (
    event_date_button,
    fmt_price,
    spa_addon_button,
    spa_service_button,
)


def _back(builder: InlineKeyboardBuilder) -> InlineKeyboardBuilder:
    builder.button(text=texts.BTN_BACK, callback_data=NavCb(action="back"))
    return builder


def categories_kb(categories: Sequence[SpaCategory]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.button(text=category.label, callback_data=CategoryCb(key=category.key))
    _back(builder).adjust(1)
    return builder.as_markup()


def services_kb(services: Sequence[Service], spa: SpaConfig) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for service in services:
        builder.button(
            text=spa_service_button(service, spa), callback_data=ServiceCb(service_id=service.id)
        )
    _back(builder).adjust(1)
    return builder.as_markup()


def card_kb(cfg: SpaService, *, details: bool) -> InlineKeyboardMarkup:
    """Варіанти тривалості (або «Обрати»), «Детальніше» / «Коротше», «Назад»."""
    builder = InlineKeyboardBuilder()
    if cfg.variants:
        for i, variant in enumerate(cfg.variants):
            builder.button(
                text=texts.SPA_VARIANT_BUTTON.format(
                    duration=variant.duration_min, price=fmt_price(variant.price)
                ),
                callback_data=SpaCardCb(action="pick", variant=i),
            )
    else:
        builder.button(text=texts.BTN_SPA_PICK, callback_data=SpaCardCb(action="pick"))
    if cfg.full:
        builder.button(
            text=texts.BTN_SPA_LESS if details else texts.BTN_SPA_MORE,
            callback_data=SpaCardCb(action="less" if details else "more"),
        )
    _back(builder).adjust(1)
    return builder.as_markup()


def guests_kb(guests: SpaGuests, counts: dict[str, int]) -> InlineKeyboardMarkup:
    """Для кожного виду гостей: ➖ · «Дорослі: 2» · ➕; далі «Далі» і «Назад»."""
    builder = InlineKeyboardBuilder()
    for kind in guests.kinds:
        builder.row(
            InlineKeyboardButton(
                text=texts.BTN_MINUS, callback_data=GuestCb(kind=kind.key, delta=-1).pack()
            ),
            InlineKeyboardButton(
                text=texts.GUEST_BUTTON.format(label=kind.label, count=counts.get(kind.key, 0)),
                callback_data=NoopCb().pack(),
            ),
            InlineKeyboardButton(
                text=texts.BTN_PLUS, callback_data=GuestCb(kind=kind.key, delta=1).pack()
            ),
        )
    builder.row(
        InlineKeyboardButton(text=texts.BTN_ADDONS_NEXT, callback_data=NavCb(action="next").pack())
    )
    builder.row(InlineKeyboardButton(text=texts.BTN_BACK, callback_data=NavCb(action="back").pack()))
    return builder.as_markup()


def addons_kb(addons: Sequence[Service], selected: set[int], spa: SpaConfig) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for addon in addons:
        mark = texts.ADDON_SELECTED if addon.id in selected else texts.ADDON_UNSELECTED
        builder.button(
            text=mark + spa_addon_button(addon, spa), callback_data=AddonCb(service_id=addon.id)
        )
    builder.button(text=texts.BTN_ADDONS_NEXT, callback_data=NavCb(action="next"))
    _back(builder).adjust(1)
    return builder.as_markup()


def event_dates_kb(dates: Sequence[tuple[datetime, int]], tz: ZoneInfo) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for start, free in dates:
        builder.button(
            text=event_date_button(start, free, tz), callback_data=EventDateCb(ts=int(start.timestamp()))
        )
    _back(builder).adjust(1)
    return builder.as_markup()


# --- Сертифікати --------------------------------------------------------------


def cert_start_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_CERT_SERVICE, callback_data=CertCb(action="service"))
    builder.button(text=texts.BTN_CERT_AMOUNT, callback_data=CertCb(action="amount"))
    _back(builder).adjust(2, 1)
    return builder.as_markup()


def cert_amounts_kb(amounts: Sequence[int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for amount in amounts:
        builder.button(
            text=texts.CERT_AMOUNT_BUTTON.format(amount=amount),
            callback_data=CertCb(action="sum", amount=amount),
        )
    _back(builder).adjust(*([2] * (len(amounts) // 2)), *([1] if len(amounts) % 2 else []), 1)
    return builder.as_markup()


def cert_confirm_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_CERT_SEND, callback_data=NavCb(action="confirm"))
    _back(builder).adjust(1)
    return builder.as_markup()
