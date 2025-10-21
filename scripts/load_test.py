# 🚀 scripts/load_test.py — нагрузочный тест (анализ скорости + 10 пользователей)
import asyncio
import time
import os
import csv
import statistics
from types import SimpleNamespace
from handlers.start import start

# === Кол-во пользователей для теста ===
USERS_COUNT = 300
LOG_PATH = "logs/db_timings.csv"


# === Поддельный бот (симуляция отправки сообщений) ===
class FakeBot:
    async def send_message(self, chat_id, text, **kwargs):
        print(f"💬 [FAKE BOT] send_message → {chat_id}: {text[:50]}...")
        await asyncio.sleep(0.05)
        return SimpleNamespace(message_id=1)

    async def send_video(self, chat_id, video, caption=None, **kwargs):
        print(f"🎬 [FAKE BOT] send_video → {chat_id}: {caption[:40]}...")
        await asyncio.sleep(0.05)
        return SimpleNamespace(video=SimpleNamespace(file_id="fake_file_id"))


# === Один пользователь ===
SEMAPHORE = asyncio.Semaphore(30)  # максимум 30 одновременно

async def fake_user(user_id):
    async with SEMAPHORE:
        await asyncio.sleep(user_id * 0.05)

        start_time = time.perf_counter()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=user_id, username=f"user_{user_id}", full_name=f"User {user_id}"),
            effective_chat=SimpleNamespace(id=user_id),
            message=SimpleNamespace(chat_id=user_id, text="/start"),
        )
        context = SimpleNamespace(bot=FakeBot(), args=[], user_data={})

        try:
            await start(update, context)
            elapsed = time.perf_counter() - start_time
            print(f"✅ User {user_id} done ({elapsed:.2f}s)")
            return elapsed
        except Exception as e:
            print(f"❌ User {user_id} error: {e}")
            return None


# === Анализ timings.csv ===
# === Анализ timings.csv ===
# === Анализ timings.csv ===
async def analyze_timings():
    import statistics
    if not os.path.exists(LOG_PATH):
        print("⚠️ Лог timings.csv не найден")
        return

    data = {}
    with open(LOG_PATH, newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 4:
                continue

            # Пример: [timestamp, module, func, duration]
            try:
                module, func, duration = row[1], row[2], float(row[3])
            except Exception:
                continue

            key = f"{module}.{func}"
            data.setdefault(key, []).append(duration)

    print("\n=== 🧠 DB TIMING SUMMARY ===")
    if not data:
        print("❌ Нет данных для анализа.")
        return

    for key, values in data.items():
        avg = statistics.mean(values)
        p95 = sorted(values)[int(len(values) * 0.95) - 1] if len(values) >= 2 else avg
        print(f"{key:<45} avg: {avg:>6.3f}s   p95: {p95:>6.3f}s   count: {len(values)}")

    if "services.billing_core.upsert_user" in data:
        total_avg = statistics.mean(data["services.billing_core.upsert_user"])
        print(f"\n⚡ TOTAL avg per upsert_user: {total_avg:.3f}s")
    print("=============================\n")


# === Главная функция ===
async def main():
    print(f"✅ Using Postgres (Production)")
    print(f"🚀 Starting load test ({USERS_COUNT} users)...")

    # очищаем старые логи
    if os.path.exists(LOG_PATH):
        os.remove(LOG_PATH)
        print("🧹 Старый лог удалён")

    t0 = time.perf_counter()
    results = await asyncio.gather(*(fake_user(i) for i in range(USERS_COUNT)))
    total_time = time.perf_counter() - t0

    valid_times = [r for r in results if r is not None]
    avg_time = sum(valid_times) / len(valid_times) if valid_times else 0

    print("\n=== 📊 LOAD TEST RESULTS ===")
    print(f"✅ Success: {len(valid_times)}/{USERS_COUNT}")
    print(f"⚡️ Avg time per user: {avg_time:.2f}s")
    print(f"🕒 Total time: {total_time:.2f}s")
    print("=============================")

    # после теста — анализируем timings
    await analyze_timings()


if __name__ == "__main__":
    asyncio.run(main())
