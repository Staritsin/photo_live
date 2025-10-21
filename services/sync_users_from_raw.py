# синхронизирует пользователей из гугл таблиц в базу - основную таблицу users

import os
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

async def sync_users():
    print("\n🚀 Синхронизация пользователей с raw-таблиц...\n")
    engine = create_async_engine(DATABASE_URL, echo=False)

    async with engine.begin() as conn:
        # 1️⃣ Собираем всех уникальных user_id
        result = await conn.execute(text("""
            SELECT DISTINCT user_id FROM generations_raw
            UNION
            SELECT DISTINCT user_id FROM payments_raw
            UNION
            SELECT DISTINCT referrer_id FROM referrals_raw
            UNION
            SELECT DISTINCT new_user_id FROM referrals_raw
        """))
        user_ids = [r[0] for r in result if r[0]]
        print(f"Найдено уникальных user_id: {len(user_ids)}")

        for uid in user_ids:
            # Пропускаем некорректные ID
            try:
                uid = int(uid)
            except Exception:
                continue

            # 2️⃣ Проверяем, есть ли уже такой пользователь
            check = await conn.execute(
                text("SELECT id FROM users WHERE id = CAST(:uid AS BIGINT)"),
                {"uid": uid}
            )
            exists = check.first()

            # 3️⃣ Считаем статистику
            pay_sum = await conn.scalar(
                text("SELECT COALESCE(SUM(amount_rub), 0) FROM payments_raw WHERE user_id = CAST(:uid AS BIGINT)"),
                {"uid": uid}
            )
            gens = await conn.scalar(
                text("SELECT COUNT(*) FROM generations_raw WHERE user_id = CAST(:uid AS BIGINT)"),
                {"uid": uid}
            )
            ref_count = await conn.scalar(
                text("SELECT COUNT(*) FROM referrals_raw WHERE referrer_id = CAST(:uid AS BIGINT)"),
                {"uid": uid}
            )
            bonus = await conn.scalar(
                text("SELECT COALESCE(SUM(bonus_total), 0) FROM referrals_summary WHERE user_id = CAST(:uid AS BIGINT)"),
                {"uid": uid}
            )
            balance = max(0, (bonus or 0))  # можно потом дополнить логикой

            # 4️⃣ Последняя оплата и активность
            last_pay = await conn.scalar(
                text("SELECT MAX(ts) FROM payments_raw WHERE user_id = CAST(:uid AS BIGINT)"),
                {"uid": uid}
            )
            last_gen = await conn.scalar(
                text("SELECT MAX(ts) FROM generations_raw WHERE user_id = CAST(:uid AS BIGINT)"),
                {"uid": uid}
            )
            last_active = last_gen or last_pay

            # 🧩 Преобразуем даты в строки
            def to_str(v):
                if v is None:
                    return None
                if isinstance(v, str):
                    return v
                return v.isoformat()

            last_pay = to_str(last_pay)
            last_active = to_str(last_active)

            if exists:
                # === Обновляем ===
                await conn.execute(text("""
                    UPDATE users
                    SET total_spent = :spent,
                        total_generations = :gens,
                        balance = :balance,
                        referrals_count = :refs,
                        generations_balance = :bonus,
                        last_payment_at = COALESCE(:last_pay, last_payment_at),
                        last_active_at = COALESCE(:last_active, last_active_at)
                    WHERE id = CAST(:uid AS BIGINT)
                """), {
                    "spent": int(pay_sum or 0),
                    "gens": int(gens or 0),
                    "balance": int(balance),
                    "refs": int(ref_count or 0),
                    "bonus": int(bonus or 0),
                    "last_pay": last_pay,
                    "last_active": last_active,
                    "uid": uid
                })
            else:
                # === Добавляем нового ===
                await conn.execute(text("""
                    INSERT INTO users (id, balance, generations_balance, total_spent, total_generations,
                        last_payment_at, last_active_at, free_trial_used, referrals_count, consent_accepted, created_at)
                    VALUES (CAST(:uid AS BIGINT), :balance, :bonus, :spent, :gens,
                        :last_pay, :last_active, FALSE, :refs, TRUE, NOW())
                """), {
                    "uid": uid,
                    "balance": int(balance),
                    "bonus": int(bonus or 0),
                    "spent": int(pay_sum or 0),
                    "gens": int(gens or 0),
                    "last_pay": last_pay,
                    "last_active": last_active,
                    "refs": int(ref_count or 0)
                })

        print("\n✅ Синхронизация завершена успешно!")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(sync_users())
