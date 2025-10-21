#берет данные из гугл таблицы дашборда и обновляет юзеров в бд


import asyncio
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials
from sqlalchemy import text
from db.database import engine

# 🔹 Настройки напрямую в коде (без .env)
SPREADSHEET_ID = "12X5nOZROvpFZwDbO0Q4Te_8jqz3H-oB03yUiX878oe8"
RANGE_NAME = "Dashboard_Live!B3:Q1000"  # диапазон с данными
CREDENTIALS_FILE = "./gcp_sa.json"

async def sync_dashboard_to_db():
    # === Подключение к Google Sheets ===
    creds = Credentials.from_service_account_file(
        CREDENTIALS_FILE,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    service = build("sheets", "v4", credentials=creds)
    sheet = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=RANGE_NAME
    ).execute()

    rows = sheet.get("values", [])
    if not rows:
        print("⚠️ Нет данных в Dashboard.")
        return

    async with engine.begin() as conn:
        updated = 0
        for row in rows:
            if len(row) < 11:
                continue

            try:
                user_id = int(row[0])  # ID пользователя
                gens = int(row[5] or 0)  # Кол-во генераций
                paid = float(row[7] or 0)  # Сумма оплат
                balance = int(row[10] or 0)  # Баланс генераций
                refs = int(row[13] or 0)  # Приглашенные
            except Exception:
                continue

            await conn.execute(text("""
                UPDATE users
                SET total_generations = :gens,
                    total_spent = :paid,
                    balance = :balance,
                    referrals_count = :refs
                WHERE id = :user_id
            """), dict(
                gens=gens,
                paid=paid,
                balance=balance,
                refs=refs,
                user_id=user_id
            ))
            updated += 1

        print(f"✅ Синхронизировано {updated} пользователей с Dashboard")

if __name__ == "__main__":
    asyncio.run(sync_dashboard_to_db())
