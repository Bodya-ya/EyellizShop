import asyncio
import logging
from decimal import Decimal
from datetime import datetime
from database import async_session

from bytecoin_api import bytecoin_api
from bot_instance import bot
from config import config
from database import get_setting

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def reset_weekly_top():
    """Сбрасывает недельный топ каждый понедельник"""
    while True:
        now = datetime.utcnow()
        # Если понедельник 00:00
        if now.weekday() == 0 and now.hour == 0 and now.minute < 10:
            async with async_session() as session:
                await session.execute("UPDATE users SET total_bought_week = 0")
                await session.commit()
            await asyncio.sleep(600)  # Ждём 10 минут
        await asyncio.sleep(60)