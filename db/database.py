# db/database.py
from typing import AsyncGenerator
import ssl
import asyncio
import logging
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.orm import declarative_base
from sqlalchemy import event, inspect
from contextlib import asynccontextmanager
from config import settings
import os

# === Универсальный FIX для Render / Railway ===
db_url = os.getenv("DATABASE_URL", "") or settings.async_database_url

if "render.com" in os.getenv("RENDER", "") or "onrender.com" in db_url:
    # Render: asyncpg не понимает sslmode — убираем
    if "sslmode" in db_url:
        db_url = db_url.split("?")[0]
        print("⚙️ Render detected — убираем sslmode из DATABASE_URL")
elif "railway" in db_url:
    # Railway: добавляем sslmode=require если его нет
    if "sslmode" not in db_url:
        db_url += "?sslmode=require"
        print("⚙️ Railway detected — добавляем sslmode=require")

os.environ["DATABASE_URL"] = db_url

# === Базовый класс моделей ===
Base = declarative_base()

# === Определяем тип базы ===
is_sqlite = "sqlite" in db_url.lower()

# === SSL и параметры движка ===
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

connect_args = {"ssl": ssl_context}

# === Основные параметры подключения ===
engine_kwargs = dict(
    echo=False,
    pool_pre_ping=True,
    connect_args=connect_args,
)

# ⚙️ Для PostgreSQL включаем пул соединений
if not is_sqlite:
    engine_kwargs.update(
        pool_size=20,
        max_overflow=10,
        pool_timeout=15,
        pool_recycle=300,
        pool_pre_ping=True,
    )

# === Создаём движок ===
engine = create_async_engine(db_url, **engine_kwargs)

# === Отладка пула соединений ===
async def debug_pool_status():
    pool = inspect(engine).get_pool()
    print(f"📊 Pool status: size={pool.size()}, checked_out={pool.checkedout()}, overflow={pool.overflow()}")

# === Логирование событий ===
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

# === Инициализация БД с автоповтором ===
async def init_db(retries: int = 5, delay: int = 3) -> None:
    """Создаёт все таблицы при старте, с повторами при ошибках"""
    from . import models
    for attempt in range(1, retries + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logging.info("✅ Database initialized successfully")
            return
        except Exception as e:
            logging.error(f"❌ DB init failed (attempt {attempt}/{retries}): {e}")
            if attempt < retries:
                await asyncio.sleep(delay)
            else:
                raise
