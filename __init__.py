from aiogram import Dispatcher
import logging

logger = logging.getLogger(__name__)


def register_all_handlers(dp: Dispatcher):
    """Регистрирует все обработчики"""
    try:
        import user
        if hasattr(user, "router"):
            dp.include_router(user.router)
            logger.info("Registered user router")
    except Exception as e:
        logger.error(f"Failed to import user: {e}")

    try:
        import admin
        if hasattr(admin, "router"):
            dp.include_router(admin.router)
            logger.info("Registered admin router")
    except Exception as e:
        logger.error(f"Failed to import admin: {e}")