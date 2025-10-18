from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.orm import declarative_base
from config import settings

# === Базовый класс моделей ===
Base = declarative_base()

# === Определяем тип базы ===
db_url = settings.async_database_url
is_sqlite = "sqlite" in db_url.lower()

# === SSL и параметры движка ===
connect_args = {"ssl": True} if "postgresql" in db_url else {}

engine_kwargs = dict(
    echo=False,
    pool_pre_ping=True,
    connect_args=connect_args,
)

# ⚙️ SQLite не поддерживает pool_size / max_overflow
if not is_sqlite:
    engine_kwargs.update(pool_size=5, max_overflow=10)

# === Создаём движок ===
engine = create_async_engine(db_url, **engine_kwargs)

# === Логирование типа базы ===
if is_sqlite:
    print("✅ Using SQLite (Local)")
else:
    print("✅ Using Postgres (Production)")

# === Сессии ===
SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# === Генератор сессий ===
from contextlib import asynccontextmanager   # 👈 добавь вверху файла, если нет

@asynccontextmanager
async def get_session():
    async with SessionLocal() as session:
        yield session


# === Инициализация БД ===
async def init_db() -> None:
    from . import models  # чтобы зарегистрировать модели
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
