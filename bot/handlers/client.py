"""Хендлери клієнтського сценарію: /start і запис на послугу."""

from datetime import UTC, date, datetime

from aiogram import F, Router, html
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.config import BusinessConfig
from bot.db import repo
from bot.db.models import Master, Service, utcnow
from bot.keyboards import booking as kb
from bot.keyboards.callbacks import ANY_MASTER, DateCb, MasterCb, NavCb, ServiceCb, TimeCb
from bot.keyboards.main_menu import main_menu_kb
from bot.services import booking as booking_service
from bot.services import slots
from bot.services.booking import SlotUnavailableError
from bot.services.formatting import booking_summary, fmt_date, fmt_time
from bot.states import BookingStates

router = Router(name="client")

Event = Message | CallbackQuery


@router.message(CommandStart())
async def cmd_start(message: Message, business: BusinessConfig, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        texts.WELCOME.format(name=html.quote(business.business.name)),
        reply_markup=main_menu_kb(),
    )


# --- Показ кроків -------------------------------------------------------------


async def _render(event: Event, text: str, reply_markup=None) -> None:
    """Для кнопки редагуємо поточне повідомлення, для тексту — надсилаємо нове."""
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=reply_markup)
    else:
        await event.answer(text, reply_markup=reply_markup)


async def _load_choice(session: AsyncSession, state: FSMContext) -> tuple[Service, int | None]:
    data = await state.get_data()
    service = await session.get(Service, data["service_id"])
    return service, data.get("master_id")


async def show_services(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    services = await repo.get_active_services(session)
    if not services:
        await state.clear()
        await _render(event, texts.NO_SERVICES.format(phone=business.business.phone))
        return
    await state.set_state(BookingStates.service)
    await _render(event, texts.CHOOSE_SERVICE, kb.services_kb(services))


async def show_masters(event: Event, state: FSMContext, session: AsyncSession) -> None:
    service, _ = await _load_choice(session, state)
    masters = await slots.masters_for_service(session, service.id)
    await state.set_state(BookingStates.master)
    if not masters:
        await _render(event, texts.NO_MASTERS, kb.back_kb())
        return
    await _render(
        event, texts.CHOOSE_MASTER.format(service=html.quote(service.name)), kb.masters_kb(masters)
    )


async def show_dates(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    service, master_id = await _load_choice(session, state)
    rules = business.booking
    dates = await slots.get_available_dates(
        session, service=service, master_id=master_id, rules=rules, now=utcnow()
    )
    await state.set_state(BookingStates.date)
    if not dates:
        text = texts.NO_DATES.format(days=rules.horizon_days, phone=business.business.phone)
        await _render(event, text, kb.back_kb())
        return
    await _render(event, texts.CHOOSE_DATE, kb.dates_kb(dates))


async def _free_starts(
    session: AsyncSession, service: Service, master_id: int | None, day: date, business: BusinessConfig
) -> list[datetime]:
    common = dict(service=service, day=day, rules=business.booking, now=utcnow())
    if master_id is None:
        return list(await slots.get_free_slots_any_master(session, **common))
    return await slots.get_free_slots(session, master_id=master_id, **common)


async def show_times(
    event: Event,
    state: FSMContext,
    session: AsyncSession,
    business: BusinessConfig,
    notice: str | None = None,
) -> None:
    service, master_id = await _load_choice(session, state)
    day = date.fromisoformat((await state.get_data())["day"])
    starts = await _free_starts(session, service, master_id, day, business)
    if not starts:
        # Поки клієнт обирав, вільний час на цю дату закінчився
        await show_dates(event, state, session, business)
        return
    await state.set_state(BookingStates.time)
    text = texts.CHOOSE_TIME.format(date=fmt_date(day))
    if notice:
        text = f"{notice}\n\n{text}"
    await _render(event, text, kb.times_kb(starts, business.booking.tz))


async def show_confirm(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    service, master_id = await _load_choice(session, state)
    start = datetime.fromisoformat((await state.get_data())["start"])
    master = await session.get(Master, master_id) if master_id else None
    summary = booking_summary(
        service=service,
        master_name=master.name if master else None,
        start=start,
        tz=business.booking.tz,
    )
    await state.set_state(BookingStates.confirm)
    await _render(event, texts.CONFIRM.format(summary=summary), kb.confirm_kb())


# --- Кроки запису -------------------------------------------------------------


@router.message(F.text == texts.BTN_BOOK)
async def start_booking(
    message: Message, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await state.clear()
    await show_services(message, state, session, business)


@router.callback_query(BookingStates.service, ServiceCb.filter())
async def pick_service(
    callback: CallbackQuery, callback_data: ServiceCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    service = await session.get(Service, callback_data.service_id)
    if service is None or not service.is_active:
        await show_services(callback, state, session, business)
        return
    await state.update_data(service_id=service.id)
    await show_masters(callback, state, session)


@router.callback_query(BookingStates.master, MasterCb.filter())
async def pick_master(
    callback: CallbackQuery, callback_data: MasterCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    master_id = None if callback_data.master_id == ANY_MASTER else callback_data.master_id
    await state.update_data(master_id=master_id)
    await show_dates(callback, state, session, business)


@router.callback_query(BookingStates.date, DateCb.filter())
async def pick_date(
    callback: CallbackQuery, callback_data: DateCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    await state.update_data(day=callback_data.day)
    await show_times(callback, state, session, business, notice=None)


@router.callback_query(BookingStates.time, TimeCb.filter())
async def pick_time(
    callback: CallbackQuery, callback_data: TimeCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    start = datetime.fromtimestamp(callback_data.ts, tz=UTC)
    await state.update_data(start=start.isoformat())

    client = await booking_service.get_client(session, callback.from_user.id)
    if client is not None and client.phone:
        await show_confirm(callback, state, session, business)
        return

    tz = business.booking.tz
    await state.set_state(BookingStates.phone)
    await callback.message.edit_text(
        texts.TIME_PICKED.format(date=fmt_date(start.astimezone(tz).date()), time=fmt_time(start, tz))
    )
    await callback.message.answer(texts.ASK_PHONE, reply_markup=kb.share_phone_kb())


@router.message(BookingStates.phone, F.contact)
async def got_phone(
    message: Message, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    if message.contact.user_id != message.from_user.id:
        await message.answer(texts.PHONE_NOT_OWN, reply_markup=kb.share_phone_kb())
        return
    await booking_service.save_client_phone(
        session, message.from_user.id, message.from_user.full_name, message.contact.phone_number
    )
    await message.answer(texts.PHONE_SAVED, reply_markup=main_menu_kb())
    await show_confirm(message, state, session, business)


@router.message(BookingStates.phone, F.text == texts.BTN_BACK)
async def phone_back(
    message: Message, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await message.answer(texts.BACK_TO_TIMES, reply_markup=main_menu_kb())
    await show_times(message, state, session, business)


@router.message(BookingStates.phone)
async def phone_expected(message: Message) -> None:
    await message.answer(texts.ASK_PHONE, reply_markup=kb.share_phone_kb())


@router.callback_query(BookingStates.confirm, NavCb.filter(F.action == "confirm"))
async def confirm_booking(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    service, master_id = await _load_choice(session, state)
    start = datetime.fromisoformat((await state.get_data())["start"])
    client = await booking_service.get_or_create_client(
        session, callback.from_user.id, callback.from_user.full_name
    )
    try:
        booking = await booking_service.create_booking(
            session, client=client, service=service, master_id=master_id,
            start=start, rules=business.booking, now=utcnow(),
        )
    except SlotUnavailableError:
        await show_times(callback, state, session, business, notice=texts.SLOT_TAKEN)
        return

    await state.clear()
    master = await session.get(Master, booking.master_id)
    summary = booking_summary(
        service=service, master_name=master.name, start=booking.start_dt, tz=business.booking.tz
    )
    await callback.message.edit_text(
        texts.BOOKING_DONE.format(summary=summary, address=html.quote(business.business.address))
    )


# --- Назад --------------------------------------------------------------------


@router.callback_query(BookingStates.service, NavCb.filter(F.action == "back"))
async def back_from_service(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(texts.BACK_TO_MENU)


@router.callback_query(BookingStates.master, NavCb.filter(F.action == "back"))
async def back_from_master(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await show_services(callback, state, session, business)


@router.callback_query(BookingStates.date, NavCb.filter(F.action == "back"))
async def back_from_date(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await show_masters(callback, state, session)


@router.callback_query(BookingStates.time, NavCb.filter(F.action == "back"))
async def back_from_time(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await show_dates(callback, state, session, business)


@router.callback_query(BookingStates.confirm, NavCb.filter(F.action == "back"))
async def back_from_confirm(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await show_times(callback, state, session, business)
