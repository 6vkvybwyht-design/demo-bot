"""Адмінка (/admin). Доступ — лише ADMIN_IDS, перевіряється перед кожним хендлером."""

from datetime import date, datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.callback_answer import CallbackAnswer
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.config import BusinessConfig, Settings
from bot.db.models import BookingStatus, Service, utcnow
from bot.keyboards import admin as kb
from bot.keyboards.callbacks import AdminCb
from bot.middlewares.admin import AdminOnlyMiddleware
from bot.services import admin, notifications
from bot.services.admin import InputError
from bot.services.booking import BookingNotFoundError
from bot.services.formatting import (
    admin_day_text,
    admin_master_text,
    admin_service_text,
    future_bookings_lines,
    period_text,
    status_label,
    time_off_line,
    time_offs_text,
)
from bot.states import AdminStates

router = Router(name="admin")
router.message.middleware(AdminOnlyMiddleware())
router.callback_query.middleware(AdminOnlyMiddleware())

Event = Message | CallbackQuery


async def _render(event: Event, text: str, reply_markup=None) -> None:
    """Для кнопки редагуємо поточне повідомлення, після введення тексту — надсилаємо нове."""
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=reply_markup)
    else:
        await event.answer(text, reply_markup=reply_markup)


def _today(business: BusinessConfig) -> date:
    return utcnow().astimezone(business.booking.tz).date()


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.ADMIN_MENU, reply_markup=kb.menu_kb())


@router.callback_query(AdminCb.filter(F.action == "menu"))
async def show_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(texts.ADMIN_MENU, reply_markup=kb.menu_kb())


@router.callback_query(AdminCb.filter(F.action == "cancel_input"))
async def cancel_input(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(texts.INPUT_CANCELLED, reply_markup=kb.back_to_menu_kb())


# --- Записи: день, тиждень, картка запису -------------------------------------


async def show_day(event: Event, day: date, session: AsyncSession, business: BusinessConfig) -> None:
    tz = business.booking.tz
    bookings = await admin.bookings_for_day(session, day, tz)
    await _render(event, admin_day_text(day, bookings, tz), kb.day_kb(day, bookings, tz))


@router.callback_query(AdminCb.filter(F.action == "today"))
async def today(callback: CallbackQuery, session: AsyncSession, business: BusinessConfig) -> None:
    await show_day(callback, _today(business), session, business)


@router.callback_query(AdminCb.filter(F.action == "day"))
async def day(
    callback: CallbackQuery, callback_data: AdminCb, session: AsyncSession, business: BusinessConfig
) -> None:
    await show_day(callback, date.fromisoformat(callback_data.arg), session, business)


@router.callback_query(AdminCb.filter(F.action == "week"))
async def week(callback: CallbackQuery, session: AsyncSession, business: BusinessConfig) -> None:
    days = await admin.week_counts(session, _today(business), business.booking.tz)
    await callback.message.edit_text(texts.ADMIN_WEEK, reply_markup=kb.week_kb(days))


async def show_booking(
    callback: CallbackQuery, booking_id: int, session: AsyncSession, business: BusinessConfig
) -> None:
    booking = await admin.get_booking(session, booking_id)
    if booking is None:
        await callback.message.edit_text(texts.BOOKING_GONE, reply_markup=kb.back_to_menu_kb())
        return
    now = utcnow()
    text = texts.ADMIN_BOOKING.format(
        details=notifications.staff_details(booking, business), status=status_label(booking.status)
    )
    markup = kb.booking_kb(
        booking, business.booking.tz,
        can_mark=admin.can_mark(booking, now), can_cancel=admin.can_cancel(booking, now),
    )
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(AdminCb.filter(F.action == "bk"))
async def booking_card(
    callback: CallbackQuery, callback_data: AdminCb, session: AsyncSession, business: BusinessConfig
) -> None:
    await show_booking(callback, callback_data.id, session, business)


@router.callback_query(AdminCb.filter(F.action.in_({"bk_done", "bk_noshow"})))
async def mark_booking(
    callback: CallbackQuery, callback_data: AdminCb, callback_answer: CallbackAnswer,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    status = BookingStatus.COMPLETED if callback_data.action == "bk_done" else BookingStatus.NO_SHOW
    try:
        await admin.mark_booking(session, callback_data.id, status, utcnow())
    except BookingNotFoundError:
        callback_answer.text = texts.ADMIN_BOOKING_CHANGED
    await show_booking(callback, callback_data.id, session, business)


@router.callback_query(AdminCb.filter(F.action == "bk_cancel"))
async def ask_admin_cancel(
    callback: CallbackQuery, callback_data: AdminCb, session: AsyncSession, business: BusinessConfig
) -> None:
    booking = await admin.get_booking(session, callback_data.id)
    if booking is None or not admin.can_cancel(booking, utcnow()):
        await show_booking(callback, callback_data.id, session, business)
        return
    await callback.message.edit_text(
        texts.ADMIN_CANCEL_CONFIRM.format(details=notifications.staff_details(booking, business)),
        reply_markup=kb.admin_cancel_confirm_kb(booking.id),
    )


@router.callback_query(AdminCb.filter(F.action == "bk_cancel_yes"))
async def do_admin_cancel(
    callback: CallbackQuery, callback_data: AdminCb, callback_answer: CallbackAnswer,
    session: AsyncSession, business: BusinessConfig, bot: Bot, settings: Settings,
) -> None:
    try:
        booking = await admin.cancel_booking_by_admin(session, callback_data.id, utcnow())
    except BookingNotFoundError:
        callback_answer.text = texts.ADMIN_BOOKING_CHANGED
        await show_booking(callback, callback_data.id, session, business)
        return

    notified = await notifications.notify_client_cancelled_by_admin(
        bot, session, booking_id=booking.id, business=business
    )
    booking = await admin.get_booking(session, booking.id)
    client_note = (
        texts.CLIENT_NOTIFIED
        if notified
        else texts.CLIENT_NOT_NOTIFIED.format(phone=booking.client.phone or texts.NO_PHONE)
    )
    day_iso = booking.start_dt.astimezone(business.booking.tz).date().isoformat()
    await callback.message.edit_text(
        texts.ADMIN_CANCELLED.format(
            client_note=client_note, details=notifications.staff_details(booking, business)
        ),
        reply_markup=kb.back_to_day_kb(date.fromisoformat(day_iso)),
    )
    await notifications.notify_staff(
        bot, session, booking_id=booking.id, template=texts.NOTIFY_CANCELLED_BY_ADMIN,
        admin_ids=settings.admin_ids, business=business, exclude=callback.from_user.id,
    )


# --- Послуги ------------------------------------------------------------------


async def show_services(event: Event, session: AsyncSession) -> None:
    await _render(event, texts.ADMIN_SERVICES, kb.services_kb(await admin.list_services(session)))


async def show_service(event: Event, service_id: int, session: AsyncSession) -> None:
    service = await session.get(Service, service_id, populate_existing=True)
    await _render(event, admin_service_text(service), kb.service_kb(service))


@router.callback_query(AdminCb.filter(F.action == "svcs"))
async def services(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await show_services(callback, session)


@router.callback_query(AdminCb.filter(F.action == "svc"))
async def service(callback: CallbackQuery, callback_data: AdminCb, session: AsyncSession) -> None:
    await show_service(callback, callback_data.id, session)


@router.callback_query(AdminCb.filter(F.action == "svc_hide"))
async def hide_service(
    callback: CallbackQuery, callback_data: AdminCb, session: AsyncSession, business: BusinessConfig
) -> None:
    upcoming = await admin.future_bookings(session, utcnow(), service_id=callback_data.id)
    if upcoming:
        svc = await session.get(Service, callback_data.id)
        await callback.message.edit_text(
            texts.WARN_HIDE_SERVICE.format(
                name=svc.name, count=len(upcoming),
                lines=future_bookings_lines(upcoming, business.booking.tz),
            ),
            reply_markup=kb.force_kb("svc_hide_force", "svc", callback_data.id,
                                     force_text=texts.BTN_FORCE_HIDE),
        )
        return
    await admin.update_service(session, callback_data.id, is_active=False)
    await show_service(callback, callback_data.id, session)


@router.callback_query(AdminCb.filter(F.action.in_({"svc_hide_force", "svc_show"})))
async def set_service_visibility(
    callback: CallbackQuery, callback_data: AdminCb, session: AsyncSession
) -> None:
    await admin.update_service(session, callback_data.id, is_active=callback_data.action == "svc_show")
    await show_service(callback, callback_data.id, session)


# --- Майстри ------------------------------------------------------------------


async def show_master(event: Event, master_id: int, session: AsyncSession) -> None:
    master = await admin.get_master(session, master_id)
    await _render(event, admin_master_text(master), kb.master_kb(master))


@router.callback_query(AdminCb.filter(F.action == "masters"))
async def masters(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await callback.message.edit_text(
        texts.ADMIN_MASTERS, reply_markup=kb.masters_kb(await admin.list_masters(session))
    )


@router.callback_query(AdminCb.filter(F.action == "mst"))
async def master(
    callback: CallbackQuery, callback_data: AdminCb, state: FSMContext, session: AsyncSession
) -> None:
    await state.clear()
    await show_master(callback, callback_data.id, session)


@router.callback_query(AdminCb.filter(F.action == "mst_hide"))
async def hide_master(
    callback: CallbackQuery, callback_data: AdminCb, session: AsyncSession, business: BusinessConfig
) -> None:
    upcoming = await admin.future_bookings(session, utcnow(), master_id=callback_data.id)
    if upcoming:
        mst = await admin.get_master(session, callback_data.id)
        await callback.message.edit_text(
            texts.WARN_HIDE_MASTER.format(
                name=mst.name, count=len(upcoming),
                lines=future_bookings_lines(upcoming, business.booking.tz),
            ),
            reply_markup=kb.force_kb("mst_hide_force", "mst", callback_data.id,
                                     force_text=texts.BTN_FORCE_HIDE),
        )
        return
    await admin.update_master(session, callback_data.id, is_active=False)
    await show_master(callback, callback_data.id, session)


@router.callback_query(AdminCb.filter(F.action.in_({"mst_hide_force", "mst_show"})))
async def set_master_visibility(
    callback: CallbackQuery, callback_data: AdminCb, session: AsyncSession
) -> None:
    await admin.update_master(session, callback_data.id, is_active=callback_data.action == "mst_show")
    await show_master(callback, callback_data.id, session)


async def show_master_services(event: Event, master_id: int, session: AsyncSession) -> None:
    mst = await admin.get_master(session, master_id)
    await _render(
        event,
        texts.ADMIN_MASTER_SERVICES.format(name=mst.name),
        kb.master_services_kb(mst, await admin.list_services(session)),
    )


@router.callback_query(AdminCb.filter(F.action == "mst_svcs"))
async def master_services(callback: CallbackQuery, callback_data: AdminCb, session: AsyncSession) -> None:
    await show_master_services(callback, callback_data.id, session)


@router.callback_query(AdminCb.filter(F.action.in_({"mst_svc", "mst_svc_force"})))
async def toggle_master_service(
    callback: CallbackQuery, callback_data: AdminCb, session: AsyncSession, business: BusinessConfig
) -> None:
    master_id, service_id = callback_data.id, int(callback_data.arg)
    mst = await admin.get_master(session, master_id)
    unlinking = service_id in {s.id for s in mst.services}
    if unlinking and callback_data.action == "mst_svc":
        upcoming = await admin.future_bookings(
            session, utcnow(), master_id=master_id, service_id=service_id
        )
        if upcoming:
            svc = await session.get(Service, service_id)
            await callback.message.edit_text(
                texts.WARN_UNLINK_SERVICE.format(
                    master=mst.name, service=svc.name, count=len(upcoming),
                    lines=future_bookings_lines(upcoming, business.booking.tz),
                ),
                reply_markup=kb.force_kb("mst_svc_force", "mst_svcs", master_id, str(service_id),
                                         force_text=texts.BTN_FORCE_UNLINK),
            )
            return
    if unlinking or callback_data.action == "mst_svc":
        await admin.toggle_master_service(session, master_id, service_id)
    await show_master_services(callback, master_id, session)


# --- Графік -------------------------------------------------------------------


async def show_schedule(event: Event, master_id: int, session: AsyncSession) -> None:
    mst = await admin.get_master(session, master_id)
    await _render(event, texts.ADMIN_SCHEDULE.format(name=mst.name), kb.schedule_kb(mst))


@router.callback_query(AdminCb.filter(F.action == "sched"))
async def schedule_choose_master(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.message.edit_text(
        texts.ADMIN_CHOOSE_MASTER,
        reply_markup=kb.masters_kb(await admin.list_masters(session), "sched_m", with_add=False),
    )


@router.callback_query(AdminCb.filter(F.action == "sched_m"))
async def schedule(
    callback: CallbackQuery, callback_data: AdminCb, state: FSMContext, session: AsyncSession
) -> None:
    await state.clear()
    await show_schedule(callback, callback_data.id, session)


@router.callback_query(AdminCb.filter(F.action == "sched_day"))
async def ask_hours(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    weekday = int(callback_data.arg)
    await _ask(callback, state, texts.ASK_HOURS.format(weekday=texts.WEEKDAYS_FULL[weekday]),
               kind="hours", id=callback_data.id, weekday=weekday)


async def _save_hours(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig,
    *, master_id: int, weekday: int, hours_text: str, force: bool,
) -> None:
    hours = admin.parse_hours(hours_text)
    if not force:
        upcoming = await admin.future_bookings(session, utcnow(), master_id=master_id)
        conflicts = admin.schedule_conflicts(upcoming, weekday, hours, business.booking.tz)
        if conflicts:
            await state.set_state(AdminStates.confirm)
            await state.set_data({"kind": "hours", "id": master_id, "weekday": weekday, "value": hours_text})
            await _render(
                event,
                texts.WARN_SCHEDULE.format(
                    weekday=texts.WEEKDAYS_FULL[weekday], count=len(conflicts),
                    lines=future_bookings_lines(conflicts, business.booking.tz),
                ),
                kb.force_kb("force", "sched_m", master_id),
            )
            return
    await admin.set_working_hours(session, master_id, weekday, hours)
    await state.clear()
    if isinstance(event, Message):
        await event.answer(texts.SCHEDULE_SAVED)
    await show_schedule(event, master_id, session)


# --- Блокування часу ----------------------------------------------------------


@router.callback_query(AdminCb.filter(F.action == "off"))
async def time_off_choose_master(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.message.edit_text(
        texts.ADMIN_CHOOSE_MASTER,
        reply_markup=kb.masters_kb(await admin.list_masters(session), "off_m", with_add=False),
    )


async def show_time_offs(
    event: Event, master_id: int, session: AsyncSession, business: BusinessConfig,
    notice: str | None = None,
) -> None:
    tz = business.booking.tz
    mst = await admin.get_master(session, master_id)
    time_offs = await admin.upcoming_time_offs(session, master_id, utcnow())
    text = time_offs_text(mst.name, time_offs, tz)
    if notice:
        text = f"{notice}\n\n{text}"
    await _render(event, text, kb.time_offs_kb(master_id, time_offs, tz))


@router.callback_query(AdminCb.filter(F.action == "off_m"))
async def time_offs(
    callback: CallbackQuery, callback_data: AdminCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    await state.clear()
    await show_time_offs(callback, callback_data.id, session, business)


@router.callback_query(AdminCb.filter(F.action == "off_add"))
async def ask_period(
    callback: CallbackQuery, callback_data: AdminCb, state: FSMContext, session: AsyncSession
) -> None:
    await state.clear()
    mst = await admin.get_master(session, callback_data.id)
    await _ask(callback, state, texts.ASK_PERIOD.format(name=mst.name), kind="period", id=mst.id)


@router.callback_query(AdminCb.filter(F.action == "off_del"))
async def ask_delete_time_off(
    callback: CallbackQuery, callback_data: AdminCb, callback_answer: CallbackAnswer,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    time_off = await admin.get_time_off(session, callback_data.id)
    if time_off is None:
        callback_answer.text = texts.TIME_OFF_GONE
        await callback.message.edit_text(texts.TIME_OFF_GONE, reply_markup=kb.back_to_menu_kb())
        return
    await callback.message.edit_text(
        texts.TIME_OFF_DELETE_CONFIRM.format(line=time_off_line(time_off, business.booking.tz)),
        reply_markup=kb.time_off_delete_kb(time_off.id, time_off.master_id),
    )


@router.callback_query(AdminCb.filter(F.action == "off_del_yes"))
async def delete_time_off(
    callback: CallbackQuery, callback_data: AdminCb, session: AsyncSession, business: BusinessConfig
) -> None:
    time_off = await admin.get_time_off(session, callback_data.id)
    if time_off is None:
        await callback.message.edit_text(texts.TIME_OFF_GONE, reply_markup=kb.back_to_menu_kb())
        return
    master_id = time_off.master_id
    await admin.delete_time_off(session, time_off.id)
    await show_time_offs(callback, master_id, session, business, notice=texts.TIME_OFF_DELETED)


async def _save_time_off(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig,
    *, reason: str | None, force: bool,
) -> None:
    data = await state.get_data()
    master_id = data["id"]
    start, end = datetime.fromisoformat(data["start"]), datetime.fromisoformat(data["end"])
    tz = business.booking.tz
    if not force:
        conflicts = await admin.time_off_conflicts(session, master_id, start, end, utcnow())
        if conflicts:
            await state.set_state(AdminStates.confirm)
            await state.update_data(kind="time_off", reason=reason)
            await _render(
                event,
                texts.WARN_TIME_OFF.format(count=len(conflicts), lines=future_bookings_lines(conflicts, tz)),
                kb.force_kb("force", "off_m", master_id),
            )
            return
    await admin.create_time_off(session, master_id, start, end, reason)
    await state.clear()
    await show_time_offs(
        event, master_id, session, business,
        notice=texts.TIME_OFF_SAVED.format(period=period_text(start, end, tz)),
    )


@router.callback_query(AdminStates.input, AdminCb.filter(F.action == "off_noreason"))
async def time_off_without_reason(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await _save_time_off(callback, state, session, business, reason=None, force=False)


# --- Підтвердження дії з конфліктами ------------------------------------------


@router.callback_query(AdminStates.confirm, AdminCb.filter(F.action == "force"))
async def force_pending(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    data = await state.get_data()
    if data["kind"] == "hours":
        await _save_hours(
            callback, state, session, business,
            master_id=data["id"], weekday=data["weekday"], hours_text=data["value"], force=True,
        )
    else:
        await _save_time_off(callback, state, session, business, reason=data.get("reason"), force=True)


# --- Введення тексту ----------------------------------------------------------

# Що запитуємо: kind -> (підказка при помилці)
_HINTS = {
    "svc_new_name": texts.HINT_NAME,
    "svc_new_duration": texts.HINT_DURATION,
    "svc_new_price": texts.HINT_PRICE,
    "svc_name": texts.HINT_NAME,
    "svc_duration_min": texts.HINT_DURATION,
    "svc_price": texts.HINT_PRICE,
    "mst_new_name": texts.HINT_NAME,
    "mst_name": texts.HINT_NAME,
    "mst_telegram_id": texts.HINT_TELEGRAM_ID,
    "hours": texts.HINT_HOURS,
    "period": texts.HINT_PERIOD,
    "reason": texts.HINT_NAME,
}

_SERVICE_PROMPTS = {
    "name": texts.ASK_SERVICE_NAME,
    "duration_min": texts.ASK_SERVICE_DURATION,
    "price": texts.ASK_SERVICE_PRICE,
}
_MASTER_PROMPTS = {"name": texts.ASK_MASTER_NAME, "telegram_id": texts.ASK_TELEGRAM_ID}


async def _ask(event: Event, state: FSMContext, prompt: str, reply_markup=None, **data) -> None:
    """Переходимо в очікування тексту; `data["kind"]` — що саме чекаємо."""
    await state.set_state(AdminStates.input)
    await state.update_data(**data)
    await _render(event, prompt, reply_markup or kb.cancel_input_kb())


@router.callback_query(AdminCb.filter(F.action == "svc_add"))
async def add_service(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _ask(callback, state, texts.ASK_SERVICE_NAME, kind="svc_new_name")


@router.callback_query(AdminCb.filter(F.action == "svc_edit"))
async def edit_service(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    await state.clear()
    await _ask(callback, state, _SERVICE_PROMPTS[callback_data.arg],
               kind=f"svc_{callback_data.arg}", id=callback_data.id)


@router.callback_query(AdminCb.filter(F.action == "mst_add"))
async def add_master(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _ask(callback, state, texts.ASK_MASTER_NAME, kind="mst_new_name")


@router.callback_query(AdminCb.filter(F.action == "mst_edit"))
async def edit_master(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    await state.clear()
    await _ask(callback, state, _MASTER_PROMPTS[callback_data.arg],
               kind=f"mst_{callback_data.arg}", id=callback_data.id)


@router.message(AdminStates.input, F.text)
async def got_input(
    message: Message, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    data = await state.get_data()
    kind, text = data["kind"], message.text
    try:
        await _handle_input(message, state, session, business, kind, text, data)
    except InputError:
        await message.answer(texts.INVALID_INPUT.format(hint=_HINTS[kind]), reply_markup=kb.cancel_input_kb())


async def _handle_input(
    message: Message, state: FSMContext, session: AsyncSession, business: BusinessConfig,
    kind: str, text: str, data: dict,
) -> None:
    # Нова послуга: назва → тривалість → ціна
    if kind == "svc_new_name":
        await _ask(message, state, texts.ASK_SERVICE_DURATION, kind="svc_new_duration",
                   name=admin.parse_name(text))
    elif kind == "svc_new_duration":
        await _ask(message, state, texts.ASK_SERVICE_PRICE, kind="svc_new_price",
                   duration=admin.parse_int(text, 5, 600))
    elif kind == "svc_new_price":
        svc = await admin.create_service(
            session, data["name"], data["duration"], admin.parse_int(text, 0, 1_000_000)
        )
        await state.clear()
        await show_service(message, svc.id, session)

    # Зміна послуги
    elif kind in {"svc_name", "svc_duration_min", "svc_price"}:
        field = kind.removeprefix("svc_")
        value = {
            "name": lambda: admin.parse_name(text),
            "duration_min": lambda: admin.parse_int(text, 5, 600),
            "price": lambda: admin.parse_int(text, 0, 1_000_000),
        }[field]()
        await admin.update_service(session, data["id"], **{field: value})
        await state.clear()
        await show_service(message, data["id"], session)

    # Майстри
    elif kind == "mst_new_name":
        mst = await admin.create_master(session, admin.parse_name(text))
        await state.clear()
        await show_master(message, mst.id, session)
    elif kind in {"mst_name", "mst_telegram_id"}:
        field = kind.removeprefix("mst_")
        value = admin.parse_name(text) if field == "name" else admin.parse_telegram_id(text)
        await admin.update_master(session, data["id"], **{field: value})
        await state.clear()
        await show_master(message, data["id"], session)

    # Графік і блокування
    elif kind == "hours":
        await _save_hours(message, state, session, business, master_id=data["id"],
                          weekday=data["weekday"], hours_text=text, force=False)
    elif kind == "period":
        start, end = admin.parse_period(text, _today(business), business.booking.tz)
        await _ask(message, state, texts.ASK_REASON, kb.no_reason_kb(), kind="reason",
                   start=start.isoformat(), end=end.isoformat())
    elif kind == "reason":
        await _save_time_off(message, state, session, business,
                             reason=admin.parse_name(text, max_length=200), force=False)
