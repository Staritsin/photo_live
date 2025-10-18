from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaVideo
from telegram.ext import ContextTypes
import asyncio

from sqlalchemy import select

from config import settings
from db.database import get_session
from db.models import User
from .utils import send_or_replace_text
from services import gsheets

from services.billing_core import calc_generations
from services import billing_core
from db.repo import get_referral_stats, has_generations




# Главное меню
def main_menu_kb(user) -> InlineKeyboardMarkup:
    buttons = []

    buttons.append([InlineKeyboardButton("✨ Оживить фото", callback_data="animate")])
    buttons.append([InlineKeyboardButton("💳 Пополнить генерации", callback_data="balance")])
    buttons.append([InlineKeyboardButton("📖 Видео - Инструкция", callback_data="instruction")])

    return InlineKeyboardMarkup(buttons)


# Проверка/создание пользователя
async def ensure_user(update: Update) -> User:
    user_tg = update.effective_user

    async with get_session() as session:

        result = await session.execute(select(User).where(User.user_id == user_tg.id))
        user = result.scalar_one_or_none()

        if not user:
            # 🔹 Новый пользователь
            user = User(
                id=user_tg.id,
                user_id=update.effective_user.id,
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


# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user

    # 1. billing_core
    user_gen = await billing_core.upsert_user(tg_user.id, tg_user.username)

    # ✅ если вдруг вернулся coroutine — дожидаемся его
    if asyncio.iscoroutine(user_gen):
        user_gen = await user_gen




    # --- проверка есть ли реф ---
    args = context.args
    if args and args[0].startswith("ref"):
        referrer_id = int(args[0].replace("ref", ""))
        if referrer_id != tg_user.id:  # нельзя самому себе
            # 🔹 сохраняем в БД
            from db.repo import add_referral
            await add_referral(inviter_id=referrer_id, invited_id=tg_user.id)

            # 🔹 логируем в Google Sheets (только для наглядности)
            asyncio.create_task(gsheets.log_referral(
                referrer_id=referrer_id,
                new_user_id=tg_user.id,
                status="registered"
            ))

    # 2. SQLAlchemy (согласие)
    async with get_session() as session:
        # Проверяем, есть ли уже пользователь с таким user_id
        result = await session.execute(select(User).where(User.user_id == tg_user.id))
        user_db = result.scalar_one_or_none()

        if user_db:
            print(f"⚡️ Пользователь уже существует ({tg_user.id}) — обновляю данные")
            user_db.full_name = tg_user.full_name
            user_db.username = tg_user.username
            await session.commit()
        else:
            print("🆕 СОЗДАЮ НОВОГО ПОЛЬЗОВАТЕЛЯ")
            user_db = User(
                id=tg_user.id,
                user_id=tg_user.id,
                full_name=tg_user.full_name,
                username=tg_user.username,
                balance=0,  # 🎁 стартовый баланс
                consent_accepted=False,
            )
            session.add(user_db)
            await session.commit()
            print(f"👤 Новый пользователь создан: {tg_user.id} ({tg_user.username})")

            # Логируем нового пользователя в Google Sheets
            asyncio.create_task(gsheets.log_unique_user(
                user_id=tg_user.id,
                username=tg_user.username or "",
                full_name=tg_user.full_name or ""
            ))

            asyncio.create_task(gsheets.log_user_event(
                user_id=tg_user.id,
                username=tg_user.username or "",
                event="start_registered",
                meta={"balance": user_db.balance}
            ))



    # Лог входа
    asyncio.create_task(gsheets.log_user_event(
        user_id=user_gen.user_id,
        username=user_gen.username or "",
        event="start",
        meta={"generations_balance": user_gen.generations_balance}
    ))

    if not user_db.consent_accepted:
        text = (
            "Пожалуйста ознакомьтесь с документами перед началом:\n\n"
            f"📄 Пользовательское соглашение:\n{USER_AGREEMENT_URL}\n\n"
            f"🔒 Политика конфиденциальности:\n{PRIVACY_POLICY_URL}"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Согласен", callback_data="consent_yes")]])
        await send_or_replace_text(update, context, text, reply_markup=kb)
        return

    # показываем главное меню
    await show_main_menu(update, context, user_gen)


# Главное меню
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, user):
    gen_price = settings.price_rub
    packs = settings.packs
    

    # ---- реальные цифры из БД (а не из billing_core) ----
    
    async with get_session() as session:

        udb = (await session.execute(
            select(User).where(User.user_id == user.user_id)
        )).scalar_one()

        paid_balance = int(udb.balance)                # что реально списывается

    # сколько всего начислено за рефералов (информативно, не "остаток")
    invited_total, invited_paid = await get_referral_stats(user.user_id)
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
    invite_link = f"https://t.me/Photo_AliveBot?start=ref{user.user_id}"

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
            switch_inline_query=f"🔥 Попробуй оживить фото! Это моя ссылка: {invite_link}"
        )],
    ])

    # ---- отправка ----
    video_path = "assets/main_menu_video.mp4"
    try:
        await update.effective_chat.send_video(
            video=open(video_path, "rb"),
            caption=text,
            parse_mode="Markdown",
            reply_markup=kb
        )
    except Exception:
        await send_or_replace_text(update, context, text, reply_markup=kb)

# Обработка согласия
async def handle_consent_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from sqlalchemy import select
    q = update.callback_query
    await q.answer()

    async with get_session() as session:

        result = await session.execute(select(User).where(User.user_id == q.from_user.id))
        user = result.scalar_one()
        user.consent_accepted = True
        await session.commit()

        # Показываем главное меню (через billing_core)
        user_gen = billing_core.get_user(q.from_user.id)
        await show_main_menu(update, context, user_gen)


# Проверка баланса при нажатии "Оживить фото"
async def check_balance_and_animate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user_id = q.from_user.id

    # Лог в Google Sheets: нажал кнопку "Оживить фото"
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
        result = await session.execute(select(User).where(User.user_id == update.effective_user.id))
        user = result.scalar_one()
        user.consent_accepted = False
        await session.commit()

    
    await send_or_replace_text(update, context, "✅ Согласие сброшено. Используйте /start для повторного показа соглашения.")
