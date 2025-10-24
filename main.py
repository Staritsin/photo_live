# main.py — финальная стабильная версия
import time
start_time = time.perf_counter()

import os, json, asyncio, signal, logging
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

gcp_env = os.getenv("GCP_SA_JSON")
if gcp_env:
    try:
        # Если переменная окружения содержит JSON в виде строки — декодируем
        data = json.loads(gcp_env)
        with open("/app/gcp_sa.json", "w") as f:
            json.dump(data, f, indent=2)
        print("✅ GCP credentials file created.")
    except json.JSONDecodeError as e:
        print(f"❌ Ошибка JSON: {e}")
        print("⚠️ Проверь формат переменной GCP_SA_JSON в Railway (она должна быть валидным JSON).")
else:
    print("⚠️ GCP_SA_JSON не найден в окружении.")


from services.performance_logger import measure_time
from config import settings
from middlewares.safe_callbacks import SafeCallbackMiddleware


from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
)
from db.database import init_db
from services import gsheets

auto_loop = None
sync_dashboard_once = None
if os.getenv("GSHEETS_ENABLE", "0") == "1":
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

    from utils.metrics import wrap_all_handlers
    wrap_all_handlers(app)
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

    # === Google Sheets ===
    if gsheets.ENABLED:
        print("✅ Google Sheets включены (GSHEETS_ENABLE=1)")
        asyncio.create_task(gsheets.start_background_flush())
    else:
        print("⚠️ Google Sheets выключены (GSHEETS_ENABLE=0)")

    # === Dashboard ===
    if gsheets.ENABLED:
        asyncio.create_task(auto_loop())
        print("✅ Dashboard включен (GSHEETS_ENABLE=1)")
    else:
        print("⚠️ Dashboard выключен (GSHEETS_ENABLE=0)")

    # 🚀 Убираем задержки от debug-loop
    asyncio.get_event_loop().set_debug(False)

    await speed_test()
    print("🚀 Startup complete")

    # === Сводка статусов при старте ===
    print("\n================= SYSTEM STATUS =================")
    print(f"🧩 Database...........: {'PostgreSQL (Railway)' if settings.use_postgres else 'SQLite (Local Mode)'}")
    print(f"📊 Google Sheets......: {'ON ✅ (GSHEETS_ENABLE=1)' if gsheets.ENABLED else 'OFF ⚠️ (GSHEETS_ENABLE=0)'}")
    print(f"📈 Dashboard..........: {'ON ✅' if gsheets.ENABLED else 'OFF ⚠️'}")
    print(f"💳 Payments...........: {settings.payment_provider} ({settings.payment_mode})")
    print(f"💰 Price per gen......: {settings.price_rub} ₽")
    print(f"🎁 Free trial.........: {'1 генерация' if getattr(settings, 'free_trial_gens', 1) else '—'}")
    print(f"🎉 Bonus policy.......: +{getattr(settings, 'bonus_per_friend', 1)} за друга / +{getattr(settings, 'bonus_per_10', 2)} за 10 оплат")
    print(f"📦 Packs available....: {', '.join([f'{p}₽' for p in settings.packs])}")
    print(f"⚙️  Engine Mode........: {'Production 🚀' if settings.payment_mode.upper() == 'PROD' else 'Test 🧪'}")
    print("=================================================\n")



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

async def auto_set_webhook(app: Application):
    RAILWAY_URL = os.getenv("RAILWAY_STATIC_URL") or "https://photo-live.up.railway.app"
    webhook_url = f"{RAILWAY_URL}/webhook"

    current = await app.bot.get_webhook_info()
    if current.url != webhook_url:
        await app.bot.set_webhook(url=webhook_url)
        print(f"✅ Webhook обновлён: {webhook_url}")
    else:
        print(f"✅ Webhook уже актуален: {webhook_url}")



# === 8. Точка входа (финальная, безопасная для Railway) ===
from fastapi import FastAPI, Request
import uvicorn
from telegram import Update as TgUpdate
from threading import Thread

fastapi_app = FastAPI()
ptb_app: Application | None = None  # PTB-приложение (глобальная ссылка)

@fastapi_app.post("/webhook")
async def webhook_handler(req: Request):
    try:
        # Telegram лог
        print("📩 Webhook hit!")

        # ✅ всегда отдаём 200 OK (чтобы Telegram не считал это ошибкой)
        data = await req.json()
        if ptb_app and ptb_app.bot:
            update = TgUpdate.de_json(data, ptb_app.bot)
            await ptb_app.update_queue.put(update)
        return {"ok": True}
    except Exception as e:
        print("⚠️ Webhook error:", e)
        return {"ok": True}  # Telegram получит 200 OK даже при ошибке



@fastapi_app.get("/")
async def root():
    return {"status": "ok", "message": "Bot is running"}


async def main():
    global ptb_app
    ptb_app = build_app()
    ptb_app.post_init = on_startup

    await ptb_app.initialize()
    await ptb_app.start()
    RAILWAY_URL = os.getenv("BASE_PUBLIC_URL") or "https://photo-live.up.railway.app"
    await ptb_app.bot.set_webhook(url=f"{RAILWAY_URL}/webhook")
    print(f"✅ Webhook установлен: {RAILWAY_URL}/webhook")


    # 🚀 тут не uvicorn.run(), а просто оставляем бот активным
    await asyncio.Event().wait()  # держим loop живым


if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()

    # 💡 запускаем uvicorn в отдельном потоке
    def run_server():
        uvicorn.run("main:fastapi_app", host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

    Thread(target=run_server, daemon=True).start()

    asyncio.run(main())


