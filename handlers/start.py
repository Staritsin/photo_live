from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaVideo
from telegram.ext import ContextTypes
import asyncio
import time

from services.performance_logger import measure_time
from sqlalchemy import select
from sqlalchemy import text as sql_text


from config import settings
from db.database import get_session
from db.models import User
from .utils import send_or_replace_text
from services import gsheets

from services.billing_core import calc_generations
from services import billing_core
from db.repo import get_referral_stats, has_generations

from pathlib import Path
FILE_ID_PATH = Path("assets/main_menu_video.id")





# Главное меню
@measure_time
def main_menu_kb(user) -> InlineKeyboardMarkup:
    buttons = []

    buttons.append([InlineKeyboardButton("✨ Оживить фото", callback_data="animate")])
    buttons.append([InlineKeyboardButton("💳 Пополнить генерации", callback_data="balance")])
    buttons.append([InlineKeyboardButton("📖 Видео - Инструкция", callback_data="instruction")])

    return InlineKeyboardMarkup(buttons)


# Проверка/создание пользователя
@measure_time
async def ensure_user(update: Update) -> User:
    user_tg = update.effective_user

    async with get_session() as session:

        result = await session.execute(select(User).where(User.id == user_tg.id))
        user = result.scalar_one_or_none()

        if not user:
            # 🔹 Новый пользователь
            user = User(
                id=user_tg.id,
                full_name=user_tg.full_name,
                username=user_tg.username,
                balance=0,
                consent_accepted=False,
            )
            session.add(user)
            await session.commit()



        else:
            # 🔹 Обновляем данные
            user.full_name = user_tg.full_name
            user.username = user_tg.username
            await session.commit()

        # ✅ всегда возвращаем billing_core-юзера
        return billing_core.get_user(user_tg.id)


# Ссылки на документы
USER_AGREEMENT_URL = "https://clck.ru/3PEqg8"
PRIVACY_POLICY_URL = "https://clck.ru/3PEqbo"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start_time = time.perf_counter()
    tg_user = update.effective_user
    chat_id = update.effective_chat.id
    print(f"🚀 /start от {tg_user.username or tg_user.id}")

    # 👇 гарантируем объект message даже при callback / webhook
    if update.message:
        send = update.message.reply_text
    else:
        send = update.effective_chat.send_message

    await send("👋 Бот запущен, проверяем связь с сервером... 🔥")

    # ⚙️ создаём или обновляем пользователя в фоне
    asyncio.create_task(billing_core.upsert_user(tg_user.id, tg_user.username))
    asyncio.create_task(gsheets.log_user_event(
        user_id=tg_user.id, username=tg_user.username or "", event="start_pressed"
    ))

    # --- Проверка есть ли реферал ---
    args = context.args
    if args and args[0].startswith("ref"):
        referrer_id = int(args[0].replace("ref", ""))
        if referrer_id != tg_user.id:
            from db.repo import add_referral
            await add_referral(inviter_id=referrer_id, invited_id=tg_user.id)
            asyncio.create_task(gsheets.log_referral(
                referrer_id=referrer_id, new_user_id=tg_user.id, status="registered"
            ))

    # --- Создаём или обновляем запись в Postgres ---
    async with get_session() as session:
        result = await session.execute(select(User).where(User.id == tg_user.id))
        user_db = result.scalar_one_or_none()

        if not user_db:
            # 🔹 если юзера нет — создаём
            user_db = User(
                id=tg_user.id,
                full_name=tg_user.full_name,
                username=tg_user.username,
                balance=0,
                consent_accepted=False,
            )
            session.add(user_db)
            print(f"🆕 Создан новый пользователь {tg_user.username or tg_user.id}")
        else:
            # 🔹 если есть — обновляем данные
            user_db.full_name = tg_user.full_name
            user_db.username = tg_user.username
            print(f"♻️ Обновлён пользователь {tg_user.username or tg_user.id}")

        await session.commit()

        # 🔹 возвращаем финальный объект после сохранения
        result = await session.execute(select(User).where(User.id == tg_user.id))
        user_db = result.scalar_one()


        # === AUTO UPSERT USER (создание/обновление в основной таблице) ===
        await session.execute(sql_text("""
            INSERT INTO users (id, username, full_name, created_at)
            VALUES (:id, :username, :full_name, NOW())
            ON CONFLICT (id) DO UPDATE 
            SET username = EXCLUDED.username,
                full_name = EXCLUDED.full_name,
                last_active_at = NOW();
        """), {
            "id": tg_user.id,
            "username": tg_user.username,
            "full_name": tg_user.full_name
        })
        await session.commit()


    # === ПОСЛЕ СОХРАНЕНИЯ user_db ===
    if user_db.consent_accepted:
        # ⚡ Если согласие уже есть — сразу показываем меню
        await show_main_menu(update, context, user_db)
    else:
        # ⚡ Если согласия нет — показываем только кнопку
        text = (
            "👋 Привет! Я помогу оживить твои фото.\n\n"
            "Перед началом ознакомься с документами:\n\n"
            f"📄 Пользовательское соглашение:\n{USER_AGREEMENT_URL}\n\n"
            f"🔒 Политика конфиденциальности:\n{PRIVACY_POLICY_URL}\n\n"
            "Нажми «✅ Согласен», чтобы начать 🔥"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Согласен", callback_data="consent_yes")]
        ])
        await send_or_replace_text(update, context, text, reply_markup=kb)
        print(f"⚡️ Время выполнения start(): {time.perf_counter() - start_time:.2f} сек")




# Главное меню
@measure_time
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, user):
    start_time = time.perf_counter()
    gen_price = settings.price_rub
    packs = settings.packs
    

    # ---- реальные цифры из БД (а не из billing_core) ----
    
    async with get_session() as session:

        udb = (await session.execute(
            select(User).where(User.id == update.effective_user.id)
        )).scalar_one()

        paid_balance = int(udb.balance)                # что реально списывается

    # сколько всего начислено за рефералов (информативно, не "остаток")
    invited_total, invited_paid = await get_referral_stats(user.id)

    bonus_total = invited_paid * settings.bonus_per_friend

    total_available = paid_balance + bonus_total


    # ---- текст ----
    text = f"👋 {update.effective_user.first_name}, здравствуйте!\n"

    text += "✨ Готовы оживить фото? 🚀\n\n"

    text += (
        "💖 Мы поможем оживить дорогие сердцу фотографии ✨\n"
        "🎉 Подарить эмоции и превратить фото в живые моменты!\n\n"
        "*Как оживить фото:*\n"
        "1️⃣ Нажмите «✨ Оживить фото»\n"
        "2️⃣ Отправьте фотографию с четкими лицами/в анфас\n"
        "3️⃣ Опишите, как хотите оживить (эмоция + действия)\n"
        "4️⃣ Получите готовое видео 🎬\n"
        "5️⃣ Подарите эмоцию близким 💌\n\n"


        f"💰 *Стоимость:* 1 оживление = 1 генерация = {gen_price} ₽\n\n"
    )
    text += (
        f"\n🧾 Ваш баланс на: {total_available} генераций\n"
        f"✨ Начислено за друзей: +{bonus_total}\n\n"
        f"📢 Приглашайте друзей → за каждого нового пользователя получайте +{settings.bonus_per_friend} генерацию в 🎁\n\n"
        f" (т.е. если вы пригласили друга, и он оплатил любой из тарифов, вы автоматически получаете +{settings.bonus_per_friend} генерацию в 🎁)\n"

    )

    # ---- кнопки ----
    invite_link = f"https://t.me/Photo_AliveBot?start=ref{user.id}"


    # 💡 динамическая кнопка — если баланс 0 → показать цену
    if total_available == 0:
        balance_label = f"💳 Попробовать за {gen_price} ₽"
    else:
        balance_label = "💳 Пополнить генерации"


    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(balance_label, callback_data="balance")],
        [InlineKeyboardButton("✨ Оживить фото", callback_data="animate")],
        [InlineKeyboardButton("📖 Инструкция", callback_data="instruction")],
        [InlineKeyboardButton(
            "🤝 Пригласить друга",
            switch_inline_query=f"🔥 Попробуй оживить фото! Я тебя приглашаю: {invite_link}"
        )],
    ])

        # === Быстрое приветственное видео ===
        try:
            from pathlib import Path
            import aiofiles
    
            video_id_path = Path("assets/main_menu_video.id")
    
            if video_id_path.exists():
                # ⚡ используем кэшированный file_id (мгновенно)
                async with aiofiles.open(video_id_path, "r") as f:
                    file_id = (await f.read()).strip()
    
                await update.effective_chat.send_chat_action("upload_video")
                await update.effective_chat.send_video(
                    video=file_id,
                    caption=text,
                    parse_mode="Markdown",
                    reply_markup=kb
                )
    
            else:
                # 📥 если нет file_id — грузим mp4, сохраняем id
                video_path = Path("assets/main_menu_video.mp4")
                msg = await update.effective_chat.send_video(
                    video=open(video_path, "rb"),
                    caption=text,
                    parse_mode="Markdown",
                    reply_markup=kb
                )
                try:
                    fid = msg.video.file_id
                    video_id_path.write_text(fid)
                    print(f"💾 Saved new video file_id: {fid}")
                except Exception as e:
                    print(f"⚠️ Ошибка сохранения file_id: {e}")
    
        except Exception as e:
            print(f"⚠️ send_video fallback: {e}")
            await send_or_replace_text(update, context, text, reply_markup=kb)
    
        print(f"⚡️ Время выполнения show_main_menu(): {time.perf_counter() - start_time:.2f} сек")
    

# === Обработка согласия ===
async def handle_consent_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        await q.answer()
    except Exception:
        pass

    user_gen = None


    async with get_session() as session:
        result = await session.execute(select(User).where(User.id == q.from_user.id))
        user = result.scalar_one_or_none()
        if user and not user.consent_accepted:
            user.consent_accepted = True
            await session.commit()
            print(f"✅ Пользователь {user.id} принял соглашение")


            asyncio.create_task(gsheets.log_user_event(
                user_id=q.from_user.id,
                username=q.from_user.username or "",
                event="consent_accepted"
            ))

    # 🚀 Показываем главное меню (без двойного вызова)
    await show_main_menu(update, context, user or q.from_user)

# Проверка баланса при нажатии "Оживить фото"
@measure_time
async def check_balance_and_animate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        await q.answer()
    except Exception:
        pass

    user_id = q.from_user.id

    # Лог в Google Sheets: нажал кнопку "Оживить фото"
    if gsheets.ENABLED:
        asyncio.create_task(gsheets.log_user_event(
            user_id=user_id,
            username=q.from_user.username or "",
            event="click_animate",
            meta={}
        ))

    # Проверяем баланс генераций (через has_generations)
    if not await has_generations(user_id):
        # 💬 Уведомление перед открытием меню оплаты
        await send_or_replace_text(update, context, 
            "⚠️ У Вас закончились генерации.\n"
            "💳 Выберите удобный тариф, чтобы продолжить ✨"
        )
        
        # 💳 Сразу открываем меню пополнения
        from handlers.balance import open_balance
        await open_balance(update, context)
        return


    # Если генерации есть → переходим к загрузке фото
    from handlers.photo import start_animate_flow
    await start_animate_flow(update, context)


# Команда для сброса согласия (для тестирования)
async def reset_consent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from sqlalchemy import select
    async with get_session() as session:
        result = await session.execute(select(User).where(User.id == update.effective_user.id))
        user = result.scalar_one()
        user.consent_accepted = False
        await session.commit()

    
    await send_or_replace_text(update, context, "✅ Согласие сброшено. Используйте /start для повторного показа соглашения.")
