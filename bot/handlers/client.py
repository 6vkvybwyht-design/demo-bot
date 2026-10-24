"""Хендлери клієнтського сценарію: /start і запис на послугу."""

from datetime import UTC, date, datetime

from aiogram import Bot, F, Router, html
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.config import BusinessConfig, Settings
from bot.db import repo
from bot.db.models import Master, Pet, Service, utcnow
from bot.keyboards import booking as kb
from bot.keyboards.callbacks import (
    ANY_MASTER,
    NEW_PET,
    AddonCb,
    DateCb,
    MasterCb,
    NavCb,
    NoopCb,
    PetCb,
    PetOptionCb,
    ServiceCb,
    TimeCb,
)
from bot.keyboards.main_menu import main_menu_kb
from bot.services import booking as booking_service
from bot.services import notifications, slots
from bot.services import pets as pets_service
from bot.services import spa as spa_service
from bot.services.booking import SlotUnavailableError
from bot.services.formatting import (
    booking_done_text,
    booking_summary,
    contacts_text,
    deposit_confirm_text,
    progress_text,
    summary_of,
)
from bot.services.phone import normalize_contact_phone, parse_ua_phone
from bot.states import BookingStates, RescheduleStates

router = Router(name="client")

Event = Message | CallbackQuery


def menu_kb(settings: Settings, business: BusinessConfig):
    """Головне меню; у спа — ще «Сертифікати» й «Правила відвідування»."""
    extra = [texts.BTN_CERTIFICATE, texts.BTN_RULES] if business.spa is not None else []
    return main_menu_kb(settings.demo_mode, extra)


@router.message(CommandStart())
async def cmd_start(
    message: Message, business: BusinessConfig, state: FSMContext, settings: Settings
) -> None:
    await state.clear()
    await message.answer(
        texts.WELCOME.format(name=html.quote(business.business.name)),
        reply_markup=menu_kb(settings, business),
    )


# --- Показ кроків -------------------------------------------------------------


async def _render(event: Event, text: str, reply_markup=None) -> None:
    """Для кнопки редагуємо поточне повідомлення, для тексту — надсилаємо нове."""
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=reply_markup)
    else:
        await event.answer(text, reply_markup=reply_markup)


@router.message(F.text == texts.BTN_CONTACTS)
async def show_contacts(message: Message, business: BusinessConfig) -> None:
    await message.answer(
        contacts_text(business.business), reply_markup=kb.contacts_kb(business.business)
    )


async def _moving_booking_id(state: FSMContext) -> int | None:
    """id запису, якщо зараз триває перенесення, інакше None (звичайний запис)."""
    return (await state.get_data()).get("booking_id")


def _staff(data: dict) -> dict:
    """Скільки майстрів потрібно одночасно (спа: масаж для двох) і, при перенесенні, хто другий."""
    staff = {"staff_count": data.get("staff_count", 1)}
    if data.get("partner_id"):
        staff["partner_id"] = data["partner_id"]
    return staff


def _resources(data: dict, business: BusinessConfig) -> spa_service.ResourceFilter | None:
    """Ресурси спа (кабінети, термальна зона), які займе запис, — для фільтра слотів."""
    plan = data.get("resource_plan")
    return spa_service.resource_filter(business.spa, spa_service.plan_from_data(plan) if plan else None)


async def _load_choice(session: AsyncSession, state: FSMContext) -> tuple[Service, int | None]:
    data = await state.get_data()
    service = await session.get(Service, data["service_id"])
    return service, data.get("master_id")


# Порядок кроків для наростаючого підсумку
_PROGRESS_STEPS = ("service", "master", "day", "start", "phone")


async def _progress(
    prompt: str,
    state: FSMContext,
    session: AsyncSession,
    business: BusinessConfig,
    *,
    upto: str,
) -> str:
    """Підказка кроку з підсумком уже зроблених виборів (усіх кроків до `upto`)."""
    done = _PROGRESS_STEPS[: _PROGRESS_STEPS.index(upto)]
    data = await state.get_data()
    master_id = data.get("master_id")
    master = await session.get(Master, master_id) if "master" in done and master_id else None
    service = await session.get(Service, data["service_id"]) if "service" in done else None
    # Послуга без майстра (спа: відпочинок у термальній зоні) — майстра в підсумку не показуємо
    staffless = service is not None and spa_service.is_staffless(business.spa, service.name)
    return progress_text(
        prompt,
        pet_name=data.get("pet_name"),
        service=service,
        master_name=master.name if master else None,
        any_master="master" in done and master_id is None and not staffless,
        day=date.fromisoformat(data["day"]) if "day" in done else None,
        start=datetime.fromisoformat(data["start"]) if "start" in done else None,
        tz=business.booking.tz,
    )


async def show_services(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    services = await repo.get_active_services(session)
    if not services:
        await state.clear()
        await _render(event, texts.NO_SERVICES.format(phone=business.business.phone))
        return
    data = await state.get_data()
    offers = None
    if business.pets is not None and "pet_category" in data:
        # Основні послуги з цінами для цієї тварини; недоступні їй — не показуємо
        main, _ = pets_service.offers_for_pet(services, data["pet_category"], business.pets)
        services = [service for service, _ in main]
        offers = {service.id: offer for service, offer in main}
        if not services:
            await state.set_state(BookingStates.service)
            await _render(
                event, texts.NO_SERVICES_FOR_PET.format(phone=business.business.phone), kb.back_kb()
            )
            return
    await state.set_state(BookingStates.service)
    text = await _progress(texts.CHOOSE_SERVICE, state, session, business, upto="service")
    await _render(event, text, kb.services_kb(services, offers))


async def show_masters(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    service, _ = await _load_choice(session, state)
    masters = await slots.masters_for_service(session, service.id)
    await state.set_state(BookingStates.master)
    if not masters:
        await _render(event, texts.NO_MASTERS, kb.back_kb())
        return
    text = await _progress(texts.CHOOSE_MASTER, state, session, business, upto="master")
    await _render(event, text, kb.masters_kb(masters))


async def show_dates(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    service, master_id = await _load_choice(session, state)
    moving_id = await _moving_booking_id(state)
    rules = business.booking
    data = await state.get_data()
    dates = await slots.get_available_dates(
        session, service=service, master_id=master_id, rules=rules, now=utcnow(),
        exclude_booking_id=moving_id, duration_min=data.get("duration_min"),
        resources=_resources(data, business), **_staff(data),
    )
    await state.set_state(RescheduleStates.date if moving_id else BookingStates.date)
    if not dates:
        text = texts.NO_DATES.format(days=rules.horizon_days, phone=business.business.phone)
        await _render(event, text, kb.back_kb())
        return
    prompt = texts.MOVE_CHOOSE_DATE if moving_id else texts.CHOOSE_DATE
    text = await _progress(prompt, state, session, business, upto="day")
    await _render(event, text, kb.dates_kb(dates))


async def _free_starts(
    session: AsyncSession,
    service: Service,
    master_id: int | None,
    day: date,
    business: BusinessConfig,
    exclude_booking_id: int | None,
    duration_min: int | None,
    resources: spa_service.ResourceFilter | None = None,
    staff: dict | None = None,
) -> list[datetime]:
    staff = staff or {}
    common = dict(
        service=service, day=day, rules=business.booking, now=utcnow(), duration_min=duration_min,
        resources=resources, staff_count=staff.get("staff_count", 1),
    )
    if master_id is None:
        return list(await slots.get_free_slots_any_master(session, **common))
    return await slots.get_free_slots(
        session, master_id=master_id, exclude_booking_id=exclude_booking_id,
        partner_id=staff.get("partner_id"), **common
    )


async def show_times(
    event: Event,
    state: FSMContext,
    session: AsyncSession,
    business: BusinessConfig,
    notice: str | None = None,
) -> None:
    service, master_id = await _load_choice(session, state)
    moving_id = await _moving_booking_id(state)
    data = await state.get_data()
    day = date.fromisoformat(data["day"])
    starts = await _free_starts(
        session, service, master_id, day, business, moving_id, data.get("duration_min"),
        _resources(data, business), _staff(data),
    )
    if not starts:
        # Поки клієнт обирав, вільний час на цю дату закінчився
        await show_dates(event, state, session, business)
        return
    await state.set_state(RescheduleStates.time if moving_id else BookingStates.time)
    prompt = texts.MOVE_CHOOSE_TIME if moving_id else texts.CHOOSE_TIME
    text = await _progress(prompt, state, session, business, upto="start")
    if notice:
        text = f"{notice}\n\n{text}"
    await _render(event, text, kb.times_kb(starts, business.booking.tz))


async def show_confirm(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    service, master_id = await _load_choice(session, state)
    data = await state.get_data()
    start = datetime.fromisoformat(data["start"])
    master = await session.get(Master, master_id) if master_id else None
    pet = await session.get(Pet, data["pet_id"]) if data.get("pet_id") else None
    if business.spa is not None:
        addon_names = [(await session.get(Service, i)).name for i in data.get("addon_ids", [])]
    else:
        addons = await pets_service.selected_addons(
            session, data.get("addon_ids", []), data.get("pet_category"), business.pets
        )
        addon_names = [addon.name for addon, _ in addons]
    summary = booking_summary(
        service=service,
        master_name=master.name if master else None,
        start=start,
        tz=business.booking.tz,
        duration_min=data.get("duration_min"),
        price=data.get("price"),
        pet=pet,
        pets=business.pets,
        price_from=data.get("price_from", False),
        addon_names=addon_names,
        guests_details=data.get("guests_details"),
        hide_master=spa_service.is_staffless(business.spa, service.name),
        staff_count=data.get("staff_count", 1),
    )
    text = texts.CONFIRM.format(summary=summary)
    if data.get("deposit"):
        # Аванс і коротко правила — до оплати
        text += deposit_confirm_text(data["deposit"], business.spa)
    await state.set_state(BookingStates.confirm)
    await _render(event, text, kb.confirm_kb())


# --- Кроки запису -------------------------------------------------------------


@router.message(F.text == texts.BTN_BOOK)
async def start_booking(
    message: Message, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await state.clear()
    if business.pets is not None:
        await show_pets(message, state, session, business)
    else:
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
    if business.pets is None:
        await state.update_data(service_id=service.id)
        await show_masters(callback, state, session, business)
        return
    category = (await state.get_data()).get("pet_category")
    offer = pets_service.offer_for(service, category, business.pets)
    if offer is None or pets_service.is_addon(service, business.pets):
        # Недоступна цій тварині або додаткова послуга (стара кнопка)
        await show_services(callback, state, session, business)
        return
    await state.update_data(service_id=service.id, addon_ids=[])
    _, addons = pets_service.offers_for_pet(
        await repo.get_active_services(session), category, business.pets
    )
    if addons:
        await show_addons(callback, state, session, business)
    else:
        await _set_totals(state, session, business, has_addons=False)
        await show_masters(callback, state, session, business)


@router.callback_query(BookingStates.master, MasterCb.filter())
async def pick_master(
    callback: CallbackQuery, callback_data: MasterCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    master_id = None if callback_data.master_id == ANY_MASTER else callback_data.master_id
    await state.update_data(master_id=master_id)
    await show_dates(callback, state, session, business)


@router.callback_query(StateFilter(BookingStates.date, RescheduleStates.date), DateCb.filter())
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
    await ask_phone_or_confirm(callback, state, session, business)


async def ask_phone_or_confirm(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    """Після вибору часу: телефон (якщо ще не знаємо) або одразу підтвердження."""
    client = await booking_service.get_client(session, callback.from_user.id)
    if client is not None and client.phone:
        await show_confirm(callback, state, session, business)
        return

    await state.set_state(BookingStates.phone)
    await callback.message.edit_text(
        await _progress(texts.PHONE_STEP, state, session, business, upto="phone")
    )
    await callback.message.answer(texts.ASK_PHONE, reply_markup=kb.share_phone_kb())


@router.message(BookingStates.phone, F.contact)
async def got_phone(
    message: Message, state: FSMContext, session: AsyncSession, business: BusinessConfig,
    settings: Settings,
) -> None:
    if message.contact.user_id != message.from_user.id:
        await message.answer(texts.PHONE_NOT_OWN, reply_markup=kb.share_phone_kb())
        return
    phone = normalize_contact_phone(message.contact.phone_number)
    await _phone_received(message, phone, state, session, business, settings)


async def _phone_received(
    message: Message, phone: str, state: FSMContext, session: AsyncSession,
    business: BusinessConfig, settings: Settings,
) -> None:
    await booking_service.save_client_phone(
        session, message.from_user.id, message.from_user.full_name, phone
    )
    await message.answer(texts.PHONE_SAVED, reply_markup=menu_kb(settings, business))
    await show_confirm(message, state, session, business)


@router.message(BookingStates.phone, F.text == texts.BTN_BACK)
async def phone_back(
    message: Message, state: FSMContext, session: AsyncSession, business: BusinessConfig,
    settings: Settings,
) -> None:
    await message.answer(texts.BACK_TO_TIMES, reply_markup=menu_kb(settings, business))
    await show_times(message, state, session, business)


@router.message(BookingStates.phone, F.text)
async def got_phone_text(
    message: Message, state: FSMContext, session: AsyncSession, business: BusinessConfig,
    settings: Settings,
) -> None:
    phone = parse_ua_phone(message.text)
    if phone is None:
        await message.answer(texts.PHONE_INVALID, reply_markup=kb.share_phone_kb())
        return
    await _phone_received(message, phone, state, session, business, settings)


@router.message(BookingStates.phone)
async def phone_expected(message: Message) -> None:
    # Стікер, фото тощо замість номера
    await message.answer(texts.PHONE_INVALID, reply_markup=kb.share_phone_kb())


@router.callback_query(BookingStates.confirm, NavCb.filter(F.action == "confirm"))
async def confirm_booking(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig,
    bot: Bot, settings: Settings,
) -> None:
    service, master_id = await _load_choice(session, state)
    start = datetime.fromisoformat((await state.get_data())["start"])
    client = await booking_service.get_or_create_client(
        session, callback.from_user.id, callback.from_user.full_name
    )
    data = await state.get_data()
    addons = await pets_service.selected_addons(
        session, data.get("addon_ids", []), data.get("pet_category"), business.pets
    )
    try:
        booking = await booking_service.create_booking(
            session, client=client, service=service, master_id=master_id,
            start=start, rules=business.booking, now=utcnow(), reminders=business.reminders,
            duration_min=data.get("duration_min"), price=data.get("price"),
            pet_id=data.get("pet_id"), price_from=data.get("price_from", False),
            addons=[(addon.id, offer) for addon, offer in addons],
        )
    except SlotUnavailableError:
        await show_times(callback, state, session, business, notice=texts.SLOT_TAKEN)
        return

    await state.clear()
    booking = await notifications.load_booking(session, booking.id)
    summary = summary_of(booking, business)
    reminders = booking_service.planned_reminders(
        booking.start_dt, booking.created_at, business.reminders
    )
    await callback.message.edit_text(
        booking_done_text(summary=summary, address=business.business.address, reminders=reminders)
    )
    owner_text = await notifications.notify_staff(
        bot, session, booking_id=booking.id, template=texts.NOTIFY_NEW,
        admin_ids=settings.admin_ids, business=business,
        demo_mode=settings.demo_mode, actor_id=callback.from_user.id,
    )
    if settings.demo_mode:
        # Відвідувач демо одразу бачить і другу сторону — сповіщення власника
        await callback.message.answer(texts.DEMO_OWNER_VIEW.format(notification=owner_text))


@router.callback_query(NoopCb.filter())
async def noop(callback: CallbackQuery) -> None:
    # Заголовок групи слотів: нічого не робимо, «годинник» прибере CallbackAnswerMiddleware
    pass


# --- Тварина (профілі з розділом pets) ----------------------------------------


def _not_menu_button(text: str) -> bool:
    """Текст кнопки головного меню — не кличка (меню видно під час введення)."""
    return text not in {
        texts.BTN_BOOK, texts.BTN_MY_BOOKINGS, texts.BTN_CONTACTS, texts.BTN_DEMO_OWNER,
        texts.BTN_CERTIFICATE, texts.BTN_RULES,
    }


async def show_pets(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    """Збережені тварини клієнта або одразу введення нової."""
    pets = pets_service.usable_pets(
        await pets_service.client_pets(session, event.from_user.id), business.pets
    )
    await state.set_data({})
    if not pets:
        await ask_pet_name(event, state)
        return
    await state.set_state(BookingStates.pet)
    await _render(event, texts.CHOOSE_PET, kb.pets_kb(pets, business.pets))


async def ask_pet_name(event: Event, state: FSMContext) -> None:
    await state.set_state(BookingStates.pet_name)
    await _render(event, texts.ASK_PET_NAME, kb.back_kb())


async def _ask_species(event: Event, state: FSMContext, business: BusinessConfig) -> None:
    name = (await state.get_data())["new_pet_name"]
    await state.set_state(BookingStates.pet_species)
    await _render(
        event, texts.ASK_PET_SPECIES.format(name=html.quote(name)), kb.pet_species_kb(business.pets)
    )


async def _ask_breed(event: Event, state: FSMContext, business: BusinessConfig) -> None:
    species = business.pets.species_by_key((await state.get_data())["new_pet_species"])
    await state.set_state(BookingStates.pet_breed)
    await _render(event, texts.ASK_PET_BREED, kb.pet_breed_kb(species))


async def _ask_weight(event: Event, state: FSMContext, business: BusinessConfig) -> None:
    data = await state.get_data()
    weights = business.pets.weights_for(data["new_pet_species"], data["new_pet_breed"])
    await state.set_state(BookingStates.pet_weight)
    await _render(
        event,
        texts.ASK_PET_WEIGHT.format(name=html.quote(data["new_pet_name"])),
        kb.pet_weight_kb(weights),
    )


async def _save_pet(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig,
    *, breed: str | None, weight: str | None,
) -> None:
    """Зберігаємо нову тварину й переходимо до послуг."""
    data = await state.get_data()
    species = data["new_pet_species"]
    try:
        category = business.pets.category_path(species, breed, weight)
    except ValueError:  # стара кнопка або змінений прайс
        await _ask_species(callback, state, business)
        return
    client = await booking_service.get_or_create_client(
        session, callback.from_user.id, callback.from_user.full_name
    )
    pet = await pets_service.create_pet(
        session, client, name=data["new_pet_name"], species=species, category=category,
        breed=business.pets.describe(category, with_species=False),
    )
    await _use_pet(callback, pet, state, session, business)


async def _use_pet(
    event: Event, pet: Pet, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await state.set_data({"pet_id": pet.id, "pet_category": pet.category, "pet_name": pet.name})
    await show_services(event, state, session, business)


@router.callback_query(BookingStates.pet, PetCb.filter())
async def pick_pet(
    callback: CallbackQuery, callback_data: PetCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    if callback_data.pet_id == NEW_PET:
        await ask_pet_name(callback, state)
        return
    pet = await pets_service.get_own_pet(session, callback_data.pet_id, callback.from_user.id)
    if pet is None or not pets_service.usable_pets([pet], business.pets):
        await show_pets(callback, state, session, business)
        return
    await _use_pet(callback, pet, state, session, business)


@router.message(BookingStates.pet_name, F.text.func(_not_menu_button))
async def got_pet_name(message: Message, state: FSMContext, business: BusinessConfig) -> None:
    try:
        name = pets_service.parse_pet_name(message.text)
    except pets_service.PetInputError:
        await message.answer(texts.INVALID_PET_NAME, reply_markup=kb.back_kb())
        return
    await state.update_data(new_pet_name=name)
    await _ask_species(message, state, business)


@router.callback_query(BookingStates.pet_species, PetOptionCb.filter(F.kind == "species"))
async def got_pet_species(
    callback: CallbackQuery, callback_data: PetOptionCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    species = business.pets.species_by_key(callback_data.key)
    if species is None:
        await _ask_species(callback, state, business)
        return
    await state.update_data(new_pet_species=species.key, new_pet_breed=None)
    if species.breeds or species.other_label:
        await _ask_breed(callback, state, business)
    else:
        # Без порід і ваги (кролик) — категорія одразу відома
        await _save_pet(callback, state, session, business, breed=None, weight=None)


@router.callback_query(BookingStates.pet_breed, PetOptionCb.filter(F.kind == "breed"))
async def got_pet_breed(
    callback: CallbackQuery, callback_data: PetOptionCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    species = (await state.get_data())["new_pet_species"]
    await state.update_data(new_pet_breed=callback_data.key)
    if business.pets.weights_for(species, callback_data.key):
        await _ask_weight(callback, state, business)
    else:
        await _save_pet(callback, state, session, business, breed=callback_data.key, weight=None)


@router.callback_query(BookingStates.pet_weight, PetOptionCb.filter(F.kind == "weight"))
async def got_pet_weight(
    callback: CallbackQuery, callback_data: PetOptionCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    breed = (await state.get_data())["new_pet_breed"]
    await _save_pet(callback, state, session, business, breed=breed, weight=callback_data.key)


@router.callback_query(BookingStates.pet, NavCb.filter(F.action == "back"))
async def back_from_pet(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(texts.BACK_TO_MENU)


@router.callback_query(BookingStates.pet_name, NavCb.filter(F.action == "back"))
async def back_from_pet_name(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    if pets_service.usable_pets(
        await pets_service.client_pets(session, callback.from_user.id), business.pets
    ):
        await show_pets(callback, state, session, business)
        return
    await state.clear()
    await callback.message.edit_text(texts.BACK_TO_MENU)


@router.callback_query(BookingStates.pet_species, NavCb.filter(F.action == "back"))
async def back_from_pet_species(callback: CallbackQuery, state: FSMContext) -> None:
    await ask_pet_name(callback, state)


@router.callback_query(BookingStates.pet_breed, NavCb.filter(F.action == "back"))
async def back_from_pet_breed(
    callback: CallbackQuery, state: FSMContext, business: BusinessConfig
) -> None:
    await _ask_species(callback, state, business)


@router.callback_query(BookingStates.pet_weight, NavCb.filter(F.action == "back"))
async def back_from_pet_weight(
    callback: CallbackQuery, state: FSMContext, business: BusinessConfig
) -> None:
    species = business.pets.species_by_key((await state.get_data())["new_pet_species"])
    if species.breeds:
        await _ask_breed(callback, state, business)
    else:
        await _ask_species(callback, state, business)


# --- Додаткові послуги ----------------------------------------------------------


async def _available_addons(
    state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> list[pets_service.ServiceOffer]:
    _, addons = pets_service.offers_for_pet(
        await repo.get_active_services(session),
        (await state.get_data()).get("pet_category"),
        business.pets,
    )
    return addons


async def show_addons(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    data = await state.get_data()
    service = await session.get(Service, data["service_id"])
    addons = await _available_addons(state, session, business)
    await state.set_state(BookingStates.addons)
    prompt = texts.ASK_ADDONS.format(service=html.quote(service.name))
    text = await _progress(prompt, state, session, business, upto="master")
    await _render(event, text, kb.addons_kb(addons, set(data.get("addon_ids", []))))


async def _set_totals(
    state: FSMContext, session: AsyncSession, business: BusinessConfig, *, has_addons: bool
) -> None:
    """Тривалість і ціна запису: основна послуга + обрані додаткові (з прайсу).

    `has_addons` — чи був крок додаткових послуг (куди вести «Назад» із вибору майстра).
    """
    data = await state.get_data()
    service = await session.get(Service, data["service_id"])
    main = pets_service.offer_for(service, data.get("pet_category"), business.pets)
    addons = await pets_service.selected_addons(
        session, data.get("addon_ids", []), data.get("pet_category"), business.pets
    )
    total = pets_service.total_offer([main, *(offer for _, offer in addons)])
    await state.update_data(
        duration_min=total.duration_min, price=total.price, price_from=total.price_from,
        addon_ids=[addon.id for addon, _ in addons], has_addons=has_addons,
    )


@router.callback_query(BookingStates.addons, AddonCb.filter())
async def toggle_addon(
    callback: CallbackQuery, callback_data: AddonCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    available = {service.id for service, _ in await _available_addons(state, session, business)}
    selected = set((await state.get_data()).get("addon_ids", []))
    if callback_data.service_id in available:
        selected ^= {callback_data.service_id}
    await state.update_data(addon_ids=sorted(selected))
    await show_addons(callback, state, session, business)


@router.callback_query(BookingStates.addons, NavCb.filter(F.action == "next"))
async def addons_done(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await _set_totals(state, session, business, has_addons=True)
    await show_masters(callback, state, session, business)


@router.callback_query(BookingStates.addons, NavCb.filter(F.action == "back"))
async def back_from_addons(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await show_services(callback, state, session, business)


# --- Назад --------------------------------------------------------------------


@router.callback_query(BookingStates.service, NavCb.filter(F.action == "back"))
async def back_from_service(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    if business.pets is not None:
        await show_pets(callback, state, session, business)
        return
    await state.clear()
    await callback.message.edit_text(texts.BACK_TO_MENU)


@router.callback_query(BookingStates.master, NavCb.filter(F.action == "back"))
async def back_from_master(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    if (await state.get_data()).get("has_addons"):
        await show_addons(callback, state, session, business)
        return
    await show_services(callback, state, session, business)


@router.callback_query(BookingStates.date, NavCb.filter(F.action == "back"))
async def back_from_date(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await show_masters(callback, state, session, business)


@router.callback_query(
    StateFilter(BookingStates.time, RescheduleStates.time), NavCb.filter(F.action == "back")
)
async def back_from_time(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await show_dates(callback, state, session, business)


@router.callback_query(BookingStates.confirm, NavCb.filter(F.action == "back"))
async def back_from_confirm(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await show_times(callback, state, session, business)
