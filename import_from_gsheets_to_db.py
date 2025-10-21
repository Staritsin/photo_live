import os
import asyncio
import pandas as pd
import aiohttp
import json
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
SPREADSHEET_ID = os.getenv("GSHEETS_SPREADSHEET_ID")
CREDS_FILE = os.getenv("GSHEETS_CREDENTIALS_FILE", "./gcp_sa.json")

SCOPES = "https://www.googleapis.com/auth/spreadsheets.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"

TABLES = {
    "users": "users_data",
    "balances_raw": "balances_raw",
    "payments_raw": "payments_raw",
    "results_raw": "results_raw",
    "generations_raw": "generations_raw",
    "referrals_raw": "referrals_raw",
    "referrals_summary": "referrals_summary",
}

# ===================================================
#  AUTH
# ===================================================

async def get_access_token():
    with open(CREDS_FILE, "r", encoding="utf-8") as f:
        sa = json.load(f)
    import jwt, time
    now = int(time.time())
    payload = {
        "iss": sa["client_email"],
        "scope": SCOPES,
        "aud": TOKEN_URL,
        "iat": now,
        "exp": now + 3600,
    }
    assertion = jwt.encode(payload, sa["private_key"], algorithm="RS256")
    async with aiohttp.ClientSession() as session:
        async with session.post(TOKEN_URL, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion
        }) as r:
            token_data = await r.json()
            return token_data["access_token"]

# ===================================================
#  GOOGLE SHEETS
# ===================================================

async def load_sheet(sheet_name):
    token = await get_access_token()
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{sheet_name}!A1:Z"
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as r:
            j = await r.json()
            values = j.get("values", [])
            if len(values) < 2:
                return pd.DataFrame()
            headers, rows = values[0], values[1:]
            df = pd.DataFrame(rows, columns=headers)
            return df

# ===================================================
#  DATABASE
# ===================================================

CREATE_QUERIES = {
    "users_data": """
        CREATE TABLE IF NOT EXISTS users_data (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            username TEXT,
            full_name TEXT,
            registered_at TIMESTAMP
        );
    """,
    "balances_raw": """
        CREATE TABLE IF NOT EXISTS balances_raw (
            id SERIAL PRIMARY KEY,
            ts TIMESTAMP,
            user_id BIGINT,
            old_balance FLOAT,
            delta FLOAT,
            new_balance FLOAT,
            total_generations FLOAT,
            reason TEXT
        );
    """,
    "payments_raw": """
        CREATE TABLE IF NOT EXISTS payments_raw (
            id SERIAL PRIMARY KEY,
            ts TIMESTAMP,
            user_id BIGINT,
            amount_rub FLOAT,
            order_id TEXT,
            mode TEXT,
            payment_url TEXT
        );
    """,
    "results_raw": """
        CREATE TABLE IF NOT EXISTS results_raw (
            id SERIAL PRIMARY KEY,
            ts TIMESTAMP,
            user_id BIGINT,
            payment_id TEXT,
            status TEXT,
            amount_rub FLOAT
        );
    """,
    "generations_raw": """
        CREATE TABLE IF NOT EXISTS generations_raw (
            id SERIAL PRIMARY KEY,
            ts TIMESTAMP,
            user_id BIGINT,
            price_rub FLOAT,
            input_type TEXT,
            prompt TEXT,
            file_id TEXT
        );
    """,
    "referrals_raw": """
        CREATE TABLE IF NOT EXISTS referrals_raw (
            id SERIAL PRIMARY KEY,
            ts TIMESTAMP,
            referrer_id BIGINT,
            new_user_id BIGINT,
            status TEXT
        );
    """,
    "referrals_summary": """
        CREATE TABLE IF NOT EXISTS referrals_summary (
            id SERIAL PRIMARY KEY,
            ts TIMESTAMP,
            user_id BIGINT,
            invited_total INT,
            invited_paid INT,
            bonus_total INT
        );
    """,
}

# ===================================================
#  TABLE MANAGEMENT
# ===================================================

async def drop_old_tables(engine):
    async with engine.begin() as conn:
        for table in TABLES.values():
            await conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE;"))
            print(f"💥 Удалена старая таблица: {table}")

async def ensure_tables_exist(engine):
    async with engine.begin() as conn:
        for name, query in CREATE_QUERIES.items():
            await conn.execute(text(query))
            print(f"✅ Таблица {name} проверена/создана.")

async def clear_tables(engine):
    async with engine.begin() as conn:
        for table in TABLES.values():
            await conn.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY;"))
            print(f"🧹 {table} очищена.")
    print("✅ Все таблицы очищены.\n")

# ===================================================
#  NORMALIZATION
# ===================================================

def normalize_types(df: pd.DataFrame) -> pd.DataFrame:
    """Безопасное приведение типов перед вставкой в PostgreSQL."""
    int_cols = [
        "user_id", "referrer_id", "new_user_id",
        "invited_total", "invited_paid", "bonus_total"
    ]
    float_cols = [
        "amount_rub", "old_balance", "delta",
        "new_balance", "total_generations", "price_rub"
    ]

    for col in int_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    for col in float_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    df = df.replace({pd.NaT: "", "NaT": "", "None": "", None: ""})

    for col in df.columns:
        if col.lower() in ["ts", "registered_at", "created_at", "updated_at"]:
            df[col] = df[col].astype(str).replace("NaT", "").replace("None", "").fillna("").str.strip()

    return df

# ===================================================
#  IMPORT
# ===================================================

async def import_table(engine, sheet_name, df):
    table_name = TABLES[sheet_name]
    if df.empty:
        print(f"⚠️ {sheet_name}: пусто, пропускаем.")
        return 0

    df = normalize_types(df)

    async with engine.begin() as conn:
        cols = ", ".join(df.columns)
        placeholders = ", ".join([f":{c}" for c in df.columns])
        query = text(f"INSERT INTO {table_name} ({cols}) VALUES ({placeholders})")
        data = df.to_dict(orient="records")

        # 🔹 Преобразуем ts в datetime или ставим текущее время
        for row in data:
            if "ts" in row:
                if isinstance(row["ts"], str) and row["ts"].strip():
                    try:
                        row["ts"] = datetime.strptime(row["ts"], "%d.%m.%Y %H:%M:%S")
                    except Exception:
                        row["ts"] = datetime.utcnow()
                elif not row["ts"]:
                    row["ts"] = datetime.utcnow()

        await conn.execute(query, data)

    print(f"✅ Импортировано {len(df)} строк → {table_name}")
    return len(df)

# ===================================================
#  MAIN
# ===================================================

async def main():
    import time
    start_time = time.time()

    print("\n🚀 Импорт из Google Sheets → PostgreSQL\n")
    engine = create_async_engine(DATABASE_URL, echo=False)

    await ensure_tables_exist(engine)
    await clear_tables(engine)

    total_rows = 0
    for sheet_name in TABLES.keys():
        print(f"\n📥 Загружаем вкладку: {sheet_name}")
        df = await load_sheet(sheet_name)
        imported = await import_table(engine, sheet_name, df)
        total_rows += imported

    print(f"\n🎯 Импорт завершён! Всего строк: {total_rows}")
    print(f"⏱ Выполнено за {round(time.time() - start_time, 2)} сек.")
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
