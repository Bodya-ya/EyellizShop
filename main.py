import asyncio
import logging
from aiogram import Dispatcher, Bot
from aiogram.fsm.storage.memory import MemoryStorage

from __init__ import register_all_handlers
from database import init_db
from config import config
from balance_monitor import balance_monitor_loop  # ← Импорт

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def main():
    # Инициализация БД
    await init_db()
    logger.info("Database initialized")

    # Создаём бота
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    # Регистрируем обработчики
    register_all_handlers(dp)

    asyncio.create_task(balance_monitor_loop())

    # Запускаем polling
    logger.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())