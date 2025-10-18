# scripts/create_tables.py
import asyncio
from db.database import init_db

async def main():
    print("▶️ Создаём таблицы в базе...")
    await init_db()
    print("✅ Таблицы успешно созданы!")

if __name__ == "__main__":
    asyncio.run(main())
