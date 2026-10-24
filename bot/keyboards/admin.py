"""Клавіатури адмінки."""

from collections.abc import Sequence
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts
from bot.db.models import Booking, Master, Service, TimeOff, WorkingHours
from bot.keyboards.callbacks import AdminCb
from bot.services.demo import FULL_ACCESS, Viewer
from bot.services.formatting import fmt_date, fmt_time, period_text, schedule_line


def _cb(action: str, id: int = 0, arg: str = "") -> str:
    return AdminCb(action=action, id=id, arg=arg).pack()


def _menu_button(builder: InlineKeyboardBuilder) -> None:
    builder.button(text=texts.BTN_ADMIN_MENU, callback_data=_cb("menu"))


def menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for text, action in [
        (texts.BTN_ADMIN_TODAY, "today"),
        (texts.BTN_ADMIN_WEEK, "week"),
        (texts.BTN_ADMIN_SERVICES, "svcs"),
        (texts.BTN_ADMIN_MASTERS, "masters"),
        (texts.BTN_ADMIN_SCHEDULE, "sched"),
        (texts.BTN_ADMIN_TIME_OFF, "off"),
    ]:
        builder.button(text=text, callback_data=_cb(action))
    builder.adjust(2)
    return builder.as_markup()


def back_kb(action: str, id: int = 0, text: str = texts.BTN_ADMIN_MENU) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=text, callback_data=_cb(action, id))
    return builder.as_markup()


def back_to_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _menu_button(builder)
    return builder.as_markup()


def cancel_input_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_CANCEL_INPUT, callback_data=_cb("cancel_input"))
    return builder.as_markup()


# --- Записи -------------------------------------------------------------------


def day_kb(
    day: date, bookings: Sequence[Booking], tz: ZoneInfo, viewer: Viewer = FULL_ACCESS
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for booking in bookings:
        builder.button(
            text=texts.ADMIN_BOOKING_BUTTON.format(
                time=fmt_time(booking.start_dt, tz),
                client=viewer.client_name(booking.client),
                master=booking.master.name,
            ),
            callback_data=_cb("bk", booking.id),
        )
    builder.button(text=texts.BTN_PREV_DAY, callback_data=_cb("day", arg=(day - timedelta(days=1)).isoformat()))
    builder.button(text=texts.BTN_NEXT_DAY, callback_data=_cb("day", arg=(day + timedelta(days=1)).isoformat()))
    _menu_button(builder)
    builder.adjust(*([1] * len(bookings)), 2, 1)
    return builder.as_markup()


def week_kb(days: Sequence[tuple[date, int]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for day, count in days:
        builder.button(
            text=texts.ADMIN_WEEK_DAY.format(date=fmt_date(day), count=count),
            callback_data=_cb("day", arg=day.isoformat()),
        )
    _menu_button(builder)
    builder.adjust(1)
    return builder.as_markup()


def deposit_kb(booking_id: int) -> InlineKeyboardMarkup:
    """У сповіщенні про бронювання, що чекає на аванс: «Аванс отримано» / «Скасувати»."""
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_DEPOSIT_RECEIVED, callback_data=_cb("dep_ok", booking_id))
    builder.button(text=texts.BTN_ADMIN_CANCEL, callback_data=_cb("bk_cancel", booking_id))
    builder.adjust(1)
    return builder.as_markup()


def booking_kb(booking: Booking, tz: ZoneInfo, *, can_mark: bool, can_cancel: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if can_cancel and booking.deposit_pending:
        builder.button(text=texts.BTN_DEPOSIT_RECEIVED, callback_data=_cb("dep_ok", booking.id))
    if can_mark:
        builder.button(text=texts.BTN_MARK_COMPLETED, callback_data=_cb("bk_done", booking.id))
        builder.button(text=texts.BTN_MARK_NO_SHOW, callback_data=_cb("bk_noshow", booking.id))
    if can_cancel:
        builder.button(text=texts.BTN_ADMIN_CANCEL, callback_data=_cb("bk_cancel", booking.id))
    day = booking.start_dt.astimezone(tz).date()
    builder.button(text=texts.BTN_BACK_TO_DAY, callback_data=_cb("day", arg=day.isoformat()))
    builder.adjust(1)
    return builder.as_markup()


def back_to_day_kb(day: date) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_BACK_TO_DAY, callback_data=_cb("day", arg=day.isoformat()))
    return builder.as_markup()


def admin_cancel_confirm_kb(booking_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_ADMIN_CANCEL_YES, callback_data=_cb("bk_cancel_yes", booking_id))
    builder.button(text=texts.BTN_ADMIN_CANCEL_NO, callback_data=_cb("bk", booking_id))
    builder.adjust(1)
    return builder.as_markup()


# --- Послуги й майстри --------------------------------------------------------


def services_kb(services: Sequence[Service]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for service in services:
        builder.button(
            text=texts.ADMIN_SERVICE_BUTTON.format(
                hidden="" if service.is_active else texts.HIDDEN_MARK,
                name=service.name,
                duration=service.duration_min,
                price=service.price,
            ),
            callback_data=_cb("svc", service.id),
        )
    builder.button(text=texts.BTN_ADD_SERVICE, callback_data=_cb("svc_add"))
    _menu_button(builder)
    builder.adjust(1)
    return builder.as_markup()


def service_kb(service: Service) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_EDIT_NAME, callback_data=_cb("svc_edit", service.id, "name"))
    builder.button(text=texts.BTN_EDIT_DURATION, callback_data=_cb("svc_edit", service.id, "duration_min"))
    builder.button(text=texts.BTN_EDIT_PRICE, callback_data=_cb("svc_edit", service.id, "price"))
    if service.is_active:
        builder.button(text=texts.BTN_HIDE, callback_data=_cb("svc_hide", service.id))
    else:
        builder.button(text=texts.BTN_SHOW, callback_data=_cb("svc_show", service.id))
    builder.button(text=texts.BTN_BACK_TO_SERVICES, callback_data=_cb("svcs"))
    builder.adjust(3, 1, 1)
    return builder.as_markup()


def masters_kb(masters: Sequence[Master], action: str = "mst", with_add: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for master in masters:
        hidden = "" if master.is_active else texts.HIDDEN_MARK
        builder.button(text=f"{hidden}{master.name}", callback_data=_cb(action, master.id))
    if with_add:
        builder.button(text=texts.BTN_ADD_MASTER, callback_data=_cb("mst_add"))
    _menu_button(builder)
    builder.adjust(1)
    return builder.as_markup()


def master_kb(master: Master) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_EDIT_MASTER_NAME, callback_data=_cb("mst_edit", master.id, "name"))
    builder.button(text=texts.BTN_EDIT_TELEGRAM_ID, callback_data=_cb("mst_edit", master.id, "telegram_id"))
    builder.button(text=texts.BTN_MASTER_SERVICES, callback_data=_cb("mst_svcs", master.id))
    builder.button(text=texts.BTN_ADMIN_SCHEDULE, callback_data=_cb("sched_m", master.id))
    builder.button(text=texts.BTN_ADMIN_TIME_OFF, callback_data=_cb("off_m", master.id))
    if master.is_active:
        builder.button(text=texts.BTN_HIDE, callback_data=_cb("mst_hide", master.id))
    else:
        builder.button(text=texts.BTN_SHOW, callback_data=_cb("mst_show", master.id))
    builder.button(text=texts.BTN_BACK_TO_MASTERS, callback_data=_cb("masters"))
    builder.adjust(2, 1, 2, 1, 1)
    return builder.as_markup()


def master_services_kb(master: Master, services: Sequence[Service]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    linked = {s.id for s in master.services}
    for service in services:
        mark = texts.LINKED_MARK if service.id in linked else texts.UNLINKED_MARK
        builder.button(
            text=f"{mark}{service.name}", callback_data=_cb("mst_svc", master.id, str(service.id))
        )
    builder.button(text=texts.BTN_BACK_TO_MASTER, callback_data=_cb("mst", master.id))
    builder.adjust(1)
    return builder.as_markup()


def force_kb(force_action: str, abort_action: str, id: int = 0, arg: str = "",
             force_text: str = texts.BTN_FORCE_SAVE) -> InlineKeyboardMarkup:
    """Підтвердження дії, що зачіпає майбутні записи."""
    builder = InlineKeyboardBuilder()
    builder.button(text=force_text, callback_data=_cb(force_action, id, arg))
    builder.button(text=texts.BTN_ABORT, callback_data=_cb(abort_action, id))
    builder.adjust(1)
    return builder.as_markup()


# --- Графік і блокування ------------------------------------------------------


def schedule_kb(master: Master) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    by_day: dict[int, WorkingHours] = {wh.weekday: wh for wh in master.working_hours}
    for weekday in range(7):
        builder.button(
            text=schedule_line(weekday, by_day.get(weekday)),
            callback_data=_cb("sched_day", master.id, str(weekday)),
        )
    builder.button(text=texts.BTN_BACK_TO_MASTER, callback_data=_cb("mst", master.id))
    builder.adjust(1)
    return builder.as_markup()


def no_reason_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_NO_REASON, callback_data=_cb("off_noreason"))
    builder.button(text=texts.BTN_CANCEL_INPUT, callback_data=_cb("cancel_input"))
    builder.adjust(1)
    return builder.as_markup()


def time_offs_kb(master_id: int, time_offs: Sequence[TimeOff], tz: ZoneInfo) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for time_off in time_offs:
        builder.button(
            text=texts.BTN_DELETE_TIME_OFF.format(period=period_text(time_off.start_dt, time_off.end_dt, tz)),
            callback_data=_cb("off_del", time_off.id),
        )
    builder.button(text=texts.BTN_ADD_TIME_OFF, callback_data=_cb("off_add", master_id))
    builder.button(text=texts.BTN_BACK_TO_MASTER, callback_data=_cb("mst", master_id))
    builder.adjust(1)
    return builder.as_markup()


def time_off_delete_kb(time_off_id: int, master_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_DELETE_YES, callback_data=_cb("off_del_yes", time_off_id))
    builder.button(text=texts.BTN_DELETE_NO, callback_data=_cb("off_m", master_id))
    builder.adjust(1)
    return builder.as_markup()
