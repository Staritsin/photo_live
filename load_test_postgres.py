import asyncio
import asyncpg
import ssl
import time

DB_URL = "postgresql://postgres:PWBWRQTQbPdXBFduRbtoYhBcAaMlJtyQ@shinkansen.proxy.rlwy.net:51443/railway"
CONNECTIONS = 200  # имитация 200 пользователей
POOL_SIZE = 20     # реально открытых подключений

async def worker(i, pool):
    async with pool.acquire() as conn:
        await conn.execute("SELECT 1;")
        return i

async def main():
    print(f"🚀 Starting load test ({CONNECTIONS} users, pool={POOL_SIZE})...")
    start = time.perf_counter()

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE  # <–– вот это добавили

    pool = await asyncpg.create_pool(DB_URL, min_size=5, max_size=POOL_SIZE, ssl=ssl_ctx)

    tasks = [asyncio.create_task(worker(i, pool)) for i in range(CONNECTIONS)]
    await asyncio.gather(*tasks)

    await pool.close()

    total_time = time.perf_counter() - start
    print(f"✅ Done in {total_time:.2f}s for {CONNECTIONS} users")
    print(f"⚡ Avg per user: {total_time / CONNECTIONS:.3f}s")

if __name__ == "__main__":
    asyncio.run(main())
