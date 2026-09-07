from dataclasses import dataclass, field
from decimal import Decimal
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

    # Админы
    ADMIN_ID: int = int(os.getenv("ADMIN_ID", "0"))
    # ADMIN_IDS как поле — УДАЛИ ЭТО
    # ADMIN_IDS: list = field(default_factory=...)

    # Bytecoin API
    BYTECOIN_API_KEY: str = os.getenv("BYTECOIN_API_KEY", "")

    # ... остальные поля ...

    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql+asyncpg://..."

    @property
    def REDIS_URL(self) -> str:
        return f"redis://..."

    @property
    def ADMIN_IDS(self) -> list:
        return [int(x) for x in os.getenv("ADMIN_IDS", str(self.ADMIN_ID)).split(",") if x]


config = Config()