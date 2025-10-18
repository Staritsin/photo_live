from pydantic_settings import BaseSettings
from typing import List
import os


class Settings(BaseSettings):
    # === Бот и токены ===
    telegram_bot_token: str
    replicate_api_token: str = ""

    enable_free_trial: bool = False
    free_trial_count: int = 0
    bonus_per_friend: int = 1
    engine: str = "fal"
    fal_key: str | None = None
    use_postgres: bool = True


    # === Основные цены и пакеты ===
    price_rub: int
    packs: List[int] = [5, 15, 30, 50]
    bonus_per_10: int = 2

    # === URL и вебхуки ===
    return_url: str = ""
    webhook_url: str = ""
    base_public_url: str = ""

    # === БД ===
    database_url: str = ""

    @property
    def async_database_url(self) -> str:
        url = self.database_url.replace("postgresql://", "postgresql+asyncpg://")
        # гарантируем SSL для Render
        if "ssl=" not in url:
            url += ("&" if "?" in url else "?") + "ssl=true"
        return url

    # === Интеграции ===
    imgbb_api_key: str = ""

    # === Платежи ===
    payment_provider: str  # TINKOFF | YOOKASSA
    payment_mode: str = "TEST"  # TEST | PROD

    # === Tinkoff ===
    tinkoff_terminal_key: str = ""
    tinkoff_secret_key: str = ""
    tinkoff_test_url: str = "https://www.tinkoff.ru/kassa/demo/payform"
    tinkoff_prod_url: str = "https://securepay.tinkoff.ru/v2"

    # === YooKassa ===
    yookassa_shop_id: str = os.getenv("YOOKASSA_SHOP_ID", "")
    yookassa_secret_key: str = os.getenv("YOOKASSA_SECRET_KEY", "")
    yookassa_return_url: str = os.getenv("YOOKASSA_RETURN_URL", "https://t.me/Photo_AliveBot")

    # === Админ ===
    admin_id: int = 0

    # === Google Sheets ===
    gsheets_enable: int = 0
    gsheets_spreadsheet_id: str = ""
    gsheets_credentials_file: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "allow"  # 👈 ставим allow вместо ignore


settings = Settings()
