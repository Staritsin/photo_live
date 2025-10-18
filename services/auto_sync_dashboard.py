# 🚀 Auto Dashboard (фикс финал)
import os
import asyncio
import gspread
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from collections import defaultdict

# === НАСТРОЙКИ ===
START_ROW = 3           # с какой строки писать данные
REFRESH_SECONDS = 300    # период обновления в секундах
SHEET_NAME = "Dashboard_Live"

# === Авторизация ===
load_dotenv()
GCP_CREDENTIALS_FILE = os.getenv("GSHEETS_CREDENTIALS_FILE", "gcp_sa.json")
SPREADSHEET_ID = os.getenv("GSHEETS_SPREADSHEET_ID")

gc = gspread.service_account(filename=GCP_CREDENTIALS_FILE)
sh = gc.open_by_key(SPREADSHEET_ID)

MOSCOW_TZ = timezone(timedelta(hours=3))

# === Цвета ===
COLOR_GREEN = {"red": 0.8, "green": 1, "blue": 0.8}
COLOR_RED = {"red": 1, "green": 0.8, "blue": 0.8}
COLOR_GRAY = {"red": 0.96, "green": 0.96, "blue": 0.96}


def _safe_float(v):
    try:
        return float(v)
    except Exception:
        return 0.0

def _safe_int(v):
    try:
        return int(float(v))
    except Exception:
        return 0

def _str(v):
    return "" if v is None else str(v)


def init_dashboard_headers(ws):
    """Создание заголовков и формул, если их нет"""
    values = ws.get("A1:P2")
    if values and len(values) >= 2:
        return  # уже есть — не трогаем

    print("🧱 Создаём заголовки и формулы...")

    headers = [
        "⏰ Время обновления",
        "ID пользователя",
        "Имя пользователя",
        "Конверсия user→заказ",
        "Кол-во заказов",
        "Сумма заказов ₽",
        "Кол-во оплат",
        "Сумма оплат ₽",
        "Конверсия заказ→оплата",
        "Конверсия user→оплата",
        "Баланс генераций",
        "Бонусные генерации",
        "Сделано генераций",
        "Пригласил пользователей",
        "Из них оплатили",
        "Дата последней оплаты",
    ]
    ws.update("A1", [headers])

    formulas_row2 = [[
        "🕒 " + datetime.now(MOSCOW_TZ).strftime("%d.%m.%Y %H:%M"),
        "=СЧЁТЗ(B3:B100000)",
        "",
        "=СРЗНАЧ(D3:D100000)",
        "=СУММ(E3:E100000)",
        "=СУММ(F3:F100000)",
        "=СУММ(G3:G100000)",
        "=СУММ(H3:H100000)",
        "=СРЗНАЧ(I3:I100000)",
        "=СРЗНАЧ(J3:J100000)",
        "=СУММ(K3:K100000)",
        "=СУММ(L3:L100000)",
        "=СУММ(M3:M100000)",
        "=СУММ(N3:N100000)",
        "=СУММ(O3:O100000)",
        "",
    ]]
    ws.update("A2", formulas_row2)


def get_or_create_dashboard():
    try:
        ws = sh.worksheet(SHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=SHEET_NAME, rows=20000, cols=30)
        print(f"🆕 Создан новый лист {SHEET_NAME}")

    init_dashboard_headers(ws)
    return ws


async def sync_dashboard_once():
    ws = get_or_create_dashboard()

    # === Данные из других листов ===
    users = sh.worksheet("users").get_all_records()
    pays_raw = sh.worksheet("payments_raw").get_all_records()
    results_raw = sh.worksheet("results_raw").get_all_records()
    balances_raw = sh.worksheet("balances_raw").get_all_records()
    gens_raw = sh.worksheet("generations_raw").get_all_records()
    ref_sum = sh.worksheet("referrals_summary").get_all_records()

    # === Расчёты ===
    orders_count = defaultdict(int)
    orders_sum = defaultdict(float)
    for p in pays_raw:
        uid = _str(p.get("user_id", "")).strip()
        if uid:
            orders_count[uid] += 1
            orders_sum[uid] += _safe_float(p.get("amount_rub", 0))

    pays_count = defaultdict(int)
    pays_sum = defaultdict(float)
    last_pay_ts = defaultdict(str)
    for r in results_raw:
        uid = _str(r.get("user_id", "")).strip()
        if uid and _str(r.get("status", "")).upper() in ("CONFIRMED", "AUTHORIZED", "PAID"):
            pays_count[uid] += 1
            pays_sum[uid] += _safe_float(r.get("amount_rub", 0))
            last_pay_ts[uid] = _str(r.get("ts", ""))

    last_new_balance = defaultdict(int)
    bonus_total = defaultdict(int)
    for b in balances_raw:
        uid = _str(b.get("user_id", "")).strip()
        if uid:
            last_new_balance[uid] = _safe_int(b.get("new_balance", 0))
            delta = _safe_int(b.get("delta", 0))
            reason = _str(b.get("reason", "")).lower()
            if delta > 0 and ("bonus" in reason or "referral" in reason):
                bonus_total[uid] += delta

    gens_count = defaultdict(int)
    for g in gens_raw:
        uid = _str(g.get("user_id", "")).strip()
        if uid:
            gens_count[uid] += 1

    invited_total = defaultdict(int)
    invited_paid = defaultdict(int)
    for s in ref_sum:
        uid = _str(s.get("user_id", "")).strip()
        if uid:
            invited_total[uid] = _safe_int(s.get("invited_total", 0))
            invited_paid[uid] = _safe_int(s.get("invited_paid", 0))

    now_str = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S")

    rows, fmt = [], []
    for idx, u in enumerate(users, start=START_ROW):
        uid = _str(u.get("user_id", "")).strip()
        if not uid:
            continue
        uname = _str(u.get("username", ""))

        e_orders = orders_count[uid]
        f_sum_orders = round(orders_sum[uid], 2)
        g_pays = pays_count[uid]
        h_sum_pays = round(pays_sum[uid], 2)
        j_balance = last_new_balance[uid]
        k_bonus = bonus_total[uid]
        l_gens = gens_count[uid]
        m_inv = invited_total[uid]
        n_paid = invited_paid[uid]
        o_last = last_pay_ts[uid]

        formula_d = f"=ЕСЛИ(E{idx}>0;1;0)"
        formula_i = f"=ЕСЛИ(E{idx}>0;G{idx}/E{idx};\"\")"
        formula_j = f"=ЕСЛИ(G{idx}>0;1;0)"

        rows.append([
            now_str, uid, uname, formula_d,
            e_orders, f_sum_orders, g_pays, h_sum_pays,
            formula_i, formula_j,
            j_balance, k_bonus, l_gens,
            m_inv, n_paid, o_last
        ])

        color = COLOR_GRAY
        if g_pays > 0 and h_sum_pays > 0:
            color = COLOR_GREEN
        elif e_orders > 0 and g_pays == 0:
            color = COLOR_RED
        fmt.append({"range": f"A{idx}:P{idx}", "format": {"backgroundColor": color}})

    ws.update(f"A{START_ROW}", rows, value_input_option="USER_ENTERED")
    if fmt:
        ws.batch_format(fmt)

    print(f"✅ Dashboard обновлён: {len(rows)} строк ({now_str})")


async def auto_loop():
    print(f"🚀 Auto Dashboard запущен (каждые {REFRESH_SECONDS // 60} мин)")
    while True:
        try:
            await asyncio.to_thread(sync_dashboard_once)
        except Exception as e:
            print(f"❌ Ошибка Dashboard: {e}")
        await asyncio.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    asyncio.run(auto_loop())
