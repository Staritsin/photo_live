# db/models.py
import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base

MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def ts_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def format_moscow(ts: dt.datetime | None) -> str:
    if ts is None:
        return ""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    local = ts.astimezone(MOSCOW_TZ)
    return (
        f"{local.day:02d}.{local.month:02d}.{local.year} "
        f"{local.hour}:{local.minute:02d}:{local.second:02d} ( московское время )"
    )


# === USERS ===
from sqlalchemy.orm import relationship

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    full_name: Mapped[str | None] = mapped_column(String, nullable=True)
    balance: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    generations_balance: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_spent: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_generations: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_payment_at: Mapped[str | None] = mapped_column(String, nullable=True)
    last_active_at: Mapped[str | None] = mapped_column(String, nullable=True)
    free_trial_used: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    referrals_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    consent_accepted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    referred_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=ts_now,
        server_default=func.now(),
    )

    # 🧩 ДОБАВЬ ЭТИ СВЯЗИ ВНУТРЬ КЛАССА
    payments = relationship(
        "Payment",
        backref="user",
        cascade="all, delete-orphan",
        lazy="selectin"
    )

    referrals_as_inviter = relationship(
        "Referral",
        foreign_keys="[Referral.inviter_id]",
        backref="inviter",
        cascade="all, delete-orphan",
        lazy="selectin"
    )

    referrals_as_invited = relationship(
        "Referral",
        foreign_keys="[Referral.invited_id]",
        backref="invited",
        cascade="all, delete-orphan",
        lazy="selectin"
    )

    @property
    def created_at_moscow(self) -> str:
        return format_moscow(self.created_at)




# === REFERRALS (core) ===
class Referral(Base):
    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inviter_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    invited_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), unique=True, nullable=False)
    bonus_awarded: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=ts_now,
        server_default=func.now(),
    )

    @property
    def created_at_moscow(self) -> str:
        return format_moscow(self.created_at)





# === PAYMENTS (core) ===
class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    provider_payment_id: Mapped[str] = mapped_column(String(255), unique=True)
    status: Mapped[str] = mapped_column(String(64), default="PENDING")
    provider: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payment_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    order_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=ts_now,
        server_default=func.now(),
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=ts_now,
        onupdate=ts_now,
    )

    @property
    def created_at_moscow(self) -> str:
        return format_moscow(self.created_at)

    @property
    def updated_at_moscow(self) -> str:
        return format_moscow(self.updated_at)



# === PAYMENTS_RAW ===
class PaymentRaw(Base):
    __tablename__ = "payments_raw"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=ts_now, server_default=func.now())
    user_id: Mapped[int] = mapped_column(BigInteger)
    amount_rub: Mapped[float] = mapped_column(Float)
    order_id: Mapped[str | None] = mapped_column(String(255))
    mode: Mapped[str | None] = mapped_column(String(255))
    payment_url: Mapped[str | None] = mapped_column(String(1024))

    @property
    def ts_moscow(self) -> str:
        return format_moscow(self.ts)


# === RESULTS_RAW ===
class ResultRaw(Base):
    __tablename__ = "results_raw"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=ts_now, server_default=func.now())
    user_id: Mapped[int] = mapped_column(BigInteger)
    payment_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str | None] = mapped_column(String(255))
    amount_rub: Mapped[float | None] = mapped_column(Float, nullable=True)

    @property
    def ts_moscow(self) -> str:
        return format_moscow(self.ts)


# === GENERATIONS_RAW ===
class GenerationRaw(Base):
    __tablename__ = "generations_raw"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=ts_now, server_default=func.now())
    user_id: Mapped[int] = mapped_column(BigInteger)
    price_rub: Mapped[float] = mapped_column(Float)
    input_type: Mapped[str | None] = mapped_column(String(64))
    prompt: Mapped[str | None] = mapped_column(String(1024))
    file_id: Mapped[str | None] = mapped_column(String(255))

    @property
    def ts_moscow(self) -> str:
        return format_moscow(self.ts)


# === BALANCES_RAW ===
class BalanceRaw(Base):
    __tablename__ = "balances_raw"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=ts_now, server_default=func.now())
    user_id: Mapped[int] = mapped_column(BigInteger)
    old_balance: Mapped[int] = mapped_column(Integer)
    delta: Mapped[int] = mapped_column(Integer)
    new_balance: Mapped[int] = mapped_column(Integer)
    total_generations: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255))

    @property
    def ts_moscow(self) -> str:
        return format_moscow(self.ts)


# === REFERRALS_RAW ===
class ReferralRaw(Base):
    __tablename__ = "referrals_raw"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=ts_now, server_default=func.now())
    referrer_id: Mapped[int] = mapped_column(BigInteger)
    new_user_id: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str | None] = mapped_column(String(255))

    @property
    def ts_moscow(self) -> str:
        return format_moscow(self.ts)


# === REFERRALS_SUMMARY ===
class ReferralSummary(Base):
    __tablename__ = "referrals_summary"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=ts_now, server_default=func.now())
    user_id: Mapped[int] = mapped_column(BigInteger)
    invited_total: Mapped[int] = mapped_column(Integer)
    invited_paid: Mapped[int] = mapped_column(Integer)
    bonus_total: Mapped[int] = mapped_column(Integer)

    @property
    def ts_moscow(self) -> str:
        return format_moscow(self.ts)

# === DASHBOARD CACHE ===
class DashboardCache(Base):
    __tablename__ = "dashboard_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    total_users: Mapped[int] = mapped_column(Integer, default=0)
    total_payments: Mapped[float] = mapped_column(Float, default=0.0)
    total_generations: Mapped[int] = mapped_column(Integer, default=0)
    total_orders: Mapped[int] = mapped_column(Integer, default=0)
    total_referrals: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=ts_now,
        onupdate=ts_now,
        server_default=func.now(),
    )

    @property
    def updated_at_moscow(self) -> str:
        return format_moscow(self.updated_at)

