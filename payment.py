import uuid
from yookassa import Configuration, Payment
from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY, RETURN_URL

Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY

def create_payment(amount_rub: int, description: str) -> tuple[str, str]:
    idem = uuid.uuid4()
    payment = Payment.create({
        "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": RETURN_URL},
        "capture": True,
        "description": description,
    }, idem)
    return payment.id, payment.confirmation.confirmation_url

def get_payment_status(payment_id: str) -> str:
    payment = Payment.find_one(payment_id)
    return payment.status  # "succeeded" / "canceled" / "pending"
