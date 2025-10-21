# main.py — финальная стабильная версия
import time
start_time = time.perf_counter()

import os, json, asyncio, logging
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

# === 3. GCP credentials ===
gcp_env = os.getenv("GCP_SA_JSON")
if gcp_env:
    try:
        data = json.loads(gcp_env)
        with open("/app/gcp_sa.json", "w") as f:
            json.dump(data, f, indent=2)
        print("✅ GCP credentials file created.")
    except json.JSONDecodeError as e:
        print(f"❌ Ошибка JSON: {e}")
        print("⚠️ Проверь формат переменной GCP_SA_JSON в Railway (она должна быть валидным JSON).")
else:
    print("⚠️ GCP_SA_JSON не найден в окружении.")


# === 4. Основные импорты ===
from services.performance_logger import measure_time
from config import settings
from middlewares.safe_callbacks import SafeCallbackMiddleware
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
)
from db.database import init_db
from services import gsheets

# === 5. Автоимпорт при включённых Google Sheets ===
auto_loop = None
sync_dashboard_once = None
if os.getenv("GSHEETS_ENABLE", "0") == "1":
    from services.auto_sync_dashboard import auto_loop, sync_dashboard_once

# === 6. Handlers ===
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
from utils.metrics import wrap_all_handlers


# === 7. Отладочный вывод при старте ===
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


# === 8. Построение Telegram-приложения ===
def build_app() -> Application:
    app = Application.builder().token(settings.telegram_bot_token).build()

    # Глобальная защита от "Query is too old"
    async def safe_callback_answer(update, context):
        if update.callback_query:
            try:
                await update.callback_query.answer()
            except Exception:
                pass
        return True

    app.add_handler(MessageHandler(filters.ALL, safe_callback_answer), group=-1)

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
        try:
            await q.answer()
        except Exception:
            pass
        user = await ensure_user(update)
        await show_main_menu(update, context, user or update.effective_user)

    app.add_handler(CallbackQueryHandler(back_menu, pattern=r"^back_menu$"))

    # Глобальный тайминг
    wrap_all_handlers(app)
    return app


# === 9. Проверка скорости цикла ===
async def speed_test():
    t0 = time.time()
    for _ in range(5):
        await asyncio.sleep(0.1)
    print(f"⚡️ Event loop OK — {time.time() - t0:.2f} сек")


# === 10. Startup ===
async def on_startup(app: Application):
    asyncio.create_task(init_db())
    print("✅ DB init task started")

    if gsheets.ENABLED:
        print("✅ Google Sheets включены (GSHEETS_ENABLE=1)")
        asyncio.create_task(gsheets.start_background_flush())
        asyncio.create_task(auto_loop())
    else:
        print("⚠️ Google Sheets выключены (GSHEETS_ENABLE=0)")

    await speed_test()
    print("🚀 Startup complete")


# === 11. Webhook автообновление ===
async def auto_set_webhook(app: Application):
    RAILWAY_URL = os.getenv("RAILWAY_STATIC_URL") or "https://photo-live.up.railway.app"
    webhook_url = f"{RAILWAY_URL}/webhook"
    current = await app.bot.get_webhook_info()
    if current.url != webhook_url:
        await app.bot.set_webhook(url=webhook_url)
        print(f"✅ Webhook обновлён: {webhook_url}")
    else:
        print(f"✅ Webhook уже актуален: {webhook_url}")


# === 12. Основная функция ===
async def main():
    app = build_app()
    app.post_init = on_startup

    await auto_set_webhook(app)

    print("🚀 Bot starting via webhook...")
    await app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 8080)),
        url_path="webhook",
        webhook_url=f"{os.getenv('RAILWAY_STATIC_URL') or 'https://photo-live.up.railway.app'}/webhook",
    )


# === 13. Точка входа ===
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("🛑 Завершение работы бота...")
