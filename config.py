from pydantic_settings import BaseSettings
from typing import List
import os


class Settings(BaseSettings):
    # === Бот и токены ===
    telegram_bot_token: str
    replicate_api_token: str = ""
    fal_key: str | None = None
    engine: str = "fal"

    # === Free trial ===
    enable_free_trial: bool = bool(int(os.getenv("ENABLE_FREE_TRIAL", "0")))
    free_trial_gens: int = int(os.getenv("FREE_TRIAL_GENS", "0"))

    # === Основные цены и пакеты ===
    price_rub: int
    packs: List[int] = [1, 3, 10, 20]
    bonus_per_10: int = 2
    bonus_per_friend: int = 1

    # === Google Sheets ===
    gsheets_enable: int = int(os.getenv("GSHEETS_ENABLE", "0"))
    gsheets_spreadsheet_id: str = os.getenv("GSHEETS_SPREADSHEET_ID", "")
    gsheets_credentials_file: str = os.getenv("GSHEETS_CREDENTIALS_FILE", "")

    # === Видео-инструкция ===
    instruction_video_url: str = ""


    # === БД ===
    database_url: str = ""
    use_postgres: bool = os.getenv("USE_POSTGRES", "1").strip() == "1"

    @property
    def async_database_url(self) -> str:
        url = self.database_url.replace("postgresql://", "postgresql+asyncpg://")
        # гарантируем SSL для Render
        if "ssl=" not in url:
            url += ("&" if "?" in url else "?") + "ssl=true"
        return url

    # === URL и вебхуки ===
    return_url: str = ""
    webhook_url: str = ""
    base_public_url: str = ""

    # === Интеграции ===
    imgbb_api_key: str = ""

    # === Платежи ===
    payment_provider: str  # TINKOFF | YOOKASSA
    payment_mode: str = "TEST"  # TEST | PROD

    # === Tinkoff ===
    tinkoff_terminal_key: str = ""
    tinkoff_secret_key: str = ""
    tinkoff_test_url: str = "https://rest-api-test.tinkoff.ru/v2"
    tinkoff_prod_url: str = "https://securepay.tinkoff.ru/v2/"

    # === YooKassa ===
    yookassa_shop_id: str = os.getenv("YOOKASSA_SHOP_ID", "")
    yookassa_secret_key: str = os.getenv("YOOKASSA_SECRET_KEY", "")
    yookassa_return_url: str = os.getenv("YOOKASSA_RETURN_URL", "https://t.me/Photo_AliveBot")

    # === Админ ===
    admin_id: int = 0

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "allow"  # 👈 оставляем allow, чтобы не падало при новых переменных


settings = Settings()

print("✅ Using Postgres (Production)" if settings.use_postgres else "🧩 Using SQLite (Local Mode)")
