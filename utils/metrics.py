# utils/metrics.py
import time
import logging
import inspect
import functools
from telegram import Update
from telegram.ext import Application

def _tag_from_update(update: Update) -> str:
    try:
        if update.callback_query:
            data = update.callback_query.data
            return f"callback:{data}"
        if update.message:
            txt = (update.message.text or "").strip()
            if txt.startswith("/"):
                return txt  # команда
            return "message"
        if update.inline_query:
            return "inline_query"
        if update.edited_message:
            return "edited_message"
        return "update"
    except Exception:
        return "update"

def _timed_callback(cb, *, name: str | None = None):
    name = name or getattr(cb, "__name__", repr(cb))

    if inspect.iscoroutinefunction(cb):
        @functools.wraps(cb)
        async def _async_wrapper(update: Update, context, *args, **kwargs):
            start = time.perf_counter()
            try:
                # 🧩 Обновляем last_active_at при любом действии пользователя
                try:
                    from sqlalchemy import text
                    from db.database import get_session

                    if update and update.effective_user:
                        async with get_session() as session:
                            await session.execute(text("""
                                UPDATE users
                                SET last_active_at = NOW()
                                WHERE id = :user_id
                            """), {"user_id": update.effective_user.id})
                            await session.commit()
                except Exception as e:
                    logging.warning(f"⚠️ Не удалось обновить last_active_at: {e}")

                # 💬 Выполняем оригинальный хендлер
                return await cb(update, context, *args, **kwargs)

            finally:
                dur = time.perf_counter() - start
                logging.info(f"⚡️ {name} {_tag_from_update(update)} — {dur:.2f} сек")
        return _async_wrapper

    # 🧩 Синхронная версия (для обычных функций)
    @functools.wraps(cb)
    def _sync_wrapper(update: Update, context, *args, **kwargs):
        start = time.perf_counter()
        try:
            return cb(update, context, *args, **kwargs)
        finally:
            dur = time.perf_counter() - start
            logging.info(f"⚡️ {name} {_tag_from_update(update)} — {dur:.2f} сек")
    return _sync_wrapper


def wrap_all_handlers(app: Application) -> None:
    """
    Обходит все группы хендлеров Application и подменяет им .callback на обёрнутый таймером.
    Ничего не ломает: сигнатура сохраняется, имя функции тоже.
    """
    for group, handlers in app.handlers.items():
        for h in handlers:
            try:
                orig = h.callback
                h.callback = _timed_callback(orig, name=getattr(orig, "__name__", None))
            except Exception as e:
                logging.warning(f"⚠️ Не удалось обернуть хендлер {h}: {e}")
