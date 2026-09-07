from dataclasses import dataclass
from decimal import Decimal
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

    # Админы
    ADMIN_ID: int = int(os.getenv("ADMIN_ID", "0"))

    # Bytecoin API
    BYTECOIN_API_KEY: str = os.getenv("BYTECOIN_API_KEY", "")
    BYTECOIN_WEBHOOK_SECRET: str = os.getenv("BYTECOIN_WEBHOOK_SECRET", "")
    BYTECOIN_BASE_URL: str = os.getenv("BYTECOIN_BASE_URL", "https://bytecoin.space/api/public/v1")

    # Database
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "bytecoin")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "")
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "bytecoin_bot")
    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", "5432"))

    # Redis
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))

    # Stars
    STAR_PRICE_BUY: Decimal = Decimal(os.getenv("STAR_PRICE_BUY", "1.2"))
    STAR_PRICE_SELL: Decimal = Decimal(os.getenv("STAR_PRICE_SELL", "1.63"))
    STAR_MIN_AMOUNT: int = int(os.getenv("STAR_MIN_AMOUNT", "10"))

    # Webhook
    WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "https://webhook.eyelliz.ru:8443")
    WEBHOOK_HOST: str = os.getenv("WEBHOOK_HOST", "0.0.0.0")
    WEBHOOK_PORT: int = int(os.getenv("WEBHOOK_PORT", "8080"))

    # Rates
    RATE_BUY: Decimal = Decimal(os.getenv("RATE_BUY", "0.00087"))
    RATE_SELL: Decimal = Decimal(os.getenv("RATE_SELL", "0.00115"))

    # Limits
    MIN_DEAL_RUB: Decimal = Decimal(os.getenv("MIN_DEAL_RUB", "1"))
    MAX_DEAL_RUB: Decimal = Decimal(os.getenv("MAX_DEAL_RUB", "17500"))
    MAX_SELL_COINS: Decimal = Decimal(os.getenv("MAX_SELL_COINS", "9999999999"))
    MIN_BALANCE_THRESHOLD: Decimal = Decimal(os.getenv("MIN_BALANCE_THRESHOLD", "1"))
    LOW_BALANCE_ALERT: Decimal = Decimal(os.getenv("LOW_BALANCE_ALERT", "200"))

    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    @property
    def REDIS_URL(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}"

    @property
    def ADMIN_IDS(self) -> list:
        return [int(x) for x in os.getenv("ADMIN_IDS", str(self.ADMIN_ID)).split(",") if x]


config = Config()