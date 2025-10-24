# db/database.py
from typing import AsyncGenerator
import ssl, asyncio, logging, os
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from contextlib import asynccontextmanager
from sqlalchemy import inspect
from config import settings


# === 1. Определяем окружение ===
db_url = os.getenv("DATABASE_URL", "") or settings.async_database_url
is_sqlite = "sqlite" in db_url.lower()
is_render = "onrender.com" in db_url or os.getenv("RENDER")
is_railway = "railway" in db_url

# === 2. Настраиваем URL и SSL ===
if is_render:
    # Render: убираем sslmode, asyncpg не понимает его
    if "sslmode" in db_url:
        db_url = db_url.split("?")[0]
        print("⚙️ Render detected — удаляем sslmode из DATABASE_URL")

elif is_railway:
    # Railway: добавляем sslmode=require если его нет
    if "sslmode" not in db_url:
        db_url += "?sslmode=require"
        print("⚙️ Railway detected — добавляем sslmode=require")

os.environ["DATABASE_URL"] = db_url


# === 3. SSL настройки ===
connect_args = {}
if not is_sqlite:
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    connect_args = {"ssl": ssl_context}


# === 4. Создаём движок ===
Base = declarative_base()

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

SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


# === 5. Логирование старта ===
env = (
    "Render 🌐" if is_render else
    "Railway 🚄" if is_railway else
    "Local 💻"
)
db_type = "SQLite" if is_sqlite else "Postgres"
print(f"✅ Using {db_type} ({env})")
print(f"🔗 DATABASE_URL: {db_url}")


# === 6. Контекст сессий ===
@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


# === 7. Отладка пула ===
async def debug_pool_status():
    try:
        pool = inspect(engine).get_pool()
        print(f"📊 Pool: size={pool.size()}, checked_out={pool.checkedout()}, overflow={pool.overflow()}")
    except Exception:
        pass


# === 8. Инициализация базы ===
async def init_db(retries: int = 5, delay: int = 3):
    """Создаёт таблицы при старте, с повторами при ошибках"""
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
