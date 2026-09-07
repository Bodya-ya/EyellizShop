# bot_instance.py
from aiogram import Bot
from config import config

bot = Bot(token=config.BOT_TOKEN)