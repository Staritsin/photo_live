# handlers/balance.py
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from sqlalchemy import select
from db.models import User, Referral, Payment
import asyncio

from config import settings
from db.database import get_session

from sqlalchemy.orm import selectinload

from services import yookassa as yk
from db.repo import get_referral_stats


# ============ Создание платежа ============
from services.gsheets import log_user_event, log_payment_attempt, log_payment_result, log_balance_change
from handlers.utils import send_or_replace_text


# ===== Создание ссылки на оплату =====
from services.tinkoff import create_payment as tinkoff_create, get_payment_status as tinkoff_status

import os, secrets, urllib.parse
from services.billing_core import calc_generations
from services import billing_core
from services import gsheets


ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))



def _make_test_link(amount: int, user_id: int, description: str):
    """Формирует простую тестовую ссылку на оплату Tinkoff (без API)."""
    order_id = f"{user_id}_{secrets.token_hex(4)}"
    url = (
        f"https://www.tinkoff.ru/kassa/demo/payform"
        f"?TerminalKey={settings.tinkoff_terminal_key}"
        f"&Amount={int(amount) * 100}"
        f"&OrderId={order_id}"
        f"&Description={urllib.parse.quote_plus(description)}"
    )
    return url, order_id



# ============ Временное пополнение генераций ============
async def add_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amount = 5  # сколько генераций добавить
    async with get_session() as session:

        result = await session.execute(select(User).where(User.id == update.effective_user.id))
        user = result.scalar_one()
        old = int(user.balance)
        user.balance = int(user.balance) + amount
        session.add(user)
        await session.commit()

        # лог в таблицу
        asyncio.create_task(log_balance_change(
            user_id=user.id,
            old_balance=old,
            delta=amount,
            new_balance=int(user.balance),
            reason="manual_add_balance",
        ))

        await update.message.reply_text(
            f"✅ Баланс пополнен на {amount} генераций\n"
            f"💳 Текущий баланс: {user.balance:.0f} генераций"
        )

# ============ Главное меню пополнения ============


async def open_balance(update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    async with get_session() as session:

        result = await session.execute(select(User).where(User.id == q.from_user.id))
        user = result.scalar_one()

        # лог в Google Sheets
        asyncio.create_task(gsheets.log_user_event(
            user_id=user.id,
            username=user.username,
            event="open_balance",
            meta={"balance": user.balance}
        ))

        gen_price = settings.price_rub
        packs = settings.packs  # [5, 15, 30, 50] = кол-во генераций

        # ---- считаем баланс (платный + рефералка) ----
        paid_balance = int(user.balance)
        invited_total, invited_paid = await get_referral_stats(user.id)
        referral_bonus = invited_paid * settings.bonus_per_friend
        total_available = paid_balance + referral_bonus

        # ---- текст ----
        text = (
            "💎 <b>ТАРИФЫ ГЕНЕРАЦИЙ</b>\n\n"
            f"💡 1 оживление = 1 генерация = <b>{gen_price} ₽</b>\n\n"
            f"🎉 Баланс: <b>{total_available}</b> генераций\n"
            "📌 Генерации <i>не сгорают</i> и копятся на аккаунте ✨\n\n"
            "⬇️ <b>ВЫБЕРИТЕ ПАКЕТ:</b>\n\n"
            f"🎁 За каждые 10 генераций → +{settings.bonus_per_10} в подарок!\n"
            f"🤝 За каждого приглашённого друга → +{settings.bonus_per_friend} в подарок!\n\n"
        )

        # #пробник: выводим тарифы по 2 в ряд
        buttons = []
        row = []
        for i, base in enumerate(packs):
            amount_rub = base * gen_price
            gens_total = calc_generations(base)
            bonus = gens_total - base
            label = f"{base} ген = {amount_rub} ₽"
            if bonus > 0:
                label += f" (+{bonus}🎁)"
            
            row.append(InlineKeyboardButton(label, callback_data=f"topup:{amount_rub}"))
            if len(row) == 2 or i == len(packs) - 1:
                buttons.append(row)
                row = []

        # кнопки внизу
        buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")])
        kb = InlineKeyboardMarkup(buttons)

        try:
            if q.message and (q.message.video or q.message.photo):
                await q.message.edit_caption(
                    caption=text, parse_mode="HTML", reply_markup=kb
                )
            else:
                await q.message.edit_text(
                    text=text, parse_mode="HTML", reply_markup=kb
                )
        except Exception:
            await context.bot.send_message(
                chat_id=q.message.chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=kb
            )

# ===== Создание ссылки на оплату =====

async def create_topup(update, context: ContextTypes.DEFAULT_TYPE, amount: int):
    q = update.callback_query
    await q.answer()

    description = "Пополнение генераций"
    provider = settings.payment_provider.upper()  # TINKOFF | YOOKASSA
    mode = settings.payment_mode.upper()



    import logging
    logging.info(f"💳 Создание оплаты: {provider} ({mode}) — {amount} ₽")


    url = None
    pay_id = None
    order_id = f"{q.from_user.id}_{secrets.token_hex(4)}"

    # === Расчёт генераций и бонуса ===
    base = amount // settings.price_rub
    total = calc_generations(base)
    bonus = total - base
    bonus_value = bonus * settings.price_rub
    approx_price = round(amount / total)

    # #пробник: текст для 1 и 10+ генераций
    if base <= 1:
        purchase_text = (
            f"💰 Сумма к оплате: {amount} ₽\n\n"
            f"🎬 {base} генерация = {settings.price_rub} ₽\n\n"
            "⚠️ После оплаты ОБЯЗАТЕЛЬНО нажмите кнопку «🔁 Проверить оплату», "
            "чтобы баланс пополнился в профиле ✅"
        )
    elif base <= 10:
        purchase_text = (
            f"💰 Сумма к оплате: {amount} ₽\n\n"
            f"🎬 {base} генераций = {settings.price_rub * base} ₽\n\n"
            "⚠️ После оплаты ОБЯЗАТЕЛЬНО нажмите кнопку «🔁 Проверить оплату», "
            "чтобы баланс пополнился в профиле ✅"
        )
    else:
        purchase_text = (
            f"💰 Сумма к оплате: {amount} ₽\n\n"
            f"🎬 {base} генераций +{bonus} бонусом → всего {total}\n"

            "⚠️ После оплаты ОБЯЗАТЕЛЬНО нажмите кнопку «🔁 Проверить оплату», "
            "чтобы баланс пополнился в профиле ✅"
        )

    if provider == "YOOKASSA":
        pay_id, url, order_id = await yk.create_payment(
            amount, description, q.from_user.id, order_id
        )


        asyncio.create_task(log_payment_attempt(
            user_id=q.from_user.id,
            username=update.effective_user.username or "",
            amount_rub=float(amount),
            order_id=order_id,
            mode="YOOKASSA",
            url=url,
        ))

    else:
        # Tinkoff
        try:
            pay_id, url, order_id = tinkoff_create(
                amount, description, q.from_user.id, order_id=order_id
            )
            asyncio.create_task(log_payment_attempt(
                user_id=q.from_user.id,
                username=update.effective_user.username or "",
                amount_rub=float(amount),
                order_id=order_id,
                mode=("PROD" if mode == "PROD" else "TEST"),
                url=url,
            ))
        except Exception as e:
            print("❌ create_payment failed:", repr(e))
            # === Fallback: тестовая ссылка на оплату ===
            url, order_id = _make_test_link(amount, q.from_user.id, description)
            pay_id = order_id
            asyncio.create_task(log_payment_attempt(
                user_id=q.from_user.id,
                username=update.effective_user.username or "",
                amount_rub=float(amount),
                order_id=order_id,
                mode="TINKOFF_FALLBACK",
                url=url,
            ))


    if not pay_id:
        pay_id = order_id

    # Кнопки
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"💳 Оплатить {amount} ₽", url=url)],
        [InlineKeyboardButton("🔁 Проверить оплату", callback_data=f"check_payment:{pay_id}")],
        [InlineKeyboardButton("⬅️ В меню", callback_data="back_menu")]
    ])

    await q.message.reply_text(
        text=purchase_text,
        reply_markup=kb
    )


# ===== Обработчик топапов (универсальный) =====
async def handle_topup(update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, amount = q.data.split(":", 1)
    await create_topup(update, context, int(amount))



# ===== Проверка платежа =====
async def check_payment(update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, pay_id = q.data.split(":", 1)

    # достаём платёж из базы
    async with get_session() as session:
        payment = (await session.execute(
            select(Payment).where(Payment.provider_payment_id == pay_id)
        )).scalar_one_or_none()

        if not payment:
            await send_or_replace_text(update, context, "⚠️ Платёж не найден в базе.")
            return

        provider = getattr(payment, "provider", settings.payment_provider).upper()
        amount_for_log = float(payment.amount)

    # проверяем статус по провайдеру
    if provider == "YOOKASSA":
        status = await asyncio.to_thread(yk.get_payment_status, pay_id)
    else:
        status = tinkoff_status(pay_id)


    # логируем событие
    asyncio.create_task(log_user_event(
        user_id=q.from_user.id,
        username=q.from_user.username or "",
        event="check_payment",
        meta={"pay_id": pay_id, "status": status, "provider": provider}
    ))

    # логируем результат
    asyncio.create_task(log_payment_result(
        user_id=q.from_user.id,
        username=q.from_user.username or "",
        payment_id=pay_id,
        status=status,
        amount_rub=amount_for_log,
    ))

    # успешный платёж
    if status in ["CONFIRMED", "AUTHORIZED"]:
        async with get_session() as session:
            payment = (await session.execute(
                select(Payment).where(Payment.provider_payment_id == pay_id)
            )).scalar_one_or_none()

            if payment and payment.status not in ["CONFIRMED", "AUTHORIZED"]:
                payment.status = status
                user = (await session.execute(
                    select(User).where(User.id == q.from_user.id)
                )).scalar_one()
                old = int(user.balance)

                base = int(payment.amount) // settings.price_rub
                gens_total = calc_generations(base)
                user.balance += gens_total

                await session.commit()

                asyncio.create_task(log_balance_change(
                    user_id=user.id,
                    old_balance=old,
                    delta=gens_total,
                    new_balance=int(user.balance),
                    reason=f"{provider.lower()}_payment_confirmed",
                ))
                
                # === РЕФЕРАЛКА ===
                from db.models import Referral

                referral = (await session.execute(
                    select(Referral).where(
                        Referral.invited_id == q.from_user.id,
                        Referral.bonus_awarded.is_(False)
                    )
                )).scalar_one_or_none()

                if referral:
                    # помечаем бонус выданным
                    referral.bonus_awarded = True
                    await session.commit()

                    # --- пригласившему +3 ---
                    ref_user = (await session.execute(
                        select(User).where(User.id == referral.inviter_id)
                    )).scalar_one_or_none()

                    if ref_user:
                        old = int(ref_user.balance)
                        ref_user.balance = old + settings.bonus_per_friend
                        await session.commit()

                        asyncio.create_task(log_balance_change(
                            user_id=ref_user.id,
                            old_balance=old,
                            delta=settings.bonus_per_friend,
                            new_balance=int(ref_user.balance),
                            reason="referral_bonus"
                        ))
                        inv_total, inv_paid = await get_referral_stats(ref_user.id)
                        bonus_total = inv_paid * settings.bonus_per_friend
                        asyncio.create_task(gsheets.update_referrals_summary(ref_user.id, inv_total, inv_paid, bonus_total))



        # ещё раз берём актуального юзера из БД, чтобы баланс был свежий
        async with get_session() as session:

            user = (await session.execute(
                select(User).where(User.id == q.from_user.id)
            )).scalar_one()
                        
        await send_or_replace_text(
            update,
            context,
            f"✅ Баланс пополнен!\n🎬 Теперь у вас {user.balance:.0f} генераций 🎉",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("✨ Оживить фото", callback_data="animate")]]
            )
        )
    elif status == "REJECTED":
        await send_or_replace_text(update, context, "❌ Платёж отклонён. Попробуйте снова.")
    else:
        await send_or_replace_text(update, context, f"⏳ Платёж ещё не завершён. Статус: {status}")

# ============ Обнуление генераций ============
async def reset_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with get_session() as session:
        result = await session.execute(select(User).where(User.id == update.effective_user.id))
        user = result.scalar_one()
        old = int(user.balance)  # старое количество генераций
        user.balance = 0  # обнуляем
        await session.commit()

        # логируем в таблицу
        asyncio.create_task(log_balance_change(
            user_id=user.id,
            old_balance=old,
            delta=-old,
            new_balance=0,
            reason="reset_generations",
        ))

        await update.message.reply_text("🔄 Баланс генераций обнулён. Теперь у Вас 0 генераций.")

# ============ Компенсация генераций через техподдержку ============
async def compensate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для админа: /compensate <user_id> <generations>"""

    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 У вас нет доступа к этой команде.")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text("⚠️ Использование: /compensate <user_id> <generations>")
        return

    try:
        user_id = int(context.args[0])
        gens_to_add = int(context.args[1])  # количество генераций
    except ValueError:
        await update.message.reply_text("⚠️ user_id и генерации должны быть числами")
        return

    async with get_session() as session:
        user = (await session.execute(
            select(User).where(User.id == user_id)
        )).scalar_one_or_none()

        if not user:
            await update.message.reply_text(f"❌ Пользователь с ID {user_id} не найден")
            return

        old = int(user.balance)
        user.balance = int(user.balance) + gens_to_add
        await session.commit()

        # лог
        asyncio.create_task(log_balance_change(
            user_id=user.id,
            old_balance=old,
            delta=gens_to_add,
            new_balance=int(user.balance),
            reason="compensate_generations",
        ))

        await update.message.reply_text(
            f"✅ Пользователю {user_id} добавлено {gens_to_add} генераций\n"
            f"🎉 Новый баланс: {user.balance} генераций"
        )


# ============ Проверка баланса пользователя (для админа) ============

async def get_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для админа: /get_balance <user_id> — выводит полные данные о пользователе"""

    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 У вас нет доступа к этой команде.")
        return

    if not context.args or len(context.args) < 1:
        await update.message.reply_text("⚠️ Использование: /get_balance <user_id>")
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ user_id должен быть числом")
        return

    async with get_session() as session:
        # Загружаем юзера вместе со всеми связями
        user = (await session.execute(
            select(User)
            .options(
                selectinload(User.referrals_as_inviter),
                selectinload(User.referrals_as_invited),
                selectinload(User.payments)
            )
            .where(User.id == user_id)
        )).scalar_one_or_none()

        if not user:
            await update.message.reply_text(f"❌ Пользователь с ID {user_id} не найден")
            return

        # ==== Баланс ====
        invited_total, invited_paid = await get_referral_stats(user.id)
        referral_bonus = invited_paid * settings.bonus_per_friend
        balance = int(user.balance)
        total_generations = balance + referral_bonus

        # ==== Последний платёж ====
        if user.payments:
            last_payment = sorted(user.payments, key=lambda p: p.created_at)[-1]
            pay_info = (
                f"💳 Последний платёж:\n"
                f"• Сумма: {last_payment.amount} ₽\n"
                f"• Статус: {last_payment.status}\n"
                f"• Провайдер: {last_payment.provider}\n"
                f"• Дата: {last_payment.created_at.strftime('%d.%m.%Y %H:%M')}\n\n"
            )
        else:
            pay_info = "💳 Платежей пока нет\n\n"

        # ==== Рефералы ====
        invited_count = len(user.referrals_as_inviter)
        invited_by_count = len(user.referrals_as_invited)

        # ==== Ответ ====
        text = (
            f"👤 ID: <b>{user.id}</b>\n"
            f"📛 Username: @{user.username or '—'}\n\n"
            f"🎬 Всего генераций: <b>{total_generations}</b>\n"
            f"💳 Оплачено: <b>{balance}</b>\n"
            f"🤝 Реферальный бонус: +{referral_bonus}\n\n"
            f"👥 Пригласил: {invited_count} пользователей\n"
            f"📥 Был приглашён: {invited_by_count}\n\n"
            f"{pay_info}"
        )

        await update.message.reply_text(text, parse_mode="HTML")


# ============ Сброс пробной генерации ============
from handlers.start import show_main_menu   # если не импортирован — добавь


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает баланс, рефералку и ссылку на приглашение"""
    user_id = update.effective_user.id
    username = update.effective_user.username or "—"
    invite_link = f"https://t.me/Photo_AliveBot?start=ref{user_id}"

    async with get_session() as session:
        user = (await session.execute(
            select(User).where(User.id == user_id)
        )).scalar_one_or_none()

        if not user:
            await update.message.reply_text("❌ Пользователь не найден.")
            return

        balance = int(user.balance)
        invited_total, invited_paid = await get_referral_stats(user_id)
        bonus_total = invited_paid * settings.bonus_per_friend
        total_generations = balance + bonus_total

        referrer_username = (await session.execute(
            select(User.username)
            .join_from(User, Referral, Referral.inviter_id == User.id)
            .where(Referral.invited_id == user_id)
        )).scalar_one_or_none()

    # лог в Google Sheets
    asyncio.create_task(gsheets.log_user_event(
        user_id=user_id,
        username=username,
        event="cmd_balance_opened",
        meta={
            "balance": balance,
            "bonus_total": bonus_total,
            "invited_total": invited_total,
            "invited_paid": invited_paid
        }
    ))

    # === ТЕКСТ ===
    text = (
        f"💎 <b>МОЙ ПРОФИЛЬ</b>\n\n"
        f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
        f"👤 <b>Логин:</b> @{username}\n\n"
        f"🎬 <b>Всего генераций:</b> {total_generations}\n"
        f"💳 Оплачено: {balance}\n"
        f"🤝 За друзей: {bonus_total}\n\n"
        f"👥 Приглашено: {invited_total} (из них оплатили: {invited_paid})\n\n"
    )

    if referrer_username:
        text += f"👤 Вас пригласил @{referrer_username}\n\n"
    else:
        text += (
            f"📢 Приглашайте друзей — получайте бонусные генерации!\n"
            f"🎁 За каждого оплатившего друга: +{settings.bonus_per_friend} генерация.\n\n"
        )

    safe_link = f"<code>{invite_link}</code>"
    text += (
        f"🔗 Ваша пригласительная ссылка - (нажмите на нее, чтобы скопировать):\n{safe_link}\n\n"
        "💡 Поделитесь ею в соцсетях и увеличивайте свой баланс 🚀"
    )

    # === КНОПКИ ===
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✨ Оживить фото", callback_data="animate"),
            InlineKeyboardButton("💳 Пополнить", callback_data="balance")
        ],
        [
            InlineKeyboardButton(
                "🤝 Поделиться",
                switch_inline_query=f"🔥 Попробуй оживить фото! Перейди по моей ссылке 👉 {invite_link}"
            )
        ]
    ])

    await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")


# обновляем handle_balance
async def handle_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /balance вызывает cmd_balance"""
    await cmd_balance(update, context)

# handlers/balance.py
# ============ Полный сброс ============
async def reset_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Полный сброс: баланс = 0, рефералы удалены"""

    # --- проверка прав ---
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 Нет доступа.")
        return

    # --- какой user_id сбрасываем ---
    if context.args and len(context.args) >= 1:
        try:
            user_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("⚠️ user_id должен быть числом")
            return
    else:
        user_id = update.effective_user.id  # если аргумента нет — сбрасываем себе

    # --- работа с БД ---
    async with get_session() as session:
        from sqlalchemy import delete
        from db.models import Referral, User

        user = (await session.execute(
            select(User).where(User.id == user_id)
        )).scalar_one_or_none()

        if not user:
            await update.message.reply_text(f"❌ Пользователь {user_id} не найден")
            return

        # обнуляем баланс
        user.balance = 0
    

        # удаляем все рефералы (и как пригласивший, и как приглашённый)
        await session.execute(delete(Referral).where(
            (Referral.inviter_id == user.id) | (Referral.invited_id == user.id)
        ))

        await session.commit()

        if user_id == update.effective_user.id:
            await update.message.reply_text(
                "✅ Полный сброс выполнен для тебя!\n"
                "Баланс = 0\n"
                "🤝 Все рефералы очищены"
            )
        else:
            await update.message.reply_text(
                f"✅ Полный сброс выполнен для пользователя {user_id}!\n"
                f"Баланс = 0\n"
                f"🤝 Все рефералы очищены"
            )

__all__ = [
    "add_balance", "open_balance", "create_topup", "handle_topup",
    "check_payment", "reset_balance", "compensate", "get_balance",
    "cmd_balance", "handle_balance", "reset_all"
]
