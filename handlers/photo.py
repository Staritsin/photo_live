from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from telegram.ext import ContextTypes
import tempfile
import os
import asyncio

from db.models import User, GenerationRaw


from config import settings
from db.database import get_session
from db.models import User
from services.replicate_kling import generate_video_from_photo
from .utils import send_or_replace_text, delete_message_safe
from services import gsheets
from db.repo import get_referral_stats, has_generations  # добавь импорт вверху файла
import time




PHOTO_KEY = "photo_bytes"
PROMPT_KEY = "prompt"
LAST_MSG_ID = "last_message_id"


_delete_message_safe = delete_message_safe
_send_or_replace_text = send_or_replace_text


def back_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 В меню", callback_data="back_menu")]])


async def start_animate_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):

    # лог: пользователь открыл поток анимации
    asyncio.create_task(gsheets.log_user_event(
        user_id=update.effective_user.id,
        username=update.effective_user.username or "",
        event="start_animate_flow",
    ))

    context.user_data.clear()
    text = (
        "✨ Сделайте своё фото живым!\n"
        "🖼 Ваши воспоминания заслуживают движения. Оживите их за секунды и создайте уникальное видео, которое никого не оставит равнодушным!\n\n"
        "Простой шаг — мгновенный результат.\n"
        "Загрузите фото сейчас и почувствуйте магию! 🪄\n"
    )
    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            pass

        await update.callback_query.message.reply_text(text, reply_markup=back_menu_kb())
    else:
        await update.message.reply_text(text, reply_markup=back_menu_kb())

async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик получения фото от пользователя"""
    file = await update.message.photo[-1].get_file()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_file:
        await file.download_to_drive(temp_file.name)
        photo_path = temp_file.name

    context.user_data["last_photo_path"] = photo_path

    try:
        await update.message.delete()
    except Exception:
        pass

    text = (
        "✍️ Опишите, что должно произойти на видео — какие движения, эмоции или действия вы хотите видеть.\n"
        "Старайтесь указывать минимум два действия, чтобы результат выглядел естественно 💫 \n\n"

        "Пример:\n"
        "Люди на фото улыбаются, машут руками и обнимаются.\n"
        "Человек на фото подмигивает и поворачивает голову влево.\n"
        "Мама берет ребенка на руки и целует.\n\n"
        "Если не знаете, что написать — просто напишите «улыбается и машет рукой» или «шлет воздушный поцелуй».\n\n"
        "Каждое фото - это история, которую стоит оживить! ✨\n"
    )

    with open(photo_path, "rb") as photo_file:
        msg = await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=photo_file,
            caption=text,
            reply_markup=back_menu_kb()
        )

    # Сохраняем ID сообщения с фото
    context.user_data[LAST_MSG_ID] = msg.message_id
    # лог: фото получено
    asyncio.create_task(gsheets.log_user_event(
        user_id=update.effective_user.id,
        username=update.effective_user.username or "",
        event="photo_received",
        meta={}
    ))

async def on_prompt_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "last_photo_path" not in context.user_data:
        await update.message.reply_text("⚠️ Сначала отправьте фото!")
        return

    prompt_text = update.message.text.strip()
    context.user_data[PROMPT_KEY] = prompt_text

    # Удаляем сообщение юзера с текстом, чтобы оставить одно "окно"
    try:
        await update.message.delete()
    except Exception:
        pass

    text = (
        "📸 Фото получено!\n\n"
        f"✍️ Сценарий: *{prompt_text}*\n\n"
        "Эмоции, которые не передать словами, теперь видны на видео!\n"
        "Нажмите «Оживить» и удивите всех за пару мгновений ✨\n"
    )

    buttons = [
        [InlineKeyboardButton("✨ Оживить фото", callback_data="do_animate")],
        [InlineKeyboardButton("🔙 В меню", callback_data="back_menu")]
    ]

    try:
        # Пробуем обновить caption у фото
        await context.bot.edit_message_caption(
            chat_id=update.effective_chat.id,
            message_id=context.user_data[LAST_MSG_ID],
            caption=text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception:
        # Если не получилось (например, фото старое или нет прав) — отправляем новое сообщение
        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    # ✅ Лог: пользователь ввёл промпт (всегда срабатывает)
    asyncio.create_task(gsheets.log_user_event(
        user_id=update.effective_user.id,
        username=update.effective_user.username or "",
        event="prompt_entered",
        meta={"prompt": prompt_text[:120]}
    ))
    

async def on_animate_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        await q.answer()
    except Exception:
        pass

    # лог: нажал кнопку подтверждения генерации
    asyncio.create_task(gsheets.log_user_event(
        user_id=q.from_user.id,
        username=q.from_user.username or "",
        event="animate_confirm",
        meta={}
    ))

    if "last_photo_path" not in context.user_data or PROMPT_KEY not in context.user_data:
        await q.message.reply_text("⚠️ Сначала загрузите фото и напишите, как оживить!")
        return

    # быстрый ответ пользователю
    await q.message.edit_caption(
        "🎬 Генерация видео началась!\n"
        "⏳ Это займёт около 30–60 секунд.\n\n"
        "👉 Можете пока закрыть бота — я пришлю готовое видео автоматически 🙌"
    )

    # 🚀 запускаем генерацию в фоне
    asyncio.create_task(run_generation_task(update, context))



async def run_generation_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        await q.answer()
    except Exception:
        pass

    user_id = q.from_user.id
    prompt_text = context.user_data.get(PROMPT_KEY)
    photo_path = context.user_data.get("last_photo_path")


    async with get_session() as session:
        from sqlalchemy import select
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Пользователь не найден в базе.")
            return  
        
        # 💰 Проверка баланса
        if user.balance <= 0:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ У тебя закончились генерации.\nПополните баланс 👇",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Пополнить баланс", callback_data="balance")],
                    [InlineKeyboardButton("🔙 В меню", callback_data="back_menu")]
                ])
            )
            return

        start_time = time.time()

        try:
            # 🎬 Генерация видео
            async for status in generate_video_from_photo(photo_path, duration=4, prompt=prompt_text):
                if status["status"] == "processing":
                    continue

                # === Fal не сработал → пробуем Replicate ===
                if status["status"] == "failed" and "Fal.ai" in status.get("error", ""):
                    print("⚠️ Fal.ai не сработал — переключаемся на Replicate...")
                    os.environ["ENGINE"] = "replicate"
                    async for backup in generate_video_from_photo(photo_path, duration=4, prompt=prompt_text):
                        status = backup
                        break

                # === Успешно ===
                if status["status"] == "succeeded":
                    video_url = status["url"]
                    engine_name = os.getenv("ENGINE", "replicate").upper()
                    gen_secs = int(time.time() - start_time)

                    new_balance_after = max(0, int(user.balance) - 1)
                    primary_row = (
                        [InlineKeyboardButton("✨ Оживить ещё фото", callback_data="animate")]
                        if new_balance_after > 0 else
                        [InlineKeyboardButton("💳 Пополнить баланс", callback_data="balance")]
                    )

                    kb = InlineKeyboardMarkup([
                        primary_row,
                        [InlineKeyboardButton("🏠 В меню", callback_data="back_menu")]
                    ])

                    msg = await context.bot.send_video(
                        chat_id=update.effective_chat.id,
                        video=video_url,
                        caption=(
                            f"✅ *Видео готово!*\n\n"
                            f"🎬 Движок: *{engine_name}*\n"
                            f"✨ Промпт: {prompt_text}\n"
                            f"⏱ Время генерации: {gen_secs} сек."
                        ),
                        parse_mode="Markdown",
                        reply_markup=kb
                    )

                    video_file_id = msg.video.file_id if msg and msg.video else ""


                    # 💾 Списание и логирование
                    old = int(user.balance)
                    user.balance -= 1
                    user.total_generations = (user.total_generations or 0) + 1
                    await session.commit()  # ✅ фиксируем изменение баланса


                    # 💾 Запись генерации
                    gen_entry = GenerationRaw(
                        user_id=user.id,
                        price_rub=float(settings.price_rub),
                        input_type="photo",
                        prompt=prompt_text[:1024],
                        file_id=video_file_id,
                    )
                    session.add(gen_entry)
                    await session.commit()  # ✅ фиксируем запись генерации




                    invited_total, invited_paid = await get_referral_stats(user.id)
                    referral_bonus = invited_paid * settings.bonus_per_friend

                    asyncio.create_task(gsheets.log_balance_change(
                        user_id=user.id,
                        old_balance=old,
                        delta=-1,
                        new_balance=int(user.balance),
                        reason="consume_generation",
                        referral_bonus=referral_bonus
                    ))

                    asyncio.create_task(gsheets.log_generation(
                        user_id=user.id,
                        username=user.username or "",
                        price_rub=float(settings.price_rub),
                        input_type="photo",
                        prompt=prompt_text,
                        file_id=video_file_id
                    ))

                    return

                elif status["status"] == "failed":
                    raw_error = status.get('error', 'Неизвестная ошибка')

                    if "content_policy_violation" in raw_error:
                        error_text = (
                            "🚫 Видео не может быть создано.\n\n"
                            "❗️Причина: контент нарушает политику безопасности модели "
                            "(например, политика, лица известных людей, насилие и т.д.).\n\n"
                            "🪄 Попробуй другое фото или измени описание (prompt)."
                        )
                    else:
                        error_text = f"❌ Ошибка при генерации видео:\n{raw_error[:4000]}"

                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=error_text,
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔁 Попробовать снова", callback_data="do_animate")],
                            [InlineKeyboardButton("🏠 В меню", callback_data="back_menu")]
                        ])
                    )
                    return

        finally:
            if os.path.isfile(photo_path):
                os.remove(photo_path)
            for key in ["last_photo_path", PROMPT_KEY]:
                context.user_data.pop(key, None)

# Вызываем основную генерацию при нажатии кнопки
async def do_animate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        await q.answer()
    except Exception:
        pass

    # проверяем, есть ли генерации
    if not await has_generations(q.from_user.id):
        await context.bot.send_message(
            chat_id=q.message.chat_id,
            text="⚠️ У тебя закончились генерации.\nПополните баланс 👇",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Пополнить баланс", callback_data="balance")],
                [InlineKeyboardButton("🔙 В меню", callback_data="back_menu")]
            ])
        )
        return   # стоп, не идём дальше

    # если генерации есть → запускаем основной пайплайн
    await on_animate_click(update, context)
