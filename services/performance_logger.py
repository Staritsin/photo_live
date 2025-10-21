# services/performance_logger.py
import time
import functools
import asyncio
import csv
import os
from datetime import datetime

THRESHOLD = 0.3  # всё, что дольше этой секунды — логируем
LOG_FILE = "performance_live.csv"

results = []

def log_slow_call(module, func, duration):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = f"⚡ {module}.{func} — {duration:.2f} сек"
    print(msg)
    results.append((ts, module, func, round(duration, 3)))

def save_to_csv():
    if not results:
        return
    file_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "module", "function", "duration_sec"])
        writer.writerows(results)
    results.clear()

def measure_time(func):
    """Декоратор для замера времени выполнения функций."""
    if asyncio.iscoroutinefunction(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                dur = time.perf_counter() - start
                if dur > THRESHOLD:
                    log_slow_call(func.__module__, func.__name__, dur)
                    save_to_csv()
        return wrapper
    else:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                dur = time.perf_counter() - start
                if dur > THRESHOLD:
                    log_slow_call(func.__module__, func.__name__, dur)
                    save_to_csv()
        return wrapper
