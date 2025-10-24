# db/database.py — финальная версия 🔥

from typing import AsyncGenerator
import ssl, asyncio, logging, os
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from contextlib import asynccontextmanager
from sqlalchemy import event, inspect
from config import settings


# === 1. Универсальный FIX для Render / Railway ===
db_url = os.getenv("DATABASE_URL", "") or settings.async_database_url
is_render = os.getenv("RENDER") == "true" or "onrender.com" in db_url
is_railway = "railway" in db_url
is_sqlite = "sqlite" in db_url.lower()

if is_render:
    # Render: asyncpg не понимает sslmode
    if "sslmode" in db_url:
        db_url = db_url.split("?")[0]
        print("⚙️ Render detected — убираем sslmode из DATABASE_URL")
elif is_railway and "sslmode" not in db_url:
    # Railway: требуется sslmode=require
    db_url += "?sslmode=require"
    print("⚙️ Railway detected — добавляем sslmode=require")

os.environ["DATABASE_URL"] = db_url


# === 2. Базовый класс моделей ===
Base = declarative_base()


# === 3. SSL / connect_args ===
connect_args = {}
if not is_sqlite:
    ssl_context = ssl.create_default_context()
    connect_args = {"ssl": ssl_context}


# === 4. Настройки пула соединений ===
engine = create_async_engine(
    db_url,
    echo=False,
    pool_pre_ping=True,
    connect_args=connect_args,
    pool_size=20 if not is_sqlite else None,
    max_overflow=10 if not is_sqlite else None,
    pool_timeout=15 if not is_sqlite else None,
    pool_recycle=300 if not is_sqlite else None,
)

# === 5. Красивая информация при старте ===
env_label = (
    "Render 🌐" if is_render else
    "Railway 🚄" if is_railway else
    "Local 💻"
)
db_label = "SQLite" if is_sqlite else "Postgres"
print(f"✅ Using {db_label} ({env_label})")
print(f"🔗 DATABASE_URL: {db_url}")


# === 6. Сессия ===
SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


# === 7. Контекстный менеджер сессий ===
@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


# === 8. Отладка пула (по запросу) ===
async def debug_pool_status():
    try:
        pool = inspect(engine).get_pool()
        print(f"📊 Pool status: size={pool.size()}, checked_out={pool.checkedout()}, overflow={pool.overflow()}")
    except Exception:
        pass


# === 9. Логирование событий (только при debug) ===
if os.getenv("DEBUG", "0") == "1":
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


# === 10. Инициализация базы ===
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
            logging.error(f"❌ DB init failed ({attempt}/{retries}): {e}")
            if attempt < retries:
                await asyncio.sleep(delay)
            else:
                raise
