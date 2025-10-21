# services/yookassa.py
from datetime import datetime, timezone
from yookassa import Configuration, Payment
from config import settings
from db.database import get_session
from db.models import Payment as PaymentModel  # SQLAlchemy модель
import asyncio

# --- Инициализация SDK ---
Configuration.account_id = settings.yookassa_shop_id
Configuration.secret_key = settings.yookassa_secret_key


def _rub(val: int) -> str:
    """Приводим к строке с двумя знаками, как требует YooKassa"""
    return f"{val:.2f}"


# services/yookassa.py

async def create_payment(
    amount_rub: int,
    description: str,
    user_id: int,
    order_id: str,
    customer_email: str = "test@example.com"
):
    from sqlalchemy import select
    from db.models import User
    from services.billing_core import upsert_user

    async with get_session() as session:
        user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()

        if not user:
            print(f"⚙️ Пользователь {user_id} не найден — создаю...")
            await upsert_user(user_id, "")
            await asyncio.sleep(0.3)
            # 🧩 теперь проверяем повторно уже с user_id
            user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()

            if user:
                print(f"✅ Пользователь {user_id} успешно добавлен в users.")
            else:
                print(f"❌ Ошибка: пользователь {user_id} так и не появился в таблице users.")


    body = {
        "amount": {
            "value": _rub(amount_rub),
            "currency": "RUB"
        },
        "capture": True,
        "description": description[:120],
        "confirmation": {
            "type": "redirect",
            "return_url": settings.yookassa_return_url,
        },
        "receipt": {
            "customer": {"email": customer_email},
            "items": [
                {
                    "description": "Пополнение баланса",
                    "quantity": "1.00",
                    "amount": {
                        "value": _rub(amount_rub),
                        "currency": "RUB"
                    },
                    "vat_code": 1,
                    "payment_mode": "full_prepayment",  # ✅ фикс
                    "payment_subject": "service"        # ✅ фикс
                }
            ]
        },
        "metadata": {
            "user_id": str(user_id),
            "order_id": str(order_id),
        },
    }

    import json
    print("➡️ YOOKASSA BODY:", json.dumps(body, ensure_ascii=False))
    print("🧾 YOOKASSA CONFIG:")
    print("SHOP_ID:", settings.yookassa_shop_id)
    print("SECRET_KEY:", settings.yookassa_secret_key[:6] + "..." if settings.yookassa_secret_key else "MISSING")
    print("MODE:", settings.payment_mode)


    payment = Payment.create(body)
    payment_id = payment.id
    confirmation_url = payment.confirmation.confirmation_url

    # 🧩 Перед записью в payments — убедимся, что user реально есть
    from sqlalchemy import select
    from db.models import User

    async with get_session() as session:
        for _ in range(10):
            user_exists = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
            if user_exists:
                print(f"✅ Пользователь {user_id} подтверждён в базе, создаём платёж.")
                break
            await asyncio.sleep(0.3)
        else:
            print(f"❌ Пользователь {user_id} так и не появился в users — платеж не создаётся.")
            return None, None, None

        # ✅ Теперь точно создаём платеж
        p = PaymentModel(
            user_id=user_id,
            amount=amount_rub,
            provider_payment_id=payment_id,
            status="PENDING",
            provider="YOOKASSA",
            created_at=datetime.now(timezone.utc),
            order_id=order_id,
        )
        session.add(p)
        await session.commit()

    # 🟢 Возвращаем данные, чтобы бот открыл оплату
    return payment_id, confirmation_url, order_id



def get_payment_status(payment_id: str) -> str:
    """
    Маппинг статусов YooKassa -> наши:
      pending / waiting_for_capture → IN_PROGRESS
      succeeded → CONFIRMED
      canceled → REJECTED
    """
    p = Payment.find_one(payment_id)

    if p.status in ("pending", "waiting_for_capture"):
        return "IN_PROGRESS"
    if p.status == "succeeded":
        return "CONFIRMED"
    if p.status == "canceled":
        return "REJECTED"
    return "IN_PROGRESS"
