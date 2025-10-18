# main.py — финальная стабильная версия
import time
start_time = time.perf_counter()

import os
import asyncio
import signal
import logging
from pathlib import Path
from dotenv import load_dotenv

# === 1. Логи (чистые, без мусора) ===
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)

# === 2. Загружаем .env до всех импортов ===
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

from config import settings
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
)
from db.database import init_db
from services import gsheets
from services.auto_sync_dashboard import auto_loop, sync_dashboard_once
from handlers.start import (
    start, handle_consent_yes, ensure_user,
    check_balance_and_animate, reset_consent, show_main_menu
)
from handlers.photo import on_photo, on_prompt_text, do_animate
from handlers.balance import (
    open_balance, check_payment, add_balance,
    reset_balance, handle_topup, compensate,
    get_balance, handle_balance, reset_all
)
from handlers.instruction import show_instruction
from handlers.support import open_support


# === 3. Отладочный вывод при старте ===
print("=== PAYMENT CONFIG ===")
print(f"Provider: {settings.payment_provider}")
print(f"Mode:     {settings.payment_mode}")
if settings.payment_provider.upper() == "TINKOFF":
    url = (
        settings.tinkoff_test_url
        if settings.payment_mode.upper() == "TEST"
        else settings.tinkoff_prod_url
    )
    print(f"Tinkoff URL: {url}")
elif settings.payment_provider.upper() == "YOOKASSA":
    print(f"YooKassa Return URL: {settings.yookassa_return_url}")
print("======================")
print("DEBUG PRICE:", settings.price_rub)


# === 4. Построение Telegram-приложения ===
def build_app() -> Application:
    app = Application.builder().token(settings.telegram_bot_token).build()

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset_consent))
    app.add_handler(CommandHandler("add_balance", add_balance))
    app.add_handler(CommandHandler("reset_balance", reset_balance))
    app.add_handler(CommandHandler("compensate", compensate))
    app.add_handler(CommandHandler("get_balance", get_balance))
    app.add_handler(CommandHandler("balance", handle_balance))
    app.add_handler(CommandHandler("reset_all", reset_all))

    # Колбэки
    app.add_handler(CallbackQueryHandler(handle_consent_yes, pattern=r"^consent_yes$"))
    app.add_handler(CallbackQueryHandler(show_instruction, pattern=r"^instruction$"))
    app.add_handler(CallbackQueryHandler(ensure_user, pattern=r"^ensure_user$"))
    app.add_handler(CallbackQueryHandler(check_balance_and_animate, pattern=r"^animate$"))
    app.add_handler(CallbackQueryHandler(open_balance, pattern=r"^balance$"))
    app.add_handler(CallbackQueryHandler(handle_topup, pattern=r"^topup:\d+$"))
    app.add_handler(CallbackQueryHandler(check_payment, pattern=r"^check_payment:"))
    app.add_handler(CallbackQueryHandler(do_animate, pattern=r"^do_animate$"))
    app.add_handler(CallbackQueryHandler(open_support, pattern=r"^support$"))

    # Фото и текст
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_prompt_text))

    # Назад в меню
    async def back_menu(update, context):
        q = update.callback_query
        await q.answer()
        user = await ensure_user(update)
        await show_main_menu(update, context, user)

    app.add_handler(CallbackQueryHandler(back_menu, pattern=r"^back_menu$"))
    return app


# === 5. Speed test ===
async def speed_test():
    t0 = time.time()
    for _ in range(5):
        await asyncio.sleep(0.1)
    print(f"⚡️ Event loop OK — {time.time() - t0:.2f} сек")


# === 6. Startup: инициализация сервисов ===
async def on_startup(app: Application):
    # ⚡️ Не ждём инициализацию базы — сразу запускаем в фоне
    asyncio.create_task(init_db())
    print("✅ DB init task started")

    if settings.gsheets_enable:
        print("✅ Google Sheets включены")
        # ⚡️ Запускаем фоновые записи без ожидания
        asyncio.create_task(gsheets.start_background_flush())
    else:
        print("⚠️ Google Sheets выключены")

    # ⚡️ Всё остальное — тоже в фоне, не блокирует интерфейс
    asyncio.create_task(sync_dashboard_once())
    asyncio.create_task(auto_loop())

    await speed_test()
    print("🚀 Startup complete")



# === 7. Корректное завершение ===
async def shutdown_tasks():
    print("🛑 Shutting down gracefully...")
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    print("✅ All tasks cancelled")


def setup_shutdown_signal():
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    else:
        # если уже есть — просто добавляем хендлеры
        pass

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown_tasks()))


# === 8. Точка входа ===
if __name__ == "__main__":
    app = build_app()
    app.post_init = on_startup
    setup_shutdown_signal()
    app.run_polling(drop_pending_updates=True)
