"""Форматування даних для показу користувачу (час — у часовому поясі бізнесу)."""

from collections.abc import Sequence
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import html

from bot import texts
from bot.config import BusinessConfig, BusinessInfo, PetsConfig, SpaConfig, SpaGuests, SpaService
from bot.db.models import Booking, BookingStatus, Master, Pet, Service, TimeOff, WorkingHours
from bot.services import spa as spa_service
from bot.services.booking import PlannedReminders
from bot.services.demo import FULL_ACCESS, Viewer
from bot.services.pets import Offer, pet_description


def fmt_time(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%H:%M")


def fmt_date(day: date) -> str:
    return f"{texts.WEEKDAYS_SHORT[day.weekday()]}, {day:%d.%m}"


def fmt_price(price: int, price_from: bool = False) -> str:
    """«1300» або «від 1300» (грн додає шаблон)."""
    return texts.PRICE_FROM.format(price=price) if price_from else str(price)


def service_button(service: Service, offer: Offer | None = None) -> str:
    """Кнопка послуги; `offer` — тривалість і ціна для конкретної тварини."""
    if offer is None:
        offer = Offer(service.duration_min, service.price)
    return texts.SERVICE_BUTTON.format(
        name=service.name, duration=offer.duration_min, price=fmt_price(offer.price, offer.price_from)
    )


def progress_text(
    prompt: str,
    *,
    pet_name: str | None = None,
    service: Service | None = None,
    master_name: str | None = None,
    any_master: bool = False,
    day: date | None = None,
    start: datetime | None = None,
    tz: ZoneInfo | None = None,
) -> str:
    """Підказка кроку з наростаючим підсумком над нею: (тварина ·) послуга · майстер · дата · час."""
    parts = []
    if pet_name:
        parts.append(html.quote(pet_name))
    if service is not None:
        parts.append(html.quote(service.name))
    if any_master:
        parts.append(texts.PROGRESS_ANY_MASTER)
    elif master_name:
        parts.append(html.quote(master_name))
    if day is not None:
        parts.append(fmt_date(day))
    if start is not None:
        parts.append(fmt_time(start, tz))
    if not parts:
        return prompt
    return texts.PROGRESS.format(line=texts.PROGRESS_SEPARATOR.join(parts), prompt=prompt)


def reminders_note(reminders: PlannedReminders) -> str:
    if reminders.before_24h and reminders.before_2h:
        return texts.REMINDERS_BOTH
    if reminders.before_24h:
        return texts.REMINDER_24H
    if reminders.before_2h:
        return texts.REMINDER_2H
    return ""


def _after_booking(reminders: PlannedReminders) -> str:
    return " ".join(filter(None, [reminders_note(reminders), texts.MANAGE_BOOKING]))


def booking_done_text(*, summary: str, address: str, reminders: PlannedReminders) -> str:
    return texts.BOOKING_DONE.format(
        summary=summary, address=html.quote(address), after=_after_booking(reminders)
    )


def booking_moved_text(*, summary: str, address: str, reminders: PlannedReminders) -> str:
    return texts.BOOKING_MOVED.format(
        summary=summary, address=html.quote(address), after=_after_booking(reminders)
    )


def fmt_when(start: datetime, tz: ZoneInfo) -> str:
    return texts.MOVE_WHEN.format(date=fmt_date(start.astimezone(tz).date()), time=fmt_time(start, tz))


def contacts_text(info: BusinessInfo) -> str:
    return texts.CONTACTS.format(
        name=html.quote(info.name), address=html.quote(info.address), phone=html.quote(info.phone)
    )


def booking_summary(
    *,
    service: Service,
    master_name: str | None,
    start: datetime,
    tz: ZoneInfo,
    duration_min: int | None = None,
    price: int | None = None,
    pet: Pet | None = None,
    pets: PetsConfig | None = None,
    price_from: bool = False,
    addon_names: Sequence[str] = (),
    guests_details: str | None = None,
    hide_master: bool = False,
    deposit_line: str = "",
) -> str:
    """Деталі запису. `master_name=None` — «будь-який майстер».

    `duration_min` і `price` — разом із додатковими послугами й для конкретної тварини,
    якщо вони не з послуги. `price_from` — ціна «від», з приміткою про огляд майстром.
    Спа: `guests_details` — склад гостей, `hide_master` — послуга без майстра,
    `deposit_line` — стан авансу.
    """
    template = texts.SUMMARY_NO_MASTER if hide_master else texts.SUMMARY
    text = template.format(
        service=html.quote(service.name),
        master=html.quote(master_name) if master_name else texts.ANY_MASTER_LABEL,
        date=fmt_date(start.astimezone(tz).date()),
        time=fmt_time(start, tz),
        duration=duration_min or service.duration_min,
        price=fmt_price(price if price is not None else service.price, price_from),
    )
    if pet is not None:
        text = pet_line(pet, pets) + text
    if guests_details:
        text += texts.SUMMARY_GUESTS.format(details=html.quote(guests_details))
    if addon_names:
        text += texts.SUMMARY_ADDONS.format(names=html.quote(", ".join(addon_names)))
    text += deposit_line
    if price_from:
        text += texts.PRICE_FROM_NOTE
    return text


def deposit_line(booking: Booking, tz: ZoneInfo) -> str:
    """Стан авансу запису: очікуємо до … / внесено. Порожньо, якщо аванс не потрібен."""
    if booking.deposit is None:
        return ""
    if booking.deposit_paid_at is not None:
        return texts.SUMMARY_DEPOSIT_PAID.format(amount=booking.deposit)
    until = fmt_time(booking.hold_until, tz) if booking.hold_until else "—"
    return texts.SUMMARY_DEPOSIT_PENDING.format(amount=booking.deposit, until=until)


def pet_line(pet: Pet, pets: PetsConfig | None) -> str:
    return texts.SUMMARY_PET.format(
        name=html.quote(pet.name), description=html.quote(pet_description(pet, pets))
    )


def summary_of(booking: Booking, business: BusinessConfig, start: datetime | None = None) -> str:
    """Деталі наявного запису: тривалість і ціна — із запису, тварина — якщо є.

    `start` — інший час (екран перенесення).
    """
    return booking_summary(
        service=booking.service,
        master_name=booking.master.name,
        start=start or booking.start_dt,
        tz=business.booking.tz,
        duration_min=booking.duration_min,
        price=booking.display_price,
        pet=booking.pet,
        pets=business.pets,
        price_from=booking.price_from,
        addon_names=[addon.service.name for addon in booking.addons],
        guests_details=booking.guests_details,
        hide_master=spa_service.is_staffless(business.spa, booking.service.name),
        deposit_line=deposit_line(booking, business.booking.tz),
    )


# --- Адмінка ------------------------------------------------------------------

FUTURE_BOOKINGS_LIMIT = 25  # щоб попередження вмістилося в одне повідомлення Telegram


def status_label(status: BookingStatus) -> str:
    return texts.STATUS_LABELS[status.value]


def admin_day_text(
    day: date, bookings: Sequence[Booking], tz: ZoneInfo, viewer: Viewer = FULL_ACCESS
) -> str:
    """Записи дня, згруповані за майстрами."""
    if not bookings:
        return texts.ADMIN_DAY_EMPTY.format(date=fmt_date(day))
    lines: list[str] = []
    current_master = None
    for booking in bookings:
        if booking.master.name != current_master:
            current_master = booking.master.name
            lines.append(texts.ADMIN_DAY_MASTER.format(master=html.quote(current_master)))
        client = viewer.client_name(booking.client)
        if booking.pet is not None:
            client = texts.ADMIN_DAY_PET.format(client=client, pet=booking.pet.name)
        lines.append(
            texts.ADMIN_DAY_LINE.format(
                icon=(
                    texts.STATUS_ICON_DEPOSIT_PENDING
                    if booking.status is BookingStatus.CONFIRMED and booking.deposit_pending
                    else texts.STATUS_ICON_CLIENT_CONFIRMED
                    if booking.status is BookingStatus.CONFIRMED and booking.client_confirmed_at
                    else texts.STATUS_ICONS[booking.status.value]
                ),
                time=fmt_time(booking.start_dt, tz),
                service=html.quote(booking.service.name),
                client=html.quote(client),
            )
        )
    return texts.ADMIN_DAY.format(date=fmt_date(day), count=len(bookings), lines="\n".join(lines))


def future_bookings_lines(
    bookings: Sequence[Booking], tz: ZoneInfo, viewer: Viewer = FULL_ACCESS
) -> str:
    """Список записів для попередження адміна — з телефонами клієнтів."""
    lines = [
        texts.FUTURE_BOOKING_LINE.format(
            when=fmt_when(b.start_dt, tz),
            service=html.quote(b.service.name),
            master=html.quote(b.master.name),
            client=html.quote(viewer.client_name(b.client)),
            phone=html.quote(viewer.client_phone(b.client) or texts.NO_PHONE),
        )
        for b in bookings[:FUTURE_BOOKINGS_LIMIT]
    ]
    if len(bookings) > FUTURE_BOOKINGS_LIMIT:
        lines.append(texts.FUTURE_BOOKINGS_MORE.format(count=len(bookings) - FUTURE_BOOKINGS_LIMIT))
    return "\n".join(lines)


def schedule_line(weekday: int, hours: WorkingHours | None) -> str:
    name = texts.WEEKDAYS_SHORT[weekday]
    if hours is None:
        return texts.SCHEDULE_DAY.format(weekday=name, hours=texts.SCHEDULE_DAY_OFF)
    text = texts.SCHEDULE_HOURS.format(
        start=f"{hours.start_time:%H:%M}", end=f"{hours.end_time:%H:%M}"
    )
    if hours.break_start is not None:
        text += texts.SCHEDULE_BREAK.format(
            start=f"{hours.break_start:%H:%M}", end=f"{hours.break_end:%H:%M}"
        )
    return texts.SCHEDULE_DAY.format(weekday=name, hours=text)


def schedule_text(working_hours: Sequence[WorkingHours]) -> str:
    by_day = {wh.weekday: wh for wh in working_hours}
    return "\n".join(schedule_line(wd, by_day.get(wd)) for wd in range(7))


def admin_service_text(service: Service, pets: PetsConfig | None = None) -> str:
    text = texts.ADMIN_SERVICE.format(
        name=html.quote(service.name),
        duration=service.duration_min,
        price=service.price,
        status=texts.STATUS_VISIBLE if service.is_active else texts.STATUS_HIDDEN,
    )
    if pets is None:
        return text
    prices = pets.services.get(service.name)
    if prices is None:
        return text + texts.ADMIN_SERVICE_NOT_IN_PRICES

    def category(path: str) -> str:
        # «dog» у прайсі — ціна для всіх собак
        if "/" not in path:
            return texts.ADMIN_SERVICE_ALL_OF_SPECIES.format(species=pets.describe(path))
        return pets.describe(path)

    lines = "\n".join(
        texts.ADMIN_SERVICE_PRICE_LINE.format(
            category=html.quote(category(path)),
            duration=entry.duration_min,
            price=fmt_price(entry.price, entry.price_from),
        )
        for path, entry in prices.prices.items()
    )
    addon = texts.ADMIN_SERVICE_ADDON if prices.addon else ""
    return text + texts.ADMIN_SERVICE_PRICES.format(lines=lines, addon=addon)


def admin_master_text(master: Master) -> str:
    services = ", ".join(html.quote(s.name) for s in master.services) or texts.NO_SERVICES_LINKED
    return texts.ADMIN_MASTER.format(
        name=html.quote(master.name),
        status=texts.STATUS_VISIBLE if master.is_active else texts.STATUS_HIDDEN,
        telegram_id=master.telegram_id or texts.NOT_SET,
        services=services,
        schedule=schedule_text(master.working_hours),
    )


def period_text(start: datetime, end: datetime, tz: ZoneInfo) -> str:
    return texts.PERIOD_FORMAT.format(start=fmt_when(start, tz), end=fmt_when(end, tz))


def time_off_line(time_off: TimeOff, tz: ZoneInfo) -> str:
    reason = texts.TIME_OFF_REASON.format(reason=html.quote(time_off.reason)) if time_off.reason else ""
    return texts.TIME_OFF_LINE.format(
        period=period_text(time_off.start_dt, time_off.end_dt, tz), reason=reason
    )


def time_offs_text(master_name: str, time_offs: Sequence[TimeOff], tz: ZoneInfo) -> str:
    lines = "\n".join(time_off_line(t, tz) for t in time_offs) or texts.TIME_OFFS_EMPTY
    return texts.ADMIN_TIME_OFFS.format(name=html.quote(master_name), lines=lines)


# --- Спа ----------------------------------------------------------------------


def spa_service_button(service: Service, spa: SpaConfig) -> str:
    """Кнопка послуги в розділі: найдешевший варіант — «від», якщо варіантів кілька."""
    duration, price, price_from = spa_service.list_price(service, spa)
    return texts.SERVICE_BUTTON.format(
        name=service.name, duration=duration, price=fmt_price(price, price_from)
    )


def spa_card_text(service: Service, cfg: SpaService, spa: SpaConfig, *, details: bool) -> str:
    """Картка послуги: назва, короткий або повний опис, ціна, аванс, дата події."""
    text = texts.SPA_CARD.format(name=html.quote(service.name))
    if details and cfg.full:
        text += texts.SPA_CARD_FULL.format(text=html.quote(cfg.full))
    elif cfg.short:
        text += texts.SPA_CARD_SHORT.format(text=html.quote(cfg.short))
    if not cfg.variants:
        duration, price, price_from = spa_service.list_price(service, spa)
        text += texts.SPA_CARD_PRICE.format(duration=duration, price=fmt_price(price, price_from))
    if cfg.event is not None:
        days = ", ".join(texts.WEEKDAYS_FULL[wd] for wd in cfg.event.weekdays)
        when = f"{days}, {cfg.event.start:%H:%M}–{cfg.event.end:%H:%M}"
        text += texts.SPA_CARD_EVENT.format(when=when, capacity=cfg.event.capacity)
    if cfg.deposit:
        text += texts.SPA_CARD_DEPOSIT.format(percent=spa.deposit.percent)
    if cfg.variants:
        text += texts.SPA_CARD_VARIANTS
    return text


def guests_text(
    guests: SpaGuests, counts: dict[str, int], quote: "spa_service.Quote", free: int | None = None
) -> str:
    lines = "\n".join(
        texts.GUESTS_LINE.format(label=html.quote(k.label), count=counts.get(k.key, 0))
        for k in guests.kinds
    )
    text = texts.ASK_GUESTS.format(
        lines=lines, price=fmt_price(quote.price, quote.price_from), duration=quote.duration_min
    )
    if free is not None:
        text += texts.GUESTS_FREE_SEATS.format(free=free)
    return text


def spa_addon_button(addon: Service, spa: SpaConfig) -> str:
    text = texts.SPA_ADDON_BUTTON.format(name=addon.name, price=addon.price)
    cfg = spa.service(addon.name)
    if cfg is not None and cfg.extra_min:
        text += texts.SPA_ADDON_EXTRA.format(minutes=cfg.extra_min)
    return text


def event_date_button(start: datetime, free: int, tz: ZoneInfo) -> str:
    return texts.EVENT_DATE_BUTTON.format(
        date=fmt_date(start.astimezone(tz).date()), time=fmt_time(start, tz), free=free
    )


def deposit_confirm_text(amount: int, spa: SpaConfig) -> str:
    """Аванс і коротко правила — на екрані підтвердження, перед оплатою."""
    return texts.CONFIRM_DEPOSIT.format(
        percent=spa.deposit.percent, amount=amount
    ) + texts.CONFIRM_DEPOSIT_RULES.format(
        rules=html.quote(spa.deposit.rules), hold=spa.deposit.hold_min
    )


def booking_held_text(
    *, booking: Booking, summary: str, address: str, tz: ZoneInfo, demo_mode: bool
) -> str:
    return texts.BOOKING_HELD.format(
        until=fmt_time(booking.hold_until, tz),
        summary=summary,
        address=html.quote(address),
        amount=booking.deposit,
        how=texts.BOOKING_HELD_DEMO if demo_mode else texts.BOOKING_HELD_MANUAL,
    )


def deposit_paid_text(
    *, summary: str, address: str, reminders: PlannedReminders, demo_mode: bool
) -> str:
    return texts.DEPOSIT_PAID.format(
        demo=texts.DEPOSIT_DEMO_MARK if demo_mode else "",
        summary=summary,
        address=html.quote(address),
        after=_after_booking(reminders),
    )
