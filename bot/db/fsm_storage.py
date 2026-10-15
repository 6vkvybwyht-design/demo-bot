"""Сховище стану FSM aiogram у базі (замість MemoryStorage)."""

from collections.abc import Mapping
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, DefaultKeyBuilder, KeyBuilder, StorageKey
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.db.models import FsmState


class DbStorage(BaseStorage):
    """Зберігає стан і дані діалогу в таблиці fsm_states.

    Рядок без стану й без даних видаляємо, щоб таблиця не росла.
    """

    def __init__(self, session_factory: async_sessionmaker, key_builder: KeyBuilder | None = None):
        self.session_factory = session_factory
        self.key_builder = key_builder or DefaultKeyBuilder(with_bot_id=True, with_destiny=True)

    async def _save(self, key: StorageKey, **fields: Any) -> None:
        db_key = self.key_builder.build(key)
        for attempt in range(2):
            try:
                await self._save_once(db_key, fields)
                return
            except IntegrityError:
                # Два оновлення того ж користувача одночасно (подвійне натискання):
                # інше вже вставило рядок — повторюємо як оновлення
                if attempt:
                    raise

    async def _save_once(self, db_key: str, fields: dict[str, Any]) -> None:
        async with self.session_factory() as session:
            row = await session.get(FsmState, db_key)
            state = fields["state"] if "state" in fields else (row.state if row else None)
            data = fields["data"] if "data" in fields else (row.data if row else {})
            if state is None and not data:
                if row is not None:
                    await session.delete(row)
            elif row is None:
                session.add(FsmState(key=db_key, state=state, data=data))
            else:
                row.state, row.data = state, data
            await session.commit()

    async def _load(self, key: StorageKey) -> FsmState | None:
        async with self.session_factory() as session:
            return await session.get(FsmState, self.key_builder.build(key))

    async def set_state(self, key: StorageKey, state: str | State | None = None) -> None:
        value = state.state if isinstance(state, State) else state
        await self._save(key, state=value)

    async def get_state(self, key: StorageKey) -> str | None:
        row = await self._load(key)
        return row.state if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        # Копія як звичайний dict — JSON-колонка має бачити нове значення
        await self._save(key, data=dict(data))

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        row = await self._load(key)
        return dict(row.data) if row and row.data else {}

    async def close(self) -> None:
        pass
