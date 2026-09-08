import asyncio
import logging
from decimal import Decimal

from bytecoin_api import bytecoin_api
from bot_instance import bot
from config import config
from database import get_setting

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Словарь: порог -> отправлено ли уведомление
alert_sent = {}


async def check_balance():
    """Проверяет баланс и уведомляет при пересечении порогов"""
    global alert_sent

    try:
        # Получаем пороги из настроек
        thresholds_str = await get_setting("balance_alert_thresholds", "100000,50000,10000")
        thresholds = [Decimal(x.strip()) for x in thresholds_str.split(",")]

        # Получаем баланс
        user_info = await bytecoin_api.get_user_info([config.BALANCE_ALERT_USER_ID])

        if user_info and user_info.get("items"):
            balance = Decimal(user_info["items"][0].get("balance", "0"))
        else:
            balance = Decimal("0")

        # Проверяем каждый порог
        for threshold in thresholds:
            if balance < threshold:
                if not alert_sent.get(str(threshold), False):
                    for admin_id in config.ADMIN_IDS:
                        try:
                            await bot.send_message(
                                admin_id,
                                f"⚠️ <b>Баланс ниже {threshold:.0f} BC!</b>\n\n"
                                f"Текущий баланс: <b>{balance:.0f} BC</b>\n\n"
                                f"Пополните баланс!",
                                parse_mode="HTML"
                            )
                        except:
                            pass

                    alert_sent[str(threshold)] = True
                    logger.warning(f"Alert sent: {balance} < {threshold}")
            else:
                alert_sent[str(threshold)] = False

    except Exception as e:
        logger.error(f"Balance check error: {e}")


async def balance_monitor_loop():
    logger.info("Balance monitor started")

    while True:
        await check_balance()
        await asyncio.sleep(10)