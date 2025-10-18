from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from config import settings
from .utils import send_or_replace_text


async def open_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Открывает поддержку из кнопки"""
    q = update.callback_query
    await q.answer()
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("💬 Чат с поддержкой", url=settings.support_chat_url)]]
    )
    await send_or_replace_text(
        update,
        context,
        "Если что-то не получается — напиши сюда @staritsin_a 👩‍💻\nМы быстро поможем",
        reply_markup=kb
    )


async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /support открывает то же самое"""
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("💬 Чат с поддержкой", url=settings.support_chat_url)]]
    )
    await update.message.reply_text(
        "Если что-то не получается — напиши сюда @staritsin_a 👩‍💻\nМы быстро поможем",
        reply_markup=kb
    )
