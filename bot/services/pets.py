"""Тварини клієнтів і прайс за розміром (профілі з розділом pets, наприклад грумінг)."""

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


def offer_for(service: Service, size: str | None, pets: PetsConfig | None) -> Offer | None:
    """Тривалість і ціна послуги для розміру тварини.

    None — послуга недоступна для тварин цього розміру (у прайсі немає розміру).
    Без прайсу (або без тварин у профілі) — тривалість і ціна з самої послуги.
    """
    base = Offer(service.duration_min, service.price)
    if pets is None or size is None:
        return base
    row = pets.prices.get(service.name)
    if row is None:
        return base
    offer = row.get(size)
    return Offer(offer.duration_min, offer.price) if offer is not None else None


def pet_description(pet: Pet, pets: PetsConfig | None) -> str:
    """«собака, лабрадор, великий» — для карток записів і сповіщень."""
    if pets is None:
        return pet.breed
    return ", ".join([pets.species_name(pet.species), pet.breed, pets.size_name(pet.size)])


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
    session: AsyncSession, client: Client, *, name: str, species: str, breed: str, size: str
) -> Pet:
    pet = Pet(client_id=client.id, name=name, species=species, breed=breed, size=size)
    session.add(pet)
    await session.commit()
    return pet


class PetInputError(ValueError):
    """Кличка чи порода не підходить за довжиною."""


def _clean(text: str, max_length: int) -> str:
    value = " ".join(text.split())
    if not value or len(value) > max_length:
        raise PetInputError
    return value


def parse_pet_name(text: str) -> str:
    return _clean(text, 30)


def parse_pet_breed(text: str) -> str:
    return _clean(text, 50)
