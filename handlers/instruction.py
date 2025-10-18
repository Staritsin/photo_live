# handlers/instruction.py
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
import aiohttp

from config import settings
from .utils import send_or_replace_text
from services.billing_core import calc_generations
from db.database import get_session
from db.models import User


async def show_instruction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    # баланс юзера
    async with get_session() as session:

        user = await session.get(User, q.from_user.id)
        balance = user.balance if user else 0

    price = settings.price_rub
    packs = settings.packs

    text = (
        "💖 <b>Инструкция по работе с ботом PhotoAlive</b> 💖\n\n"
        "Этот бот оживляет дорогие сердцу фотографии ✨ и превращает их в короткие видео 🎥\n\n"

        "📌 <b>Как пользоваться ботом:</b>\n"
        "1️⃣ Нажмите кнопку «✨ Оживить фото»\n"
        "2️⃣ Отправьте фотографию (лучше портрет с чётким лицом)\n"
        "3️⃣ Напишите короткое описание, что должно происходить:\n"
        "   ─ эмоции (<i>улыбка, радость, удивление</i>)\n"
        "   ─ движение (<i>моргнуть, повернуть голову</i>)\n"
        "   ─ фон или стиль (<i>свет, настроение</i>)\n"
        "4️⃣ Через 1–2 минуты вы получите готовое видео 🎬\n\n"

        "💡 <b>Примеры промтов для оживления:</b>\n"
        "• Улыбнись и моргни глазами\n"
        "• Посмотри влево и слегка наклони голову\n"
        "• Лёгкая улыбка + подмигни 😉\n"
        "• Вдохни и выдохни, как будто вспоминаешь что-то приятное\n\n"

        "⚠️ <b>Советы для лучшего результата:</b>\n"
        "• Используйте фото с чётким лицом и хорошим освещением\n"
        "• Лучше всего подходят портретные снимки\n"
        "• Избегайте размытых лиц\n\n"

        "✨ Загружайте фото — и ваши воспоминания оживут прямо сейчас! ✨"
    )


    # Кнопки
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Загрузить фото", callback_data="animate")],
        [InlineKeyboardButton("🔙 В меню", callback_data="back_menu")]
    ])

    await send_or_replace_text(update, context, text, parse_mode="HTML", reply_markup=kb)


    # при наличии видеоинструкции — прикрепляем видео отдельным сообщением
    if settings.instruction_video_url:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(settings.instruction_video_url) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    await context.bot.send_video(chat_id=update.effective_chat.id, video=data)
