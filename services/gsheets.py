import os
import json
import time
import aiohttp
import asyncio
import typing as t
import jwt
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv


# === Загрузка .env ===
load_dotenv()

# === Конфигурация ===
ENABLED = os.getenv("GSHEETS_ENABLE", "0") == "1"
SPREADSHEET_ID = os.getenv("GSHEETS_SPREADSHEET_ID")
CREDS_FILE = os.getenv("GSHEETS_CREDENTIALS_FILE", "./gcp_sa.json")

SCOPES = "https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/drive"
TOKEN_URL = "https://oauth2.googleapis.com/token"

# Московское время
MOSCOW_TZ = timezone(timedelta(hours=3))


def now_iso() -> str:
    """Возвращает строку времени Москвы в формате `dd.mm.YYYY HH:MM:SS`, принудительно как текст."""
    return datetime.now(MOSCOW_TZ).strftime("'%d.%m.%Y %H:%M:%S")


# === Кэш токена ===
_token_cache: dict[str, t.Any] = {"access_token": None, "expires_at": 0}
_token_lock = asyncio.Lock()

# Очередь для батчевой отправки
_queue_rows: dict[str, list[list[t.Any]]] = defaultdict(list)
_queue_headers: dict[str, list[str] | None] = {}
_queue_lock = asyncio.Lock()
_flush_task: asyncio.Task | None = None
_FLUSH_INTERVAL = 5  # секунд


def _safe_create_task(coro: t.Coroutine) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        loop.create_task(coro)


async def start_background_flush() -> None:
    if not ENABLED:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    global _flush_task
    if _flush_task is None or _flush_task.done():
        _flush_task = loop.create_task(_flush_loop())


# === Авторизация ===
def _load_sa() -> dict:
    if not CREDS_FILE or not os.path.exists(CREDS_FILE):
        raise RuntimeError("GSHEETS_CREDENTIALS_FILE not found.")
    with open(CREDS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


async def _get_access_token() -> str:
    now = int(time.time())
    if _token_cache["access_token"] and _token_cache["expires_at"] - 60 > now:
        return _token_cache["access_token"]

    async with _token_lock:
        now = int(time.time())
        if _token_cache["access_token"] and _token_cache["expires_at"] - 60 > now:
            return _token_cache["access_token"]

        sa = _load_sa()
        iat = int(time.time())
        exp = iat + 3600
        assertion = jwt.encode(
            {
                "iss": sa["client_email"],
                "scope": SCOPES,
                "aud": TOKEN_URL,
                "iat": iat,
                "exp": exp,
            },
            sa["private_key"],
            algorithm="RS256",
        )

        data = {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}

        async with aiohttp.ClientSession() as session:
            async with session.post(TOKEN_URL, data=data, timeout=30) as resp:
                j = await resp.json()
                if resp.status != 200:
                    raise RuntimeError(f"OAuth error {resp.status}: {j}")
                _token_cache["access_token"] = j["access_token"]
                _token_cache["expires_at"] = now + int(j.get("expires_in", 3600))
                return _token_cache["access_token"]


# === Универсальные функции ===
async def _request_json(method: str, url: str, *, params=None, json_body=None):
    token = await _get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.request(method, url, params=params, json=json_body, timeout=30) as resp:
            data = await resp.json()
            print(f"📡 SHEETS API {method} {url} -> {resp.status}")
            if resp.status >= 400:
                raise RuntimeError(f"Sheets API {resp.status}: {data}")
            return data


async def _get_sheet_titles() -> set[str]:
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}"
    res = await _request_json("GET", url, params={"fields": "sheets(properties(title))"})
    return {s["properties"]["title"] for s in res.get("sheets", [])}


async def _ensure_sheet_exists(title: str, headers: list[str] | None = None):
    titles = await _get_sheet_titles()
    if title in titles:
        return
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}:batchUpdate"
    body = {"requests": [{"addSheet": {"properties": {"title": title}}}]}
    await _request_json("POST", url, json_body=body)
    if headers:
        await batch_update_values_async([
            {"range": f"{title}!A1:{chr(64 + len(headers))}1", "values": [headers]}
        ])


async def _enqueue_row(sheet_name: str, headers: list[str] | None, row: list[t.Any]) -> None:
    async with _queue_lock:
        if headers is not None:
            _queue_headers.setdefault(sheet_name, headers)
        _queue_rows[sheet_name].append(row)


def queue_append(sheet_name: str, headers: list[str] | None, row: list[t.Any]) -> None:
    if not ENABLED or not SPREADSHEET_ID:
        return
    _safe_create_task(_enqueue_row(sheet_name, headers, row))


async def _flush_once() -> None:
    async with _queue_lock:
        if not _queue_rows:
            return
        pending = {
            sheet: (_queue_headers.get(sheet), rows[:])
            for sheet, rows in _queue_rows.items()
        }
        _queue_rows.clear()

    for sheet, (headers, rows) in pending.items():
        await append_rows_async(rows=rows, sheet_name=sheet, headers=headers, use_queue=False)


async def _flush_loop() -> None:
    while True:
        await asyncio.sleep(_FLUSH_INTERVAL)
        try:
            await _flush_once()
        except Exception as exc:
            print(f"⚠️ GSHEETS queue flush error: {exc}")


# === Основная запись в Google Sheets ===
async def append_rows_async(
    rows: list[list[t.Any]],
    sheet_name: str,
    headers: list[str] | None = None,
    *,
    use_queue: bool = False,
):
    if not ENABLED or not SPREADSHEET_ID:
        return {"disabled": True}
    if use_queue:
        for row in rows:
            queue_append(sheet_name, headers, row)
        return {"queued": len(rows)}

    await _ensure_sheet_exists(sheet_name, headers=headers)
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{sheet_name}!A3:append"
    params = {"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"}
    body = {"values": rows}
    print(f"📊 APPEND_ROWS: {sheet_name} ({len(rows)} строк)")
    return await _request_json("POST", url, params=params, json_body=body)


async def batch_update_values_async(data: list[dict]):
    if not ENABLED or not SPREADSHEET_ID:
        return {"disabled": True}
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values:batchUpdate"
    body = {"valueInputOption": "USER_ENTERED", "data": data, "includeValuesInResponse": False}
    return await _request_json("POST", url, json_body=body)


# === Логи событий ===
async def log_user_event(
    user_id: int,
    username: str | None,
    event: str,
    meta: dict | None = None,
    new_balance: int | None = None,
    free_trial_used: bool | None = None,
):
    queue_append(
        "users_raw",
        ["ts", "user_id", "username", "event", "meta"],
        [
            now_iso(),
            user_id,
            (username or ""),
            event,
            ("" if not meta else json.dumps(meta, ensure_ascii=False)),
        ],
    )


async def log_payment_attempt(user_id: int, username: str | None, amount_rub: float, order_id: str, mode: str, url: str):
    queue_append(
        "payments_raw",
        ["ts", "user_id", "amount_rub", "order_id", "mode", "payment_url"],
        [now_iso(), user_id, amount_rub, order_id, mode, url],
    )


async def log_payment_result(user_id: int, username: str | None, payment_id: str, status: str, amount_rub: float | None):
    queue_append(
        "results_raw",
        ["ts", "user_id", "payment_id", "status", "amount_rub"],
        [now_iso(), user_id, payment_id, status, ("" if amount_rub is None else amount_rub)],
    )


async def log_generation(user_id: int, username: str, price_rub: float, input_type: str, prompt: str | None, file_id: str | None):
    queue_append(
        "generations_raw",
        ["ts", "user_id", "price_rub", "input_type", "prompt", "file_id"],
        [now_iso(), user_id, price_rub, input_type, (prompt or ""), (file_id or "")],
    )


async def log_balance_change(
    user_id: int,
    old_balance: float,
    delta: float,
    new_balance: float,
    reason: str,
    referral_bonus: int = 0,
):
    total_generations = new_balance + referral_bonus
    queue_append(
        "balances_raw",
        [
            "ts",
            "user_id",
            "old_balance",
            "delta",
            "new_balance",
            "total_generations",
            "reason",
        ],
        [
            now_iso(),
            user_id,
            old_balance,
            delta,
            new_balance,
            total_generations,
            reason,
        ],
    )


# === Рефералы ===
async def log_referral(referrer_id: int, username: str | None, new_user_id: int, status: str):
    queue_append(
        "referrals_raw",
        ["ts", "referrer_id", "new_user_id", "status"],
        [now_iso(), referrer_id, new_user_id, status],
    )


async def update_referrals_summary(
    user_id: int,
    invited_total: int,
    invited_paid: int,
    bonus_total: int,
):
    if invited_total == 0 and invited_paid == 0:
        return
    queue_append(
        "referrals_summary",
        ["ts", "user_id", "invited_total", "invited_paid", "bonus_total"],
        [now_iso(), user_id, invited_total, invited_paid, bonus_total],
    )
    print(f"📊 referrals_summary обновлён для user_id={user_id}")

# === USERS UNIQUE ===
async def log_unique_user(user_id: int, username: str | None, full_name: str | None):
    """Добавляет юзера во вкладку 'users', если его там ещё нет."""
    if not ENABLED or not SPREADSHEET_ID:
        return

    try:
        # 1️⃣ Убедимся, что вкладка существует
        await _ensure_sheet_exists("users", headers=["user_id", "username", "full_name", "registered_at"])

        # 2️⃣ Получаем существующие user_id (первые 10k строк)
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/users!A2:A10000"
        data = await _request_json("GET", url)
        existing_ids = {str(r[0]) for r in data.get("values", []) if r}

        if str(user_id) in existing_ids:
            print(f"👤 User {user_id} уже есть в 'users' — пропускаем")
            return

        # 3️⃣ Добавляем нового пользователя
        queue_append(
            "users",
            ["user_id", "username", "full_name", "registered_at"],
            [user_id, username or "", full_name or "", now_iso()],
        )
        print(f"✅ Добавлен новый пользователь в 'users': {username or user_id}")

    except Exception as e:
        print(f"⚠️ Ошибка log_unique_user: {e}")
