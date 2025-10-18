# services/tinkoff.py
import hashlib
import requests
import os
from typing import Dict, Any, Optional
from config import settings


def _build_token(params: Dict[str, Any], password: str) -> str:
    """Генерация токена по алгоритму Тинькофф"""
    data = {k: v for k, v in params.items() if v and k != "Token"}
    data["Password"] = password
    concat = "".join(str(v) for k, v in sorted(data.items()))
    return hashlib.sha256(concat.encode("utf-8")).hexdigest()


def create_payment(amount: float, description: str, user_id: int, order_id: Optional[str] = None):
    """Создание платежа через Тинькофф (Init). Возвращает (payment_id, payment_url, order_id)."""

    if not settings.tinkoff_terminal_key or not settings.tinkoff_secret_key:
        raise RuntimeError("❌ Нет TINKOFF_TERMINAL_KEY или TINKOFF_SECRET_KEY")

    if not order_id:
        order_id = f"{user_id}_{os.urandom(4).hex()}"

    payload = {
        "TerminalKey": settings.tinkoff_terminal_key,
        "Amount": int(round(amount * 100)),  # рубли → копейки
        "OrderId": order_id,
        "Description": description,
        "SuccessURL": settings.return_url + "?start=success",
        "FailURL": settings.return_url + "?start=fail",
    }
    payload["Token"] = _build_token(payload, settings.tinkoff_secret_key)

    base_url = settings.tinkoff_test_url if settings.payment_mode.upper() == "TEST" else settings.tinkoff_prod_url
    r = requests.post(f"{base_url}/Init", json=payload, timeout=30)

    print("➡️ TINKOFF INIT REQUEST:", payload)
    print("⬅️ TINKOFF INIT RESPONSE:", r.status_code, r.text)

    if r.status_code >= 300:
        raise RuntimeError(f"Tinkoff Init HTTP error: {r.status_code} {r.text}")

    data = r.json()
    if not data.get("Success"):
        raise RuntimeError(f"Tinkoff Init failed: {data}")

    return str(data["PaymentId"]), data["PaymentURL"], order_id


def get_payment_status(payment_id: str) -> str:
    """Проверка статуса платежа (GetState)."""

    payload = {
        "TerminalKey": settings.tinkoff_terminal_key,
        "PaymentId": payment_id,
    }
    payload["Token"] = _build_token(payload, settings.tinkoff_secret_key)

    base_url = settings.tinkoff_test_url if settings.payment_mode.upper() == "TEST" else settings.tinkoff_prod_url
    r = requests.post(f"{base_url}/GetState", json=payload, timeout=30)

    print("➡️ TINKOFF GETSTATE REQUEST:", payload)
    print("⬅️ TINKOFF GETSTATE RESPONSE:", r.status_code, r.text)

    if r.status_code >= 300:
        raise RuntimeError(f"Tinkoff GetState HTTP error: {r.status_code} {r.text}")

    data = r.json()
    if not data.get("Success"):
        raise RuntimeError(f"Tinkoff GetState failed: {data}")

    return data.get("Status", "UNKNOWN")
