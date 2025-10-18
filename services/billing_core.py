# services/billing_core.py
from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple
from services import gsheets
import asyncio
from config import settings
PACKS = settings.packs

# === Константы ===
DB_PATH = "db/generations.db"   # база для генераций
BONUS_PER_10 = 2                # бонус за каждые 10
FREE_TRIAL_GENS = 1             # бесплатная проба


# === Вспомогалки ===
def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")

def _conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

# === Инициализация базы ===
def init_db():
    con = _conn()
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        generations_balance INTEGER DEFAULT 0,
        total_spent INTEGER DEFAULT 0,
        total_generations INTEGER DEFAULT 0,
        free_trial_used INTEGER DEFAULT 0,
        last_payment_at TEXT,
        last_active_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        gens_base INTEGER NOT NULL,
        gens_bonus INTEGER NOT NULL,
        gens_total INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )
    """)
    con.commit()
    con.close()

# === Подсчёт генераций ===
def calc_generations(base: int) -> int:
    bonus = (base // 10) * BONUS_PER_10
    return base + bonus


# === Модель User ===
@dataclass
class User:
    user_id: int
    username: Optional[str]
    generations_balance: int
    total_spent: int
    total_generations: int
    free_trial_used: bool
    last_payment_at: Optional[str]
    last_active_at: Optional[str]

# === Работа с юзером ===
# === Универсальный upsert_user: работает и с SQLite, и с PostgreSQL ===
# === Универсальный upsert_user: работает и с SQLite, и с PostgreSQL ===
async def upsert_user(user_id: int, username: Optional[str]):
    """
    Универсальный upsert: один и тот же код работает
    и локально (SQLite), и на Railway (Postgres).
    """
    # --- Если локально: SQLite (sync код в отдельном потоке) ---
    if not settings.use_postgres:
        import sqlite3

        def sync_upsert():
            con = sqlite3.connect(DB_PATH, check_same_thread=False)
            cur = con.cursor()
            cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
            row = cur.fetchone()

            if row is None:
                print(f"🆕 [SQLite] Создаю пользователя {user_id} ({username})")
                cur.execute("""
                    INSERT INTO users(user_id, username, generations_balance, total_spent, total_generations, free_trial_used, last_payment_at, last_active_at)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (user_id, username, 0, 0, 0, 0, None, now_iso()))
                con.commit()
                user = User(
                    user_id=user_id, username=username,
                    generations_balance=0, total_spent=0, total_generations=0,
                    free_trial_used=False, last_payment_at=None, last_active_at=now_iso()
                )
            else:
                print(f"♻️ [SQLite] Обновляю пользователя {user_id} ({username})")
                cur.execute("UPDATE users SET username=?, last_active_at=? WHERE user_id=?", (username, now_iso(), user_id))
                con.commit()
                user = User(
                    user_id=row[0], username=row[1],
                    generations_balance=row[2], total_spent=row[3],
                    total_generations=row[4], free_trial_used=bool(row[5]),
                    last_payment_at=row[6], last_active_at=row[7]
                )
            con.close()
            return user

        return await asyncio.to_thread(sync_upsert)

    # --- Если Railway (PostgreSQL): чистый async SQLAlchemy ---
    else:
        from db.database import get_session
        from db.models import User as DBUser

        async with get_session() as session:
            db_user = await session.get(DBUser, user_id)
            if not db_user:
                print(f"🆕 [Postgres] Создаю нового пользователя {user_id} ({username})")
                db_user = DBUser(user_id=user_id, username=username, balance=0)
                session.add(db_user)
            else:
                print(f"♻️ [Postgres] Обновляю пользователя {user_id} ({username})")
                db_user.username = username
                db_user.last_active_at = now_iso()

            await session.commit()
            return db_user




def get_user(user_id: int) -> Optional[User]:
    con = _conn()
    cur = con.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    con.close()
    if not row:
        return None
    return User(
        user_id=row[0], username=row[1],
        generations_balance=row[2], total_spent=row[3],
        total_generations=row[4], free_trial_used=bool(row[5]),
        last_payment_at=row[6], last_active_at=row[7]
    )

# === Бесплатная проба ===
def grant_free_trial(user_id: int) -> bool:
    con = _conn()
    cur = con.cursor()
    cur.execute("SELECT free_trial_used FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    # если пробник уже был использован — не выдаём
    if row and row[0] == 1:
        con.close()
        return False

    # начисляем пробную генерацию (но НЕ помечаем как использованную!)
    cur.execute("""
        UPDATE users
        SET generations_balance = generations_balance + ?,
            total_generations = total_generations + ?,
            last_active_at = ?
        WHERE user_id = ?
    """, (FREE_TRIAL_GENS, FREE_TRIAL_GENS, now_iso(), user_id))
    con.commit()

    # лог в Google Sheets
    awaitable = gsheets.log_user_event(
        user_id=user_id,
        username="",
        event="free_trial_granted",
        meta={"gens": FREE_TRIAL_GENS}
    )
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(awaitable)
    except Exception as e:
        print("GSHEETS log_user_event error:", e)

    con.close()
    return True

def add_package(user_id: int, amount_rub: int) -> Tuple[int, int, int]:
    base = amount_rub // settings.price_rub       # сколько генераций купил за деньги
    total = calc_generations(base)                # добавляем бонус
    bonus = total - base


    con = _conn()
    cur = con.cursor()
    # старый баланс
    cur.execute("SELECT generations_balance FROM users WHERE user_id=?", (user_id,))
    old_bal = cur.fetchone()[0]

    # апдейт
    cur.execute("""
        UPDATE users SET
            generations_balance = generations_balance + ?,
            total_spent = total_spent + ?,
            total_generations = total_generations + ?,
            last_payment_at = ?,
            last_active_at = ?
        WHERE user_id = ?
    """, (total, amount_rub, total, now_iso(), now_iso(), user_id))

    cur.execute("""
        INSERT INTO payments(user_id, amount, gens_base, gens_bonus, gens_total, created_at)
        VALUES (?,?,?,?,?,?)
    """, (user_id, amount_rub, base, bonus, total, now_iso()))
    con.commit()

    # новый баланс
    cur.execute("SELECT generations_balance FROM users WHERE user_id=?", (user_id,))
    new_bal = cur.fetchone()[0]

    # логи
    asyncio.create_task(gsheets.log_user_event(
        user_id=user_id,
        username="",
        event="package_purchase",
        meta={"amount": amount_rub, "gens_total": total, "bonus": bonus}
    ))
    asyncio.create_task(gsheets.log_balance_change(
        user_id=user_id,
        old_balance=old_bal,
        delta=total,
        new_balance=new_bal,
        reason="package_purchase"
    ))
    

    con.close()
    return base, bonus, total

# === Списание генерации ===
def consume_generation(user_id: int) -> Tuple[bool, int]:
    con = _conn()
    cur = con.cursor()
    # достаём баланс и статус пробника
    cur.execute("SELECT generations_balance, free_trial_used FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if not row:
        con.close()
        return False, 0

    bal, trial_used = row
    if bal <= 0:
        con.close()
        return False, bal

    # списание
    cur.execute("""
        UPDATE users
        SET generations_balance = generations_balance - 1,
            last_active_at = ?
        WHERE user_id = ?
    """, (now_iso(), user_id))
    con.commit()

    # новый баланс
    cur.execute("SELECT generations_balance FROM users WHERE user_id=?", (user_id,))
    new_bal = cur.fetchone()[0]

    # если списали пробную и она ещё не была отмечена → ставим флаг
    trial_consumed_now = False
    if not trial_used and bal == 1:
        cur.execute("""
            UPDATE users
            SET free_trial_used = 1
            WHERE user_id = ?
        """, (user_id,))
        con.commit()
        trial_consumed_now = True

    # лог
    awaitable = gsheets.log_user_event(
        user_id=user_id,
        username="",
        event="consume_generation",
        meta={
            "old_balance": bal,
            "left_balance": new_bal,
            "trial_consumed": trial_consumed_now
        }
    )
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(awaitable)
    except Exception as e:
        print("GSHEETS log_user_event error:", e)

    con.close()
    return True, new_bal


# === Красивый текст баланса ===
def balance_text(user: User) -> str:
    if not user.free_trial_used and user.generations_balance > 0:
        trial_info = "🎁 Доступна 1 бесплатная генерация!"
    else:
        trial_info = f"Бесплатная проба: {'✅ использована' if user.free_trial_used else '❌ ещё не активирована'}"

    return (
        f"🎉 Баланс: {int(user.generations_balance)} генераций\n\n"
        f"{trial_info}"
    )


# === Отметка, что пробник использован ===
def mark_trial_used(user_id: int):
    con = _conn()
    cur = con.cursor()
    cur.execute("""
        UPDATE users
        SET free_trial_used = 1,
            last_active_at = ?
        WHERE user_id = ?
    """, (now_iso(), user_id))
    con.commit()
    con.close()

def use_free_trial(user_id: int) -> bool:
    con = _conn()
    cur = con.cursor()
    cur.execute("SELECT free_trial_used FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if not row or row[0] == 1:
        con.close()
        return False
    cur.execute("""
        UPDATE users
        SET free_trial_used = 1,
            generations_balance = generations_balance - 1,
            last_active_at = ?
        WHERE user_id = ?
    """, (now_iso(), user_id))
    con.commit()
    con.close()
    return True
