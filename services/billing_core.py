# services/billing_core.py# от 19 окт - 13:23# - неработающий 
from __future__ import annotations
import logging

from services.performance_logger import measure_time

from datetime import datetime, timezone
import sqlite3
from dataclasses import dataclass
from datetime import datetime

@measure_time
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

from typing import Optional, Tuple
from services import gsheets
import asyncio
_upsert_semaphore = asyncio.Semaphore(10)
from config import settings
PACKS = settings.packs

# === Константы ===
DB_PATH = "db/generations.db"   # база для генераций
BONUS_PER_10 = 2                # бонус за каждые 10
FREE_TRIAL_GENS = 1             # бесплатная проба


@measure_time
def _conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

# === Инициализация базы ===
@measure_time
def init_db():
    con = _conn()
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
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
        user_id INTEGER NOT NULL REFERENCES users(id),
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
    id: int 
    username: Optional[str]
    generations_balance: int
    total_spent: int
    total_generations: int
    free_trial_used: bool
    last_payment_at: Optional[str]
    last_active_at: Optional[str]

# === Работа с юзером ===
# === Универсальный upsert_user: работает и с SQLite, и с PostgreSQL ===
@measure_time
async def upsert_user(user_id: int, username: Optional[str]):
    """
    Универсальный upsert пользователя для SQLite и PostgreSQL.
    🚀 С покадровым логом: connect / select / modify / flush / total.
    """
    import csv, os, time, logging
    from datetime import datetime, timezone

    def now_iso():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    start_ts = datetime.now()
    logging.info(f"🚀 START upsert_user | user_id={user_id} | username={username}")

    # === CSV лог ===
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    csv_path = os.path.join(log_dir, "db_timings.csv")

    async with _upsert_semaphore:
        # === SQLite ===
        if not settings.use_postgres:
            def sync_upsert():
                con = sqlite3.connect(DB_PATH, check_same_thread=False)
                cur = con.cursor()
                cur.execute("SELECT * FROM users WHERE id=?", (user_id,))
                row = cur.fetchone()

                if row is None:
                    cur.execute("""
                        INSERT INTO users(id, username, generations_balance, total_spent, total_generations, free_trial_used, last_payment_at, last_active_at)
                        VALUES (?,?,?,?,?,?,?,?)
                    """, (user_id, username, 0, 0, 0, 0, None, now_iso()))
                else:
                    cur.execute("UPDATE users SET username=?, last_active_at=? WHERE id=?", (username, now_iso(), user_id))
                con.commit()
                con.close()
            return await asyncio.to_thread(sync_upsert)

        # === PostgreSQL ===
        from db.database import get_session
        from db.models import User as DBUser
        from sqlalchemy import select

        stage_times = {}
        t0 = time.perf_counter()
        try:
            # 1️⃣ CONNECT
            t_conn = time.perf_counter()
            async with get_session() as session:
                stage_times["connect"] = round(time.perf_counter() - t_conn, 3)

                # 2️⃣ SELECT
                t1 = time.perf_counter()
                result = await session.execute(select(DBUser).where(DBUser.id == user_id))
                db_user = result.scalar_one_or_none()
                stage_times["select"] = round(time.perf_counter() - t1, 3)

                # 3️⃣ INSERT / UPDATE
                t2 = time.perf_counter()
                if db_user:
                    db_user.username = username or db_user.username
                    db_user.last_active_at = now_iso()
                else:
                    db_user = DBUser(
                        id=user_id,
                        username=username,
                        balance=0,
                        generations_balance=0,
                        total_spent=0,
                        total_generations=0,
                        free_trial_used=False,
                        consent_accepted=False,
                        last_active_at=now_iso()
                    )
                    session.add(db_user)
                stage_times["modify"] = round(time.perf_counter() - t2, 3)

                # 4️⃣ FLUSH
                t3 = time.perf_counter()
                await session.flush()
                stage_times["flush"] = round(time.perf_counter() - t3, 3)

                total_time = round(time.perf_counter() - t0, 3)

                # 🧾 CSV запись
                with open(csv_path, "a", newline="") as f:
                    writer = csv.writer(f)
                    if f.tell() == 0:
                        writer.writerow(["ts", "user_id", "connect_s", "select_s", "modify_s", "flush_s", "total_s"])
                    writer.writerow([
                        datetime.now().isoformat(timespec="seconds"),
                        user_id,
                        stage_times.get("connect", 0),
                        stage_times.get("select", 0),
                        stage_times.get("modify", 0),
                        stage_times.get("flush", 0),
                        total_time
                    ])

                logging.info(f"✅ upsert_user({user_id}) OK | total={total_time}s | {stage_times}")
                return db_user

        except Exception as e:
            logging.exception(f"❌ upsert_user ERROR | user_id={user_id} | {e}")
            raise
        finally:
            logging.info(f"📤 END upsert_user {user_id} | total={round((datetime.now()-start_ts).total_seconds(),3)}s")

@measure_time
def get_user(user_id: int) -> Optional[User]:
    # 🔧 Временно отключаем SQLite, чтобы не падал бот
    # cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    # row = cur.fetchone()
    # if not row:
    #     return None
    # return User(
    #     id=row[0], username=row[1],
    #     generations_balance=row[2], total_spent=row[3],
    #     total_generations=row[4], free_trial_used=bool(row[5]),
    #     last_payment_at=row[6], last_active_at=row[7]
    # )
    return None


# === Бесплатная проба ===
@measure_time
def grant_free_trial(user_id: int) -> bool:
    # Если бесплатные пробы выключены — просто выходим
    if not getattr(settings, "enable_free_trial", False):
        logging.warning(" Бесплатная проба отключена (ENABLE_FREE_TRIAL=0)")
        return False

    # Если количество бесплатных генераций = 0 — тоже выходим
    if getattr(settings, "free_trial_gens", 0) <= 0:
        logging.warning("FREE_TRIAL_GENS=0 — проба не начисляется")
        return False

    con = _conn()
    cur = con.cursor()
    cur.execute("SELECT free_trial_used FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()

    # если пробник уже был использован — не выдаём
    if row and row[0] == 1:
        con.close()
        return False

    # начисляем пробную генерацию
    free_gens = getattr(settings, "free_trial_gens", 1)
    cur.execute("""
        UPDATE users
        SET generations_balance = generations_balance + ?,
            total_generations = total_generations + ?,
            last_active_at = ?
        WHERE id = ?
    """, (free_gens, free_gens, now_iso(), user_id))
    con.commit()

    # лог в Google Sheets (если включено)
    if gsheets.ENABLED:
        awaitable = gsheets.log_user_event(
            user_id=user_id,
            username="",
            event="free_trial_granted",
            meta={"gens": free_gens}
        )
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(awaitable)
        except Exception as e:
            logging.info("GSHEETS log_user_event error:", e)

    con.close()
    return True

@measure_time
def add_package(user_id: int, amount_rub: int) -> Tuple[int, int, int]:
    base = amount_rub // settings.price_rub       # сколько генераций купил за деньги
    total = calc_generations(base)                # добавляем бонус
    bonus = total - base


    con = _conn()
    cur = con.cursor()
    # старый баланс
    cur.execute("SELECT generations_balance FROM users WHERE id=?", (user_id,))
    old_bal = cur.fetchone()[0]

    # апдейт
    cur.execute("""
        UPDATE users SET
            generations_balance = generations_balance + ?,
            total_spent = total_spent + ?,
            total_generations = total_generations + ?,
            last_payment_at = ?,
            last_active_at = ?
        WHERE id = ?
    """, (total, amount_rub, total, now_iso(), now_iso(), user_id))

    cur.execute("""
        INSERT INTO payments(user_id, amount, gens_base, gens_bonus, gens_total, created_at)
        VALUES (?,?,?,?,?,?)
    """, (user_id, amount_rub, base, bonus, total, now_iso()))
    con.commit()


    # 🧩 === UPDATE USER AFTER PAYMENT (PostgreSQL версия) ===
    if settings.use_postgres:
        from sqlalchemy import text
        from db.database import get_session

        async def update_pg_user():
            async with get_session() as session:
                await session.execute(text("""
                    UPDATE users
                    SET 
                        total_spent = COALESCE(total_spent, 0) + :amount,
                        generations_balance = COALESCE(generations_balance, 0) + :gens,
                        last_payment_at = NOW()
                    WHERE id = :user_id
                """), {
                    "user_id": user_id,
                    "amount": amount_rub,
                    "gens": total
                })
                await session.commit()

        # 🔄 запускаем фоново, не блокируя SQLite поток
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(update_pg_user())
        except Exception as e:
            logging.warning(f"Postgres update failed: {e}")



    # новый баланс
    cur.execute("SELECT generations_balance FROM users WHERE id=?", (user_id,))
    new_bal = cur.fetchone()[0]

    # логи
    if gsheets.ENABLED:
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

    # 🧩 === REFERRAL BONUS AFTER FRIEND PAYMENT ===
    from db.models import Referral
    from sqlalchemy import select, text
    from db.database import get_session

    async def apply_referral_bonus():
        async with get_session() as session:
            result = await session.execute(select(Referral).where(Referral.invited_id == user_id))
            ref = result.scalar_one_or_none()
            if ref and not ref.bonus_awarded:
                ref.bonus_awarded = True
                await session.execute(text("""
                    UPDATE users
                    SET 
                        generations_balance = COALESCE(generations_balance, 0) + :bonus,
                        total_generations = COALESCE(total_generations, 0) + :bonus
                    WHERE id = :referrer_id
                """), {"referrer_id": ref.inviter_id, "bonus": settings.bonus_per_friend})
                await session.commit()

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(apply_referral_bonus())
    except Exception as e:
        logging.warning(f"Referral bonus apply failed: {e}")

    return base, bonus, total


# === Списание генерации ===
@measure_time
def consume_generation(user_id: int) -> Tuple[bool, int]:
    con = _conn()
    cur = con.cursor()
    # достаём баланс и статус пробника
    cur.execute("SELECT generations_balance, free_trial_used FROM users WHERE id=?", (user_id,))
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
        WHERE id = ?
    """, (now_iso(), user_id))
    con.commit()

    # новый баланс
    cur.execute("SELECT generations_balance FROM users WHERE id=?", (user_id,))
    new_bal = cur.fetchone()[0]

    # если списали пробную и она ещё не была отмечена → ставим флаг
    trial_consumed_now = False
    if not trial_used and bal == 1:
        cur.execute("""
            UPDATE users
            SET free_trial_used = 1
            WHERE id = ?
        """, (user_id,))
        con.commit()
        trial_consumed_now = True

    # лог
    if gsheets.ENABLED:
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
            logging.info("GSHEETS log_user_event error:", e)

    con.close()
    return True, new_bal


# === Красивый текст баланса ===
@measure_time
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
@measure_time
def mark_trial_used(user_id: int):
    con = _conn()
    cur = con.cursor()
    cur.execute("""
        UPDATE users
        SET free_trial_used = 1,
            last_active_at = ?
        WHERE id = ?
    """, (now_iso(), user_id))
    con.commit()
    con.close()

@measure_time
def use_free_trial(user_id: int) -> bool:
    con = _conn()
    cur = con.cursor()
    cur.execute("SELECT free_trial_used FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    if not row or row[0] == 1:
        con.close()
        return False
    cur.execute("""
        UPDATE users
        SET free_trial_used = 1,
            generations_balance = generations_balance - 1,
            last_active_at = ?
        WHERE id = ?
    """, (now_iso(), user_id))
    con.commit()
    con.close()
    return True


