# middlewares/safe_callbacks.py
from telegram import Update
from telegram.ext import CallbackContext
from utils.telegram_safe import safe_answer_callback

class SafeCallbackMiddleware:
    """Простой middleware-обёртка для безопасных callback'ов"""
    async def __call__(self, update: Update, context: CallbackContext, next_callback):
        if getattr(update, "callback_query", None):
            await safe_answer_callback(update.callback_query)
        return await next_callback(update, context)
