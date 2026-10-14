"""Прості запити до БД. Бізнес-логіка — у bot/services/."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Master, MasterService, Service


async def get_active_services(session: AsyncSession) -> Sequence[Service]:
    result = await session.scalars(
        select(Service).where(Service.is_active).order_by(Service.id)
    )
    return result.all()


async def get_masters_for_service(session: AsyncSession, service_id: int) -> Sequence[Master]:
    """Активні майстри, які виконують послугу."""
    result = await session.scalars(
        select(Master)
        .join(MasterService, MasterService.master_id == Master.id)
        .where(MasterService.service_id == service_id, Master.is_active)
        .order_by(Master.id)
    )
    return result.all()
