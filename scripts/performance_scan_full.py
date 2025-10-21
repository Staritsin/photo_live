# scripts/performance_scan_full.py
import asyncio, time, inspect, importlib, os, pkgutil, csv
from datetime import datetime, timedelta
from types import ModuleType
from functools import wraps
from tabulate import tabulate  # pip install tabulate
from collections import Counter

TARGET_PACKAGES = ["handlers", "services", "db", "utils"]
THRESHOLD = 0.2  # секунды
REPORT_FILE = "performance_report.csv"

results = []

def time_it(func):
    if asyncio.iscoroutinefunction(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                d = time.perf_counter() - start
                if d > THRESHOLD:
                    results.append((func.__module__, func.__name__, round(d, 3)))
        return wrapper
    else:
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                d = time.perf_counter() - start
                if d > THRESHOLD:
                    results.append((func.__module__, func.__name__, round(d, 3)))
        return wrapper

def patch_module_functions(module: ModuleType):
    for name, obj in inspect.getmembers(module):
        if inspect.isfunction(obj) or inspect.iscoroutinefunction(obj):
            setattr(module, name, time_it(obj))

def scan_and_patch():
    for pkg_name in TARGET_PACKAGES:
        try:
            pkg = importlib.import_module(pkg_name)
        except ModuleNotFoundError:
            continue
        pkg_path = os.path.dirname(pkg.__file__)
        for _, mod_name, _ in pkgutil.walk_packages([pkg_path], prefix=f"{pkg_name}."):
            try:
                mod = importlib.import_module(mod_name)
                patch_module_functions(mod)
            except Exception as e:
                print(f"⚠️ Ошибка при импорте {mod_name}: {e}")

async def run_full_scan():
    print("🚀 Запуск полного сканирования...")
    for pkg_name in TARGET_PACKAGES:
        try:
            pkg = importlib.import_module(pkg_name)
        except ModuleNotFoundError:
            continue
        pkg_path = os.path.dirname(pkg.__file__)
        for _, mod_name, _ in pkgutil.walk_packages([pkg_path], prefix=f"{pkg_name}."):
            try:
                mod = importlib.import_module(mod_name)
                for name, obj in inspect.getmembers(mod):
                    if inspect.isfunction(obj):
                        try: obj()
                        except: pass
                    elif inspect.iscoroutinefunction(obj):
                        try: await obj()
                        except: pass
            except Exception as e:
                print(f"⚠️ Ошибка при сканировании {mod_name}: {e}")

def save_to_csv():
    if not results:
        print("✅ Все функции работают быстро — сохранять нечего.")
        return
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_file = not os.path.exists(REPORT_FILE)
    with open(REPORT_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["timestamp", "module", "function", "duration_sec"])
        for mod, func, dur in sorted(results, key=lambda x: x[2], reverse=True):
            w.writerow([ts, mod, func, dur])
    print(f"📊 Результаты сохранены в {REPORT_FILE}")

def print_results():
    if not results:
        print("✅ Все функции работают быстро!")
        return
    table = sorted(results, key=lambda x: x[2], reverse=True)
    print("\n=== ⚠️ Медленные функции ===")
    print(tabulate(table, headers=["Модуль", "Функция", "Время (сек)"], tablefmt="fancy_grid"))

def analyze_top_functions():
    if not os.path.exists(REPORT_FILE):
        print("⏳ Нет предыдущих данных для анализа.")
        return
    cutoff = datetime.now() - timedelta(days=7)
    counts = Counter()
    with open(REPORT_FILE, encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                ts = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
                if ts >= cutoff:
                    key = f"{row['module']}.{row['function']}"
                    counts[key] += 1
            except: pass
    if not counts:
        print("📅 За последнюю неделю тормозов не было! 🔥")
        return
    top5 = counts.most_common(5)
    print("\n=== 🧠 ТОП-5 функций, чаще всего тормозивших за 7 дней ===")
    print(tabulate(top5, headers=["Функция", "Количество"], tablefmt="fancy_grid"))

if __name__ == "__main__":
    scan_and_patch()
    asyncio.run(run_full_scan())
    print_results()
    save_to_csv()
    analyze_top_functions()
