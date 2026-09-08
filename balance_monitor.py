import asyncio
import logging
from decimal import Decimal
from datetime import datetime

from database import get_setting

from bytecoin_api import bytecoin_api
from bot_instance import bot
from config import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Флаг, чтобы не спамить уведомлениями
alert_sent = False


async def check_balance():
    """Проверяет баланс пользователя и уведомляет админов"""
    global alert_sent

    try:
        # Получаем порог из настроек
        threshold_str = await get_setting("balance_alert_threshold", "100000")
        threshold = Decimal(threshold_str)

        user_info = await bytecoin_api.get_user_info([config.BALANCE_ALERT_USER_ID])

        if user_info and user_info.get("items"):
            balance = Decimal(user_info["items"][0].get("balance", "0"))
        else:
            balance = Decimal("0")

        if balance < threshold:
            if not alert_sent:
                for admin_id in config.ADMIN_IDS:
                    try:
                        await bot.send_message(
                            admin_id,
                            f"⚠️ <b>Внимание!</b>\n\n"
                            f"Баланс ниже порога!\n\n"
                            f"Текущий баланс: <b>{balance:.0f} BC</b>\n"
                            f"Порог: <b>{threshold:.0f} BC</b>\n\n"
                            f"Пополните баланс!",
                            parse_mode="HTML"
                        )
                    except:
                        pass
                alert_sent = True
        else:
            alert_sent = False
    except Exception as e:
        logger.error(f"Balance check error: {e}")


async def balance_monitor_loop():
    """Бесконечный цикл проверки баланса"""
    logger.info("Balance monitor started")

    while True:
        await check_balance()
        await asyncio.sleep(60)  # Проверяем раз в минуту