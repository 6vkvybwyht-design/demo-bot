"""Тварини клієнтів і прайс за категорією тварини (профілі з розділом pets, наприклад грумінг)."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import PetsConfig
from bot.db.models import Client, Pet, Service


@dataclass(frozen=True)
class Offer:
    """Тривалість і ціна послуги для конкретної тварини."""

    duration_min: int
    price: int
    price_from: bool = False  # «від X грн»: точна ціна після огляду майстром


def offer_for(service: Service, category: str | None, pets: PetsConfig | None) -> Offer | None:
    """Тривалість і ціна послуги для категорії тварини.

    None — послуга недоступна для тварини (у прайсі послуги немає її категорії).
    Без прайсу (або без тварин у профілі) — тривалість і ціна з самої послуги.
    """
    base = Offer(service.duration_min, service.price)
    if pets is None or category is None or service.name not in pets.services:
        return base
    entry = pets.price_for(service.name, category)
    if entry is None:
        return None
    return Offer(entry.duration_min, entry.price, entry.price_from)


def is_addon(service: Service, pets: PetsConfig | None) -> bool:
    return pets is not None and pets.is_addon(service.name)


def total_offer(offers: Iterable[Offer]) -> Offer:
    """Основна послуга з додатковими: тривалості й ціни сумуються, «від» — якщо хоч одна «від»."""
    offers = list(offers)
    return Offer(
        duration_min=sum(o.duration_min for o in offers),
        price=sum(o.price for o in offers),
        price_from=any(o.price_from for o in offers),
    )


def pet_description(pet: Pet, pets: PetsConfig | None) -> str:
    """«собака, шпіц, до 4 кг» — для карток записів і сповіщень."""
    return pets.describe(pet.category) if pets is not None else pet.breed


def usable_pets(pets: Sequence[Pet], config: PetsConfig) -> list[Pet]:
    """Тварини, чия категорія є в поточному прайсі (після зміни прайсу старі можуть зникнути)."""
    valid = set(config.categories())
    return [pet for pet in pets if pet.category in valid]


async def client_pets(session: AsyncSession, telegram_id: int) -> list[Pet]:
    result = await session.scalars(
        select(Pet)
        .join(Client, Client.id == Pet.client_id)
        .where(Client.telegram_id == telegram_id)
        .order_by(Pet.id)
    )
    return list(result.all())


async def get_own_pet(session: AsyncSession, pet_id: int, telegram_id: int) -> Pet | None:
    return await session.scalar(
        select(Pet)
        .join(Client, Client.id == Pet.client_id)
        .where(Pet.id == pet_id, Client.telegram_id == telegram_id)
    )


async def create_pet(
    session: AsyncSession, client: Client, *, name: str, species: str, category: str, breed: str
) -> Pet:
    pet = Pet(client_id=client.id, name=name, species=species, category=category, breed=breed)
    session.add(pet)
    await session.commit()
    return pet


class PetInputError(ValueError):
    """Кличка не підходить за довжиною."""


def parse_pet_name(text: str) -> str:
    value = " ".join(text.split())
    if not value or len(value) > 30:
        raise PetInputError
    return value


ServiceOffer = tuple[Service, Offer]


def offers_for_pet(
    services: Sequence[Service], category: str | None, pets: PetsConfig | None
) -> tuple[list[ServiceOffer], list[ServiceOffer]]:
    """Доступні тварині послуги з її цінами: (основні, додаткові)."""
    main: list[ServiceOffer] = []
    addons: list[ServiceOffer] = []
    for service in services:
        offer = offer_for(service, category, pets)
        if offer is not None:
            (addons if is_addon(service, pets) else main).append((service, offer))
    return main, addons


async def selected_addons(
    session: AsyncSession, addon_ids: Iterable[int], category: str | None, pets: PetsConfig | None
) -> list[ServiceOffer]:
    """Обрані додаткові послуги з цінами (перераховуємо з прайсу, а не довіряємо збереженим)."""
    result = []
    for service_id in addon_ids:
        service = await session.get(Service, service_id)
        if service is None or not service.is_active or not is_addon(service, pets):
            continue
        offer = offer_for(service, category, pets)
        if offer is not None:
            result.append((service, offer))
    return result
