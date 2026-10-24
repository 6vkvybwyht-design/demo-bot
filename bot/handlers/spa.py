"""Сценарій спа (профілі з розділом spa): розділи послуг, картка з описом, гості,
додаткові послуги, групові події, аванс, подарункові сертифікати й правила відвідування.

Після вибору послуги запис іде спільними кроками з client.py (майстер, дата, час,
телефон), лише «Назад» і підтвердження — свої. Роутер підключається перед client.
"""

from datetime import UTC, datetime

from aiogram import Bot, F, Router, html
from aiogram.filters import Filter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject
from aiogram.utils.callback_answer import CallbackAnswer
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.config import BusinessConfig, Settings, SpaService
from bot.db import repo
from bot.db.models import Service, utcnow
from bot.handlers.client import (
    _not_menu_button,
    _render,
    _resources,
    ask_phone_or_confirm,
    menu_kb,
    show_dates,
    show_masters,
    show_times,
)
from bot.keyboards import admin as admin_kb
from bot.keyboards import booking as kb
from bot.keyboards import spa as spa_kb
from bot.keyboards.callbacks import (
    AddonCb,
    CategoryCb,
    CertCb,
    EventDateCb,
    GuestCb,
    NavCb,
    ServiceCb,
    SpaCardCb,
)
from bot.services import booking as booking_service
from bot.services import notifications
from bot.services import spa as spa_service
from bot.services.booking import EventFullError, SlotUnavailableError, SpaExtras
from bot.services.formatting import (
    booking_done_text,
    booking_held_text,
    guests_text,
    spa_card_text,
    summary_of,
)
from bot.services.pets import Offer
from bot.services.phone import normalize_contact_phone, parse_ua_phone
from bot.states import BookingStates, SpaStates

Event = Message | CallbackQuery


class IsSpa(Filter):
    """Хендлери спа працюють лише для профілю з розділом spa."""

    async def __call__(self, event: TelegramObject, business: BusinessConfig) -> bool:
        return business.spa is not None


router = Router(name="spa")
router.message.filter(IsSpa())
router.callback_query.filter(IsSpa())


async def _service_and_cfg(
    session: AsyncSession, state: FSMContext, business: BusinessConfig
) -> tuple[Service | None, SpaService | None]:
    service_id = (await state.get_data()).get("service_id")
    service = await session.get(Service, service_id) if service_id else None
    if service is None or not service.is_active:
        return None, None
    return service, business.spa.service(service.name)


async def _quote(
    session: AsyncSession, state: FSMContext, business: BusinessConfig, **overrides
) -> spa_service.Quote:
    """Розрахунок за поточними виборами (GuestsError — гостей поза лімітами)."""
    data = {**await state.get_data(), **overrides}
    service = await session.get(Service, data["service_id"])
    addons = [await session.get(Service, i) for i in data.get("addon_ids", [])]
    return spa_service.quote(
        business.spa, service, variant=data.get("variant"), counts=data.get("guests_counts"),
        addons=[a for a in addons if a is not None and a.is_active],
        max_first=data.get("event_free"),
    )


# --- Розділи й послуги --------------------------------------------------------


@router.message(F.text == texts.BTN_BOOK)
async def start_booking(message: Message, state: FSMContext, session: AsyncSession, business: BusinessConfig) -> None:
    await state.clear()
    await show_categories(message, state, session, business)


async def show_categories(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    services = await repo.get_active_services(session)
    categories = [
        c for c in business.spa.categories
        if spa_service.category_services(services, business.spa, c.key)
    ]
    if not categories:
        await state.clear()
        await _render(event, texts.NO_SERVICES.format(phone=business.business.phone))
        return
    await state.set_state(SpaStates.category)
    await _render(event, texts.CHOOSE_CATEGORY, spa_kb.categories_kb(categories))


@router.callback_query(SpaStates.category, CategoryCb.filter())
async def pick_category(
    callback: CallbackQuery, callback_data: CategoryCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    await state.set_data({"spa_category": callback_data.key})
    await show_category_services(callback, state, session, business)


@router.callback_query(SpaStates.category, NavCb.filter(F.action == "back"))
async def back_from_categories(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(texts.BACK_TO_MENU)


async def show_category_services(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    key = (await state.get_data()).get("spa_category")
    category = business.spa.category(key or "")
    services = spa_service.category_services(
        await repo.get_active_services(session), business.spa, key or ""
    )
    if category is None or not services:
        await show_categories(event, state, session, business)
        return
    await state.set_state(SpaStates.service)
    text = texts.PROGRESS.format(line=html.quote(category.label), prompt=texts.CHOOSE_SERVICE)
    await _render(event, text, spa_kb.services_kb(services, business.spa))


@router.callback_query(SpaStates.service, ServiceCb.filter())
async def pick_service(
    callback: CallbackQuery, callback_data: ServiceCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    service = await session.get(Service, callback_data.service_id)
    cfg = business.spa.service(service.name) if service is not None else None
    if not service or not service.is_active or cfg is None or cfg.category is None:
        await show_category_services(callback, state, session, business)
        return
    await state.update_data(service_id=service.id, details=False)
    await show_card(callback, state, session, business)


@router.callback_query(SpaStates.service, NavCb.filter(F.action == "back"))
async def back_from_services(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await show_categories(callback, state, session, business)


# --- Картка послуги -----------------------------------------------------------


async def show_card(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    service, cfg = await _service_and_cfg(session, state, business)
    if service is None or cfg is None:
        await show_category_services(event, state, session, business)
        return
    details = (await state.get_data()).get("details", False)
    await state.set_state(SpaStates.card)
    await _render(
        event,
        spa_card_text(service, cfg, business.spa, details=details),
        spa_kb.card_kb(cfg, details=details),
    )


@router.callback_query(SpaStates.card, SpaCardCb.filter(F.action.in_({"more", "less"})))
async def toggle_details(
    callback: CallbackQuery, callback_data: SpaCardCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    await state.update_data(details=callback_data.action == "more")
    await show_card(callback, state, session, business)


@router.callback_query(SpaStates.card, SpaCardCb.filter(F.action == "pick"))
async def pick_variant(
    callback: CallbackQuery, callback_data: SpaCardCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    service, cfg = await _service_and_cfg(session, state, business)
    if service is None or cfg is None or (cfg.variants and callback_data.variant >= len(cfg.variants)):
        await show_category_services(callback, state, session, business)
        return
    await state.update_data(
        variant=callback_data.variant if cfg.variants else None,
        guests_counts=cfg.guests.defaults() if cfg.guests else None,
        addon_ids=[], event_free=None, master_id=None,
    )
    if cfg.event is not None:
        await show_event_dates(callback, state, session, business)
    elif cfg.guests is not None and not cfg.guests.fixed:
        await show_guests(callback, state, session, business)
    else:
        await _after_guests(callback, state, session, business)


@router.callback_query(SpaStates.card, NavCb.filter(F.action == "back"))
async def back_from_card(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await show_category_services(callback, state, session, business)


# --- Гості --------------------------------------------------------------------


async def show_guests(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    service, cfg = await _service_and_cfg(session, state, business)
    if service is None or cfg is None or cfg.guests is None:
        await show_category_services(event, state, session, business)
        return
    data = await state.get_data()
    counts = data.get("guests_counts") or cfg.guests.defaults()
    try:
        quote = await _quote(session, state, business, guests_counts=counts)
    except spa_service.GuestsError:
        # Вільних місць стало менше, ніж обрано: починаємо з мінімуму
        counts = cfg.guests.defaults()
        quote = await _quote(session, state, business, guests_counts=counts)
    await state.update_data(guests_counts=counts)
    await state.set_state(SpaStates.guests)
    await _render(
        event,
        guests_text(cfg.guests, counts, quote, data.get("event_free")),
        spa_kb.guests_kb(cfg.guests, counts),
    )


@router.callback_query(SpaStates.guests, GuestCb.filter())
async def change_guests(
    callback: CallbackQuery, callback_data: GuestCb, callback_answer: CallbackAnswer,
    state: FSMContext, session: AsyncSession, business: BusinessConfig,
) -> None:
    counts = dict((await state.get_data()).get("guests_counts") or {})
    counts[callback_data.kind] = counts.get(callback_data.kind, 0) + (1 if callback_data.delta > 0 else -1)
    if counts[callback_data.kind] < 0:
        return  # ➖ на нулі — нічого не змінюємо
    try:
        await _quote(session, state, business, guests_counts=counts)
    except spa_service.GuestsError as exc:
        # Ліміт: кількість не змінюємо, пояснюємо чому
        callback_answer.text = texts.GUESTS_LIMIT.format(error=exc)
        callback_answer.show_alert = True
        return
    await state.update_data(guests_counts=counts)
    await show_guests(callback, state, session, business)


@router.callback_query(SpaStates.guests, NavCb.filter(F.action == "next"))
async def guests_done(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await _after_guests(callback, state, session, business)


@router.callback_query(SpaStates.guests, NavCb.filter(F.action == "back"))
async def back_from_guests(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    _, cfg = await _service_and_cfg(session, state, business)
    if cfg is not None and cfg.event is not None:
        await show_event_dates(callback, state, session, business)
    else:
        await show_card(callback, state, session, business)


# --- Додаткові послуги --------------------------------------------------------


async def _addons(session: AsyncSession, state: FSMContext, business: BusinessConfig) -> list[Service]:
    service, _ = await _service_and_cfg(session, state, business)
    if service is None:
        return []
    return spa_service.available_addons(service, business.spa, await repo.get_active_services(session))


async def _after_guests(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    if await _addons(session, state, business):
        await show_addons(event, state, session, business)
    else:
        await _finish_choice(event, state, session, business, back="before_addons")


async def show_addons(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    service, _ = await _service_and_cfg(session, state, business)
    addons = await _addons(session, state, business)
    if service is None or not addons:
        await show_category_services(event, state, session, business)
        return
    selected = set((await state.get_data()).get("addon_ids", []))
    await state.set_state(SpaStates.addons)
    await _render(
        event,
        texts.ASK_SPA_ADDONS.format(service=html.quote(service.name)),
        spa_kb.addons_kb(addons, selected, business.spa),
    )


@router.callback_query(SpaStates.addons, AddonCb.filter())
async def toggle_addon(
    callback: CallbackQuery, callback_data: AddonCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    addons = await _addons(session, state, business)
    addon = next((a for a in addons if a.id == callback_data.service_id), None)
    if addon is not None:
        selected = (await state.get_data()).get("addon_ids", [])
        await state.update_data(addon_ids=spa_service.toggle_addon(selected, addon, business.spa, addons))
    await show_addons(callback, state, session, business)


@router.callback_query(SpaStates.addons, NavCb.filter(F.action == "next"))
async def addons_done(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await _finish_choice(callback, state, session, business, back="addons")


@router.callback_query(SpaStates.addons, NavCb.filter(F.action == "back"))
async def back_from_addons(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await _back_before_addons(callback, state, session, business)


async def _back_before_addons(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    _, cfg = await _service_and_cfg(session, state, business)
    if cfg is not None and (cfg.event is not None or cfg.guests is not None and not cfg.guests.fixed):
        await show_guests(event, state, session, business)
    else:
        await show_card(event, state, session, business)


# --- Підсумок вибору → майстер / дата / телефон --------------------------------


async def _finish_choice(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig, *, back: str
) -> None:
    """Рахуємо тривалість, ціну, аванс і ресурси, далі — спільні кроки запису.

    `back` — куди вести «Назад» із наступного кроку: addons або before_addons (гості чи картка).
    """
    service, cfg = await _service_and_cfg(session, state, business)
    if service is None or cfg is None:
        await show_category_services(event, state, session, business)
        return
    try:
        quote = await _quote(session, state, business)
    except spa_service.GuestsError:
        await show_guests(event, state, session, business)
        return
    await state.update_data(
        duration_min=quote.duration_min, price=quote.price, price_from=quote.price_from,
        deposit=quote.deposit, guests=quote.guests, guests_details=quote.guests_details,
        resource_plan=spa_service.plan_to_data(quote.plan), staff_count=quote.staff_count,
        spa_back=back,
    )
    if cfg.event is not None:
        await ask_phone_or_confirm(event, state, session, business)
    elif spa_service.is_staffless(business.spa, service.name):
        await state.update_data(master_id=None)
        await show_dates(event, state, session, business)
    else:
        await show_masters(event, state, session, business)


async def _back_to_choice(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    """«Назад» із першого спільного кроку: до додаткових послуг, гостей чи картки."""
    if (await state.get_data()).get("spa_back") == "addons":
        await show_addons(event, state, session, business)
    else:
        await _back_before_addons(event, state, session, business)


async def _is_event(session: AsyncSession, state: FSMContext, business: BusinessConfig) -> bool:
    _, cfg = await _service_and_cfg(session, state, business)
    return cfg is not None and cfg.event is not None


@router.callback_query(BookingStates.master, NavCb.filter(F.action == "back"))
async def back_from_master(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await _back_to_choice(callback, state, session, business)


@router.callback_query(BookingStates.date, NavCb.filter(F.action == "back"))
async def back_from_date(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    service, _ = await _service_and_cfg(session, state, business)
    if service is not None and spa_service.is_staffless(business.spa, service.name):
        await _back_to_choice(callback, state, session, business)
    else:
        await show_masters(callback, state, session, business)


@router.callback_query(BookingStates.confirm, NavCb.filter(F.action == "back"))
async def back_from_confirm(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    if await _is_event(session, state, business):
        await _back_to_choice(callback, state, session, business)
    else:
        await show_times(callback, state, session, business)


@router.message(BookingStates.phone, F.text == texts.BTN_BACK)
async def phone_back(
    message: Message, state: FSMContext, session: AsyncSession, business: BusinessConfig,
    settings: Settings,
) -> None:
    if await _is_event(session, state, business):
        await message.answer(texts.BACK_TO_MENU, reply_markup=menu_kb(settings, business))
        await _back_to_choice(message, state, session, business)
        return
    await message.answer(texts.BACK_TO_TIMES, reply_markup=menu_kb(settings, business))
    await show_times(message, state, session, business)


# --- Групова подія ------------------------------------------------------------


async def show_event_dates(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig,
    notice: str | None = None,
) -> None:
    service, cfg = await _service_and_cfg(session, state, business)
    if service is None or cfg is None or cfg.event is None:
        await show_category_services(event, state, session, business)
        return
    dates = await spa_service.event_dates(session, service, cfg, business.booking, utcnow())
    await state.set_state(SpaStates.event_date)
    if not dates:
        text = texts.NO_EVENT_DATES.format(
            days=business.booking.horizon_days, phone=business.business.phone
        )
        await _render(event, text, kb.back_kb())
        return
    text = texts.PROGRESS.format(line=html.quote(service.name), prompt=texts.CHOOSE_EVENT_DATE)
    if notice:
        text = f"{notice}\n\n{text}"
    await _render(event, text, spa_kb.event_dates_kb(dates, business.booking.tz))


@router.callback_query(SpaStates.event_date, EventDateCb.filter())
async def pick_event_date(
    callback: CallbackQuery, callback_data: EventDateCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    service, cfg = await _service_and_cfg(session, state, business)
    start = datetime.fromtimestamp(callback_data.ts, tz=UTC)
    dates = dict(await spa_service.event_dates(session, service, cfg, business.booking, utcnow())) if cfg else {}
    if start not in dates:
        await show_event_dates(callback, state, session, business, notice=texts.EVENT_FULL)
        return
    await state.update_data(
        start=start.isoformat(), day=start.astimezone(business.booking.tz).date().isoformat(),
        event_free=dates[start],
    )
    await show_guests(callback, state, session, business)


@router.callback_query(SpaStates.event_date, NavCb.filter(F.action == "back"))
async def back_from_event_dates(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await show_card(callback, state, session, business)


# --- Підтвердження й аванс ----------------------------------------------------


@router.callback_query(BookingStates.confirm, NavCb.filter(F.action == "confirm"))
async def confirm_booking(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig,
    bot: Bot, settings: Settings,
) -> None:
    data = await state.get_data()
    service, cfg = await _service_and_cfg(session, state, business)
    if service is None or cfg is None:
        await state.clear()
        await callback.message.edit_text(texts.STALE_BUTTON)
        return
    client = await booking_service.get_or_create_client(
        session, callback.from_user.id, callback.from_user.full_name
    )
    start = datetime.fromisoformat(data["start"])
    addons = [await session.get(Service, i) for i in data.get("addon_ids", [])]
    addon_offers = [
        (a.id, Offer((business.spa.service(a.name) or SpaService()).extra_min, a.price))
        for a in addons if a is not None
    ]
    extras = SpaExtras(
        resources=_resources(data, business), guests=data.get("guests"),
        guests_details=data.get("guests_details"), deposit=data.get("deposit"),
        hold_min=business.spa.deposit.hold_min, staff_count=data.get("staff_count", 1),
    )
    common = dict(
        client=client, service=service, start=start, rules=business.booking, now=utcnow(),
        reminders=business.reminders, price=data["price"], addons=addon_offers, extras=extras,
    )
    try:
        if cfg.event is not None:
            booking = await booking_service.create_event_booking(
                session, capacity=cfg.event.capacity, duration_min=data["duration_min"], **common
            )
        else:
            booking = await booking_service.create_booking(
                session, master_id=data.get("master_id"), duration_min=data["duration_min"],
                price_from=data.get("price_from", False), **common,
            )
    except SlotUnavailableError as exc:
        if cfg.event is not None:
            notice = texts.EVENT_FULL if isinstance(exc, EventFullError) else None
            await show_event_dates(callback, state, session, business, notice=notice)
        else:
            await show_times(callback, state, session, business, notice=texts.SLOT_TAKEN)
        return

    await state.clear()
    booking = await notifications.load_booking(session, booking.id)
    summary = summary_of(booking, business)
    tz = business.booking.tz
    owner_markup = None
    if booking.deposit_pending:
        await callback.message.edit_text(
            booking_held_text(
                booking=booking, summary=summary, address=business.business.address, tz=tz,
                demo_mode=settings.demo_mode,
            ),
            reply_markup=kb.booking_card_kb(
                booking.id, can_move=False, pay_amount=booking.deposit if settings.demo_mode else None
            ),
        )
        template = texts.NOTIFY_NEW_PENDING
        owner_markup = admin_kb.deposit_kb(booking.id)
    else:
        reminders = booking_service.planned_reminders(
            booking.start_dt, booking.created_at, business.reminders
        )
        await callback.message.edit_text(
            booking_done_text(summary=summary, address=business.business.address, reminders=reminders)
        )
        template = texts.NOTIFY_NEW
    owner_text = await notifications.notify_staff(
        bot, session, booking_id=booking.id, template=template, admin_ids=settings.admin_ids,
        business=business, demo_mode=settings.demo_mode, actor_id=callback.from_user.id,
        reply_markup=owner_markup,
    )
    if settings.demo_mode:
        # Відвідувач демо одразу бачить і другу сторону — сповіщення власника з кнопками
        await callback.message.answer(
            texts.DEMO_OWNER_VIEW.format(notification=owner_text), reply_markup=owner_markup
        )


# --- Правила відвідування й сертифікати ---------------------------------------


@router.message(F.text == texts.BTN_RULES)
async def show_rules(message: Message, state: FSMContext, business: BusinessConfig) -> None:
    await state.clear()
    await message.answer(business.spa.visit_rules)


@router.message(F.text == texts.BTN_CERTIFICATE)
async def start_certificate(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_cert_start(message, state)


async def show_cert_start(event: Event, state: FSMContext) -> None:
    await state.set_state(SpaStates.cert)
    await _render(event, texts.CERT_START, spa_kb.cert_start_kb())


@router.callback_query(SpaStates.cert, NavCb.filter(F.action == "back"))
async def back_from_cert(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(texts.BACK_TO_MENU)


@router.callback_query(SpaStates.cert, CertCb.filter(F.action == "amount"))
async def cert_amounts(callback: CallbackQuery, state: FSMContext, business: BusinessConfig) -> None:
    await state.set_state(SpaStates.cert_amount)
    await callback.message.edit_text(
        texts.CERT_CHOOSE_AMOUNT, reply_markup=spa_kb.cert_amounts_kb(business.spa.certificate_amounts)
    )


@router.callback_query(SpaStates.cert_amount, CertCb.filter(F.action == "sum"))
async def cert_pick_amount(callback: CallbackQuery, callback_data: CertCb, state: FSMContext) -> None:
    await state.update_data(cert_item=texts.CERT_ITEM_AMOUNT.format(amount=callback_data.amount))
    await show_cert_confirm(callback, state)


async def show_cert_categories(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    services = await repo.get_active_services(session)
    categories = [
        c for c in business.spa.categories
        if spa_service.category_services(services, business.spa, c.key)
    ]
    await state.set_state(SpaStates.cert_category)
    await _render(event, texts.CERT_CHOOSE_SERVICE, spa_kb.categories_kb(categories))


@router.callback_query(SpaStates.cert, CertCb.filter(F.action == "service"))
async def cert_services_start(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await show_cert_categories(callback, state, session, business)


@router.callback_query(SpaStates.cert_category, CategoryCb.filter())
async def cert_pick_category(
    callback: CallbackQuery, callback_data: CategoryCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    services = spa_service.category_services(
        await repo.get_active_services(session), business.spa, callback_data.key
    )
    if not services:
        await show_cert_categories(callback, state, session, business)
        return
    await state.set_state(SpaStates.cert_service)
    await callback.message.edit_text(
        texts.CERT_CHOOSE_SERVICE, reply_markup=spa_kb.services_kb(services, business.spa)
    )


@router.callback_query(SpaStates.cert_service, ServiceCb.filter())
async def cert_pick_service(
    callback: CallbackQuery, callback_data: ServiceCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    service = await session.get(Service, callback_data.service_id)
    if service is None or not service.is_active:
        await show_cert_categories(callback, state, session, business)
        return
    await state.update_data(cert_item=texts.CERT_ITEM_SERVICE.format(name=service.name))
    await show_cert_confirm(callback, state)


@router.callback_query(SpaStates.cert_service, NavCb.filter(F.action == "back"))
async def back_from_cert_service(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await show_cert_categories(callback, state, session, business)


@router.callback_query(
    StateFilter(SpaStates.cert_category, SpaStates.cert_amount, SpaStates.cert_confirm),
    NavCb.filter(F.action == "back"),
)
async def back_to_cert_start(callback: CallbackQuery, state: FSMContext) -> None:
    await show_cert_start(callback, state)


async def show_cert_confirm(event: Event, state: FSMContext) -> None:
    item = (await state.get_data())["cert_item"]
    await state.set_state(SpaStates.cert_confirm)
    await _render(event, texts.CERT_CONFIRM.format(item=html.quote(item)), spa_kb.cert_confirm_kb())


@router.callback_query(SpaStates.cert_confirm, NavCb.filter(F.action == "confirm"))
async def cert_send(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig,
    bot: Bot, settings: Settings,
) -> None:
    client = await booking_service.get_client(session, callback.from_user.id)
    if client is None or not client.phone:
        await state.set_state(SpaStates.cert_phone)
        await callback.message.edit_text(texts.CERT_CONFIRM.format(
            item=html.quote((await state.get_data())["cert_item"])
        ))
        await callback.message.answer(texts.CERT_ASK_PHONE, reply_markup=kb.share_phone_kb())
        return
    await callback.message.edit_text(texts.CERT_SENT)
    await _send_cert_request(callback.message, state, session, business, bot, settings, callback.from_user.id)


@router.message(SpaStates.cert_phone, F.contact)
async def cert_phone_contact(
    message: Message, state: FSMContext, session: AsyncSession, business: BusinessConfig,
    bot: Bot, settings: Settings,
) -> None:
    if message.contact.user_id != message.from_user.id:
        await message.answer(texts.PHONE_NOT_OWN, reply_markup=kb.share_phone_kb())
        return
    await _cert_phone_received(
        message, normalize_contact_phone(message.contact.phone_number), state, session, business,
        bot, settings,
    )


@router.message(SpaStates.cert_phone, F.text == texts.BTN_BACK)
async def cert_phone_back(message: Message, state: FSMContext, business: BusinessConfig, settings: Settings) -> None:
    await message.answer(texts.BACK_TO_MENU, reply_markup=menu_kb(settings, business))
    await show_cert_start(message, state)


@router.message(SpaStates.cert_phone, F.text.func(_not_menu_button))
async def cert_phone_text(
    message: Message, state: FSMContext, session: AsyncSession, business: BusinessConfig,
    bot: Bot, settings: Settings,
) -> None:
    phone = parse_ua_phone(message.text)
    if phone is None:
        await message.answer(texts.PHONE_INVALID, reply_markup=kb.share_phone_kb())
        return
    await _cert_phone_received(message, phone, state, session, business, bot, settings)


async def _cert_phone_received(
    message: Message, phone: str, state: FSMContext, session: AsyncSession,
    business: BusinessConfig, bot: Bot, settings: Settings,
) -> None:
    await booking_service.save_client_phone(
        session, message.from_user.id, message.from_user.full_name, phone
    )
    await message.answer(texts.CERT_SENT, reply_markup=menu_kb(settings, business))
    await _send_cert_request(message, state, session, business, bot, settings, message.from_user.id)


async def _send_cert_request(
    message: Message, state: FSMContext, session: AsyncSession, business: BusinessConfig,
    bot: Bot, settings: Settings, user_id: int,
) -> None:
    item = (await state.get_data())["cert_item"]
    await state.clear()
    client = await booking_service.get_client(session, user_id)
    text = await notifications.notify_certificate(
        bot, item=item, client=client, admin_ids=settings.admin_ids
    )
    if settings.demo_mode:
        await message.answer(texts.DEMO_OWNER_VIEW.format(notification=text))
