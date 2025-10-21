# utils/telegram_safe.py
from telegram import CallbackQuery
import logging

async def safe_answer_callback(q: CallbackQuery, text: str | None = None):
    """Безопасно отвечает на callback_query, чтобы бот не падал при просрочке"""
    try:
        await q.answer(text=text)
    except Exception as e:
        logging.error(f"Ошибка при ответе на callback: {e}")
