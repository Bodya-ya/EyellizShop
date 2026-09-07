from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, BigInteger, String, Numeric, Boolean, DateTime, Integer
from datetime import datetime
import uuid
from decimal import Decimal

# SQLite для локальной разработки
DATABASE_URL = "sqlite+aiosqlite:///bytecoin.db"

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()


def format_decimal(value):
    """Форматирует Decimal — убирает лишние нули и копейки"""
    if value is None:
        return "0"
    value = Decimal(str(value))
    if value == 0:
        return "0"
    # Округляем до целого
    return f"{value:.0f}"


class User(Base):
    __tablename__ = "users"

    telegram_id = Column(BigInteger, primary_key=True)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    total_bought_coins = Column(Numeric, default=0)
    total_sold_coins = Column(Numeric, default=0)
    total_bought_rub = Column(Numeric, default=0)
    total_sold_rub = Column(Numeric, default=0)
    is_banned = Column(Boolean, default=False)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class PaymentMethod(Base):
    __tablename__ = "payment_methods"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger)
    method_type = Column(String(10))  # 'card', 'sbp', 'stars'
    card_number = Column(String(50), nullable=True)
    card_bank = Column(String(100), nullable=True)
    sbp_phone = Column(String(20), nullable=True)
    sbp_bank = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PendingSell(Base):
    __tablename__ = "pending_sells"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger)
    created_at = Column(DateTime, default=datetime.utcnow)


class Settings(Base):
    __tablename__ = "settings"

    key = Column(String(50), primary_key=True)
    value = Column(String(255))


class Deal(Base):
    __tablename__ = "deals"

    id = Column(Integer, primary_key=True, autoincrement=True)  # Простой номер
    deal_number = Column(String(20), unique=True)  # Номер для отображения: #1, #2, ...
    user_id = Column(BigInteger)
    type = Column(String(10))
    coins_amount = Column(Numeric)
    rub_amount = Column(Numeric)
    rate = Column(Numeric)
    status = Column(String(20), default='pending')
    payment_method = Column(String(20))
    transaction_id = Column(String(36), nullable=True)
    idempotency_key = Column(String(128), unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    event_id = Column(String(36), primary_key=True)
    transaction_id = Column(String(36), unique=True)
    user_id = Column(BigInteger)
    sum = Column(Numeric)
    processed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session():
    async with async_session() as session:
        yield session


async def get_setting(key: str, default: str = "0") -> str:
    """Получает настройку по ключу"""
    async with async_session() as session:
        setting = await session.get(Settings, key)
        return setting.value if setting else default

async def set_setting(key: str, value: str):
    """Сохраняет настройку"""
    async with async_session() as session:
        setting = await session.get(Settings, key)
        if setting:
            setting.value = value
        else:
            setting = Settings(key=key, value=value)
            session.add(setting)
        await session.commit()