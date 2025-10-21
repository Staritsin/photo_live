# db/database.py 
from typing import AsyncGenerator

import ssl
import logging
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.orm import declarative_base
from sqlalchemy import event
from contextlib import asynccontextmanager
from config import settings

# === Базовый класс моделей ===
Base = declarative_base()

# === Определяем тип базы ===
db_url = settings.async_database_url
is_sqlite = "sqlite" in db_url.lower()

# === SSL и параметры движка ===
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

connect_args = {"ssl": ssl_context}  # 👈 передаём свой ssl-контекст вручную

engine_kwargs = dict(
    echo=False,
    pool_pre_ping=True,
    connect_args=connect_args,
)


# ⚙️ SQLite не поддерживает pool_size / max_overflow
if not is_sqlite:
    engine_kwargs.update(
        pool_size=50,         # 🔹 держим 20 постоянных соединений
        max_overflow=25,      # 🔹 до 10 временных при пиках
        pool_timeout=20,      # 🔹 10 сек ожидания вместо вечного зависания
        pool_recycle=300,     # 🔹 обновление каждые 10 мин
        pool_pre_ping=True    # 🔹 проверка «живых» коннектов
    )

# === Создаём движок ===
engine = create_async_engine(db_url, **engine_kwargs)

from sqlalchemy import inspect

async def debug_pool_status():
    pool = inspect(engine).get_pool()
    print(f"📊 Pool status: size={pool.size()}, checked_out={pool.checkedout()}, overflow={pool.overflow()}")


# === Логирование событий соединения ===
@event.listens_for(engine.sync_engine, "connect")
def connect_log(dbapi_connection, connection_record):
    print("🔌 Connection opened")

@event.listens_for(engine.sync_engine, "close")
def close_log(dbapi_connection, connection_record):
    print("❌ Connection closed")

@event.listens_for(engine.sync_engine, "checkout")
def checkout_log(dbapi_connection, connection_record, connection_proxy):
    print("📤 Connection checked out")

@event.listens_for(engine.sync_engine, "checkin")
def checkin_log(dbapi_connection, connection_record):
    print("📥 Connection returned")

# === Логирование типа базы ===
if is_sqlite:
    print("✅ Using SQLite (Local)")
else:
    print("✅ Using Postgres (Production)")

# === Создание фабрики сессий ===
SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# === Генератор сессий ===
@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session

# === Инициализация БД ===
async def init_db() -> None:
    """Создаёт все таблицы при старте"""
    from . import models  # обязательно регистрируем модели
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logging.info("✅ Database initialized successfully")

