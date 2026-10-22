"""Моделі БД. Усі datetime зберігаються в UTC."""

from datetime import UTC, datetime, time
from enum import StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Time,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Сталі імена обмежень, щоб міграції однаково працювали на SQLite і PostgreSQL
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class UTCDateTime(TypeDecorator):
    """Datetime, що завжди зберігається й повертається як aware UTC.

    SQLite не зберігає часовий пояс, тому при читанні додаємо UTC самі.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Очікується datetime з часовим поясом (aware)")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class BookingStatus(StrEnum):
    CONFIRMED = "confirmed"
    CANCELLED_BY_CLIENT = "cancelled_by_client"
    CANCELLED_BY_ADMIN = "cancelled_by_admin"
    COMPLETED = "completed"
    NO_SHOW = "no_show"


class MasterService(Base):
    __tablename__ = "master_services"

    master_id: Mapped[int] = mapped_column(
        ForeignKey("masters.id", ondelete="CASCADE"), primary_key=True
    )
    service_id: Mapped[int] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), primary_key=True
    )


class Service(Base):
    __tablename__ = "services"
    __table_args__ = (
        CheckConstraint("duration_min > 0", name="duration_positive"),
        CheckConstraint("price >= 0", name="price_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    duration_min: Mapped[int] = mapped_column(Integer)
    price: Mapped[int] = mapped_column(Integer)  # у гривнях, без копійок
    is_active: Mapped[bool] = mapped_column(default=True)

    masters: Mapped[list["Master"]] = relationship(
        secondary="master_services", back_populates="services"
    )


class Master(Base):
    __tablename__ = "masters"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(default=True)
    # Необов'язково: щоб майстер теж отримував сповіщення
    telegram_id: Mapped[int | None] = mapped_column(BigInteger)

    services: Mapped[list[Service]] = relationship(
        secondary="master_services", back_populates="masters"
    )
    working_hours: Mapped[list["WorkingHours"]] = relationship(
        back_populates="master", cascade="all, delete-orphan", passive_deletes=True
    )


class WorkingHours(Base):
    """Робочі години майстра на день тижня (0 — понеділок). Час — місцевий, бізнесу."""

    __tablename__ = "working_hours"
    __table_args__ = (
        UniqueConstraint("master_id", "weekday", name="uq_working_hours_master_weekday"),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday_range"),
        CheckConstraint("start_time < end_time", name="start_before_end"),
        CheckConstraint(
            "(break_start IS NULL AND break_end IS NULL) OR "
            "(break_start IS NOT NULL AND break_end IS NOT NULL AND break_start < break_end)",
            name="break_consistent",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id", ondelete="CASCADE"))
    weekday: Mapped[int] = mapped_column(Integer)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    break_start: Mapped[time | None] = mapped_column(Time)
    break_end: Mapped[time | None] = mapped_column(Time)

    master: Mapped[Master] = relationship(back_populates="working_hours")


class TimeOff(Base):
    """Відпустка, лікарняний або ручне блокування часу майстра."""

    __tablename__ = "time_off"
    __table_args__ = (CheckConstraint("start_dt < end_dt", name="start_before_end"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    master_id: Mapped[int] = mapped_column(
        ForeignKey("masters.id", ondelete="CASCADE"), index=True
    )
    start_dt: Mapped[datetime] = mapped_column(UTCDateTime)
    end_dt: Mapped[datetime] = mapped_column(UTCDateTime)
    reason: Mapped[str | None] = mapped_column(String(200))

    master: Mapped[Master] = relationship()


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Pet(Base):
    """Тварина клієнта (для профілів із розділом pets, наприклад грумінг)."""

    __tablename__ = "pets"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(50))
    species: Mapped[str] = mapped_column(String(30))  # ключ виду з профілю
    breed: Mapped[str] = mapped_column(String(50))
    size: Mapped[str] = mapped_column(String(30))  # ключ розміру з профілю
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    client: Mapped[Client] = relationship()


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("start_dt < end_dt", name="start_before_end"),
        Index("ix_bookings_master_start", "master_id", "start_dt"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # RESTRICT: історія записів не повинна зникати разом із майстром, послугою
    # чи клієнтом. Майстрів і послуги приховуємо через is_active, а не видаляємо.
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT"), index=True
    )
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id", ondelete="RESTRICT"))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="RESTRICT"))
    start_dt: Mapped[datetime] = mapped_column(UTCDateTime)
    end_dt: Mapped[datetime] = mapped_column(UTCDateTime)
    status: Mapped[BookingStatus] = mapped_column(
        Enum(
            BookingStatus,
            native_enum=False,
            length=32,
            values_callable=lambda e: [m.value for m in e],
            validate_strings=True,
        ),
        default=BookingStatus.CONFIRMED,
        server_default=BookingStatus.CONFIRMED.value,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    reminded_24h: Mapped[bool] = mapped_column(default=False, server_default="0")
    reminded_2h: Mapped[bool] = mapped_column(default=False, server_default="0")
    # Коли клієнт натиснув «Буду» в нагадуванні (None — не підтверджував)
    client_confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Тварина (лише для профілів із тваринами)
    pet_id: Mapped[int | None] = mapped_column(ForeignKey("pets.id", ondelete="RESTRICT"))
    # Ціна на момент запису: залежить від розміру тварини й не змінюється разом із прайсом.
    # None — старі записи, ціна береться з послуги
    price: Mapped[int | None] = mapped_column(Integer)

    client: Mapped[Client] = relationship()
    master: Mapped[Master] = relationship()
    service: Mapped[Service] = relationship()
    pet: Mapped[Pet | None] = relationship()

    @property
    def duration_min(self) -> int:
        return int((self.end_dt - self.start_dt).total_seconds() // 60)

    @property
    def display_price(self) -> int:
        return self.price if self.price is not None else self.service.price


class FsmState(Base):
    """Стан діалогу aiogram (FSM): незавершений запис не губиться після перезапуску бота."""

    __tablename__ = "fsm_states"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    state: Mapped[str | None] = mapped_column(String(200))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
