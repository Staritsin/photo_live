import os
import aiohttp
import asyncio
import base64
import json
from dotenv import load_dotenv
from typing import Optional, AsyncGenerator

# === Загружаем .env ===
load_dotenv()

# === Настройки из .env ===
ENGINE = os.getenv("ENGINE", "replicate").lower()  # fal | replicate
REPLICATE_TOKEN = os.getenv("REPLICATE_API_TOKEN")
FAL_KEY = os.getenv("FAL_KEY")


# === Константы ===
REPLICATE_API_BASE = "https://api.replicate.com/v1"
REPLICATE_MODEL_VERSION = "7e324e5fcb9479696f15ab6da262390cddf5a1efa2e11374ef9d1f85fc0f82da"
FAL_API_URL = "https://fal.run/fal-ai/kling-video/v2.5-turbo/pro/image-to-video"


# === Утилита ===
def encode_image_to_base64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


# === Главная функция ===
async def generate_video_from_photo(photo_path: str, duration: int = 5, prompt: Optional[str] = None) -> AsyncGenerator[dict, None]:
    """
    Универсальный генератор видео из фото.
    ⚙️ ENGINE=fal → Fal.ai
    ⚙️ ENGINE=replicate → Replicate
    """
    print(f"🎬 ENGINE: {ENGINE.upper()} | prompt='{prompt}'")

    if ENGINE == "fal":
        try:
            async for res in _generate_fal(photo_path, prompt):
                yield res
                if res.get("status") in ["succeeded", "processing"]:
                    return
        except Exception as e:
            print("⚠️ Fal.ai ошибка:", e)
            yield {"status": "failed", "error": "Fal.ai недоступен. Попробуйте позже 🙏"}
        return  # ❗ без fallback

    elif ENGINE == "replicate":
        try:
            async for res in _generate_replicate(photo_path, duration, prompt):
                yield res
                if res.get("status") in ["succeeded", "processing"]:
                    return
        except Exception as e:
            print("⚠️ Replicate ошибка:", e)
            yield {"status": "failed", "error": "Replicate недоступен. Попробуйте позже 🙏"}
        return

    else:
        yield {"status": "failed", "error": f"❌ Неизвестный движок: {ENGINE}"}


# === Replicate ===
async def _generate_replicate(photo_path: str, duration: int, prompt: Optional[str]):
    if not REPLICATE_TOKEN:
        yield {"status": "failed", "error": "❌ Нет REPLICATE_API_TOKEN"}
        return

    image_b64 = encode_image_to_base64(photo_path)
    payload = {
        "version": REPLICATE_MODEL_VERSION,
        "input": {
            "start_image": f"data:image/jpeg;base64,{image_b64}",
            "prompt": prompt or "A person blinks and smiles",
            "duration": duration
        }
    }
    headers = {"Authorization": f"Token {REPLICATE_TOKEN}", "Content-Type": "application/json"}

    async with aiohttp.ClientSession() as session:
        async with session.post(f"{REPLICATE_API_BASE}/predictions", headers=headers, json=payload) as r:
            if r.status >= 300:
                err = await r.text()
                yield {"status": "failed", "error": f"Ошибка запуска Replicate: {r.status} {err}"}
                return
            pred = await r.json()
            pred_id = pred.get("id")

        if not pred_id:
            yield {"status": "failed", "error": "❌ prediction_id не найден"}
            return

        for _ in range(60):
            async with session.get(f"{REPLICATE_API_BASE}/predictions/{pred_id}", headers=headers) as s:
                data = await s.json()
                status = data.get("status")

                if status == "succeeded":
                    out = data.get("output")
                    url = out[-1] if isinstance(out, list) else out
                    yield {"status": "succeeded", "url": url}
                    return
                elif status in ("failed", "canceled"):
                    yield {"status": "failed", "error": data.get("error", "❌ генерация не удалась")}
                    return
                else:
                    print("⏳ Replicate статус:", status)
                    yield {"status": "processing"}

            await asyncio.sleep(10)

        yield {"status": "failed", "error": "⏳ Таймаут ожидания Replicate"}


# === Fal.ai ===
# === Fal.ai ===
# === Fal.ai ===
async def _generate_fal(photo_path: str, prompt: Optional[str]):
    if not FAL_KEY:
        yield {"status": "failed", "error": "❌ Нет FAL_KEY"}
        return

    image_b64 = encode_image_to_base64(photo_path)
    headers = {
        "Authorization": f"Key {FAL_KEY}",
        "Content-Type": "application/json"
    }

    # ⚡️ ВНИМАНИЕ: prompt и image_url теперь на верхнем уровне
    payload = {
        "prompt": prompt or "A person smiles",
        "image_url": f"data:image/jpeg;base64,{image_b64}",
        "logs": True
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(FAL_API_URL, headers=headers, json=payload) as r:
            raw = await r.text()
            print("🌐 FAL response:", raw)
            if r.status >= 300:
                yield {"status": "failed", "error": f"Ошибка FAL: {r.status} {raw}"}
                return

            data = json.loads(raw)

    # ✅ Извлечение видео
    video_url = (
        data.get("video", {}).get("url")
        or data.get("output", {}).get("video_url")
        or data.get("output", {}).get("video", {}).get("url")
        or data.get("url")
    )

    if video_url:
        print("✅ Fal.ai видео готово:", video_url)
        yield {"status": "succeeded", "url": video_url}
    else:
        yield {"status": "failed", "error": f"❌ FAL не вернул ссылку на видео. Ответ: {data}"}
