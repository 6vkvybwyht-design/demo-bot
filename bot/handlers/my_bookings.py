"""«Мої записи»: перегляд, скасування й перенесення записів клієнтом."""

from datetime import UTC, datetime

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.config import BusinessConfig, Settings
from bot.db.models import Booking, utcnow
from bot.handlers.client import show_dates, show_times
from bot.keyboards import booking as kb
from bot.keyboards.callbacks import MyBookingCb, NavCb, TimeCb
from bot.services import booking as booking_service
from bot.services import notifications
from bot.services.reminders import reminder_text
from bot.services.booking import (
    BookingNotFoundError,
    ChangeDeadlinePassedError,
    SlotUnavailableError,
)
from bot.services.formatting import booking_moved_text, booking_summary, fmt_when
from bot.states import RescheduleStates

router = Router(name="my_bookings")


def _summary(booking: Booking, business: BusinessConfig) -> str:
    return booking_summary(
        service=booking.service,
        master_name=booking.master.name,
        start=booking.start_dt,
        tz=business.booking.tz,
    )


def _card(
    booking: Booking, business: BusinessConfig, now: datetime
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Картка запису: кнопки скасування й перенесення або телефон, якщо дедлайн минув."""
    summary = _summary(booking, business)
    if booking_service.can_client_change(booking, business.booking, now):
        return summary, kb.booking_card_kb(booking.id)
    note = texts.CHANGE_DEADLINE_PASSED.format(
        hours=business.booking.cancel_deadline_hours, phone=business.business.phone
    )
    return f"{summary}\n\n{note}", None


async def _show_card(
    callback: CallbackQuery, booking_id: int, session: AsyncSession, business: BusinessConfig
) -> None:
    now = utcnow()
    try:
        booking = await booking_service.get_own_booking(
            session, booking_id, callback.from_user.id, now
        )
    except BookingNotFoundError:
        await callback.message.edit_text(texts.BOOKING_GONE)
        return
    text, markup = _card(booking, business, now)
    await callback.message.edit_text(text, reply_markup=markup)


@router.message(F.text == texts.BTN_MY_BOOKINGS)
async def my_bookings(
    message: Message, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await state.clear()
    now = utcnow()
    bookings = await booking_service.get_upcoming_bookings(session, message.from_user.id, now)
    if not bookings:
        await message.answer(texts.MY_BOOKINGS_EMPTY)
        return
    await message.answer(texts.MY_BOOKINGS_HEADER.format(count=len(bookings)))
    for booking in bookings:
        text, markup = _card(booking, business, now)
        await message.answer(text, reply_markup=markup)


# --- «Буду» з нагадування -----------------------------------------------------


@router.callback_query(MyBookingCb.filter(F.action == "attend"))
async def confirm_attendance(
    callback: CallbackQuery, callback_data: MyBookingCb, session: AsyncSession,
    business: BusinessConfig,
) -> None:
    now = utcnow()
    try:
        booking = await booking_service.confirm_attendance(
            session, booking_id=callback_data.booking_id, telegram_id=callback.from_user.id, now=now
        )
    except BookingNotFoundError:
        await callback.message.edit_text(texts.BOOKING_GONE)
        return
    can_cancel = booking_service.can_client_change(booking, business.booking, now)
    text = reminder_text(booking, business, can_cancel=can_cancel)
    markup = kb.booking_card_kb(booking.id) if can_cancel else None
    await callback.message.edit_text(f"{text}\n\n{texts.ATTENDANCE_CONFIRMED}", reply_markup=markup)


# --- Скасування ---------------------------------------------------------------


@router.callback_query(MyBookingCb.filter(F.action == "cancel"))
async def ask_cancel(
    callback: CallbackQuery, callback_data: MyBookingCb, session: AsyncSession,
    business: BusinessConfig,
) -> None:
    now = utcnow()
    try:
        booking = await booking_service.get_own_booking(
            session, callback_data.booking_id, callback.from_user.id, now
        )
    except BookingNotFoundError:
        await callback.message.edit_text(texts.BOOKING_GONE)
        return
    if not booking_service.can_client_change(booking, business.booking, now):
        await _show_card(callback, booking.id, session, business)
        return
    await callback.message.edit_text(
        texts.CANCEL_CONFIRM.format(summary=_summary(booking, business)),
        reply_markup=kb.cancel_confirm_kb(booking.id),
    )


@router.callback_query(MyBookingCb.filter(F.action == "cancel_yes"))
async def do_cancel(
    callback: CallbackQuery, callback_data: MyBookingCb, session: AsyncSession,
    business: BusinessConfig, bot: Bot, settings: Settings,
) -> None:
    try:
        booking = await booking_service.cancel_booking_by_client(
            session, booking_id=callback_data.booking_id, telegram_id=callback.from_user.id,
            rules=business.booking, now=utcnow(),
        )
    except BookingNotFoundError:
        await callback.message.edit_text(texts.BOOKING_GONE)
        return
    except ChangeDeadlinePassedError:
        await _show_card(callback, callback_data.booking_id, session, business)
        return
    await callback.message.edit_text(
        texts.BOOKING_CANCELLED.format(summary=_summary(booking, business))
    )
    await notifications.notify_staff(
        bot, session, booking_id=booking.id, template=texts.NOTIFY_CANCELLED_BY_CLIENT,
        admin_ids=settings.admin_ids, business=business,
    )


@router.callback_query(MyBookingCb.filter(F.action == "cancel_no"))
async def keep_booking(
    callback: CallbackQuery, callback_data: MyBookingCb, session: AsyncSession,
    business: BusinessConfig,
) -> None:
    await _show_card(callback, callback_data.booking_id, session, business)


# --- Перенесення --------------------------------------------------------------


@router.callback_query(MyBookingCb.filter(F.action == "move"))
async def start_move(
    callback: CallbackQuery, callback_data: MyBookingCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    now = utcnow()
    try:
        booking = await booking_service.get_own_booking(
            session, callback_data.booking_id, callback.from_user.id, now
        )
    except BookingNotFoundError:
        await callback.message.edit_text(texts.BOOKING_GONE)
        return
    if not booking_service.can_client_change(booking, business.booking, now):
        await _show_card(callback, booking.id, session, business)
        return
    # Ті самі послуга й майстер; кроки дати й часу — спільні зі сценарієм запису
    await state.clear()
    await state.set_data(
        {
            "booking_id": booking.id,
            "service_id": booking.service_id,
            "master_id": booking.master_id,
            "old_start": booking.start_dt.isoformat(),
        }
    )
    await show_dates(callback, state, session, business)


@router.callback_query(RescheduleStates.date, NavCb.filter(F.action == "back"))
async def move_back_to_card(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    booking_id = (await state.get_data())["booking_id"]
    await state.clear()
    await _show_card(callback, booking_id, session, business)


@router.callback_query(RescheduleStates.time, TimeCb.filter())
async def move_pick_time(
    callback: CallbackQuery, callback_data: TimeCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    new_start = datetime.fromtimestamp(callback_data.ts, tz=UTC)
    await state.update_data(start=new_start.isoformat())
    await show_move_confirm(callback, state, session, business)


async def show_move_confirm(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    data = await state.get_data()
    try:
        booking = await booking_service.get_own_booking(
            session, data["booking_id"], callback.from_user.id, utcnow()
        )
    except BookingNotFoundError:
        await state.clear()
        await callback.message.edit_text(texts.BOOKING_GONE)
        return
    tz = business.booking.tz
    new_start = datetime.fromisoformat(data["start"])
    new_summary = booking_summary(
        service=booking.service, master_name=booking.master.name, start=new_start, tz=tz
    )
    await state.set_state(RescheduleStates.confirm)
    await callback.message.edit_text(
        texts.MOVE_CONFIRM.format(
            old=fmt_when(booking.start_dt, tz), new=fmt_when(new_start, tz), summary=new_summary
        ),
        reply_markup=kb.move_confirm_kb(),
    )


@router.callback_query(RescheduleStates.confirm, NavCb.filter(F.action == "back"))
async def move_back_to_times(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await show_times(callback, state, session, business)


@router.callback_query(RescheduleStates.confirm, NavCb.filter(F.action == "confirm"))
async def do_move(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig,
    bot: Bot, settings: Settings,
) -> None:
    data = await state.get_data()
    now = utcnow()
    try:
        booking = await booking_service.reschedule_booking(
            session, booking_id=data["booking_id"], telegram_id=callback.from_user.id,
            new_start=datetime.fromisoformat(data["start"]), rules=business.booking, now=now,
            reminders=business.reminders,
        )
    except SlotUnavailableError:
        await show_times(callback, state, session, business, notice=texts.SLOT_TAKEN)
        return
    except BookingNotFoundError:
        await state.clear()
        await callback.message.edit_text(texts.BOOKING_GONE)
        return
    except ChangeDeadlinePassedError:
        await state.clear()
        await _show_card(callback, data["booking_id"], session, business)
        return

    await state.clear()
    reminders = booking_service.planned_reminders(booking.start_dt, now, business.reminders)
    await callback.message.edit_text(
        booking_moved_text(
            summary=_summary(booking, business),
            address=business.business.address,
            reminders=reminders,
        )
    )
    await notifications.notify_staff(
        bot, session, booking_id=booking.id, template=texts.NOTIFY_RESCHEDULED,
        admin_ids=settings.admin_ids, business=business,
        old_start=datetime.fromisoformat(data["old_start"]),
    )
