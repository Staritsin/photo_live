# scripts/init_sheets.py
# =========================================================
# ВСПОМОГАТЕЛЬНЫЙ СКРИПТ ДЛЯ РАБОТЫ С GOOGLE SHEETS
#
# ⚡ Что делает:
#   - Создаёт нужные листы (если их нет) 
#   - Проставляет заголовки
#   - Добавляет тестовую строку для проверки записи
#
# 🚀 Как запускать:
#   1. Убедись, что активировано venv:
#        source .venv/bin/activate
#   2. Запусти скрипт:
#        python scripts/init_sheets.py
#
# 📌 Важно:
#   - Этот скрипт используется только для инициализации таблиц
#   - В рабочем боте НЕ используется
#   - Можно запускать повторно, чтобы добавить новые листы
# =========================================================

import sys
import os
import asyncio
from datetime import datetime

# добавляем корень проекта в sys.path
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from services import gsheets

# 👇 Твой реальный Telegram user_id
TEST_USER_ID = 2101512357
TEST_USERNAME = "staritsin_a"

async def init_sheets():
    now = datetime.now().isoformat(timespec="seconds")

    tests = [
        (
            "users_raw",
            ["ts","user_id","username","event","meta"],
            [now, TEST_USER_ID, TEST_USERNAME, "init", "{}"]
        ),
        (
            "payments_raw",
            ["ts","user_id","amount_rub","order_id","mode","payment_url"],
            [now, TEST_USER_ID, 100, "order123", "TEST", "http://url"]
        ),
        (
            "results_raw",
            ["ts","user_id","payment_id","status","amount_rub"],
            [now, TEST_USER_ID, "pid123", "registered", 100]
        ),
        (
            "generations_raw",
            ["ts","user_id","price_rub","input_type","prompt","file_id"],
            [now, TEST_USER_ID, 85, "text", "hello", "file123"]
        ),
        (
            "balances_raw",
            ["ts","user_id","old_balance","delta","new_balance","reason"],
            [now, TEST_USER_ID, 0, 5, 5, "init_bonus"]
        ),
        (
            "Referrals",
            ["ts","referrer_id","new_user_id","status"],
            [now, TEST_USER_ID, 999999, "registered"]
        ),
        (
            "Referrals_Summary",
            ["ts","user_id","invited_total","invited_paid","bonus_total"],
            [now, TEST_USER_ID, 1, 0, 0]
        ),
        (
            "Logs",
            ["ts","msg"],
            [now, "init OK"]
        ),
    ]

    for sheet, headers, row in tests:
        print(f"👉 Проверяем лист {sheet}")
        asyncio.create_task(gsheets.append_rows_async(
            sheet_name=sheet,
            headers=headers,
            rows=[row]
        ))
        print(f"✅ {sheet} готов + запись {row}")

if __name__ == "__main__":
    asyncio.run(init_sheets())
