import os
from aiocryptopay import AioCryptoPay, Networks
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, PreCheckoutQuery, ContentType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from decimal import Decimal, InvalidOperation
from bytecoin_api import bytecoin_api
from datetime import datetime, timedelta
import uuid
import json
import html
from bot_instance import bot  # ← Для bot
from sqlalchemy import select, func  # ← Для select
from database import async_session, User, Deal, format_decimal, get_setting, set_setting, PaymentMethod, PendingSell, HiddenUser
from config import config
from user_kb import main_menu_kb, payment_method_sell_kb, confirm_kb, saved_payments_kb, payment_method_buy_kb

import logging
logger = logging.getLogger(__name__)

router = Router()

class BuyStates(StatesGroup):
    waiting_amount = State()
    waiting_confirm = State()
    waiting_stars_amount = State()  # ← Для ввода звёзд

MAIN_MENU_BUTTONS = [
    "Купить BC",
    "Продать BC",
    "Курс и лимиты",
    "Топ покупателей",  # ← Добавь
    "Мой профиль",
    "О сервисе"
]






confirm_spam = {}


@router.callback_query(F.data == "confirm_payment")
async def confirm_sell_payment(callback: CallbackQuery, state: FSMContext):
    """Ждём webhook — он сам создаст сделку"""

    user_id = callback.from_user.id
    now = datetime.utcnow()

    # Проверяем, нажимал ли пользователь за последние 15 секунд
    if user_id in confirm_spam:
        last_time = confirm_spam[user_id]
        if (now - last_time).total_seconds() < 15:
            await callback.answer(
                "⏳ Подождите 15 секунд перед повторной проверкой",
                show_alert=True
            )
            return

    # Сохраняем время нажатия
    confirm_spam[user_id] = now

    await callback.message.answer(
        '<tg-emoji emoji-id="5215277915930896212">⏳</tg-emoji> Ожидаем подтверждение перевода...\n\n'
        "Как только BC поступят на сервис, сделка будет создана автоматически.",
        parse_mode="HTML"
    )
    await callback.answer("⏳ Ожидайте...")

def parse_amount(text: str) -> Decimal:
    """Парсит сумму с поддержкой 'к' и 'кк'
    1к = 1 000
    2.2к = 2 200
    2кк = 2 000 000
    """
    text = text.strip().lower()

    # Проверяем "кк" (миллионы)
    if text.endswith("кк") or text.endswith("kk"):
        return Decimal(text[:-2]) * Decimal("1000000")

    # Проверяем "к" (тысячи)
    if text.endswith("к") or text.endswith("k"):
        return Decimal(text[:-1]) * Decimal("1000")

    # Обычное число
    return Decimal(text)

class SellStates(StatesGroup):
    waiting_amount = State()
    waiting_payment_method = State()
    waiting_card_number = State()
    waiting_card_bank = State()
    waiting_sbp_phone = State()
    waiting_sbp_bank = State()
    waiting_transfer = State()


# В начале user.py
cached_balance = {
    "value": None,
    "updated_at": None
}

def format_num(num):
    return f"{num:,.0f}".replace(",", " ")

async def get_cached_balance():
    """Возвращает кэшированный баланс"""
    from datetime import datetime, timedelta

    now = datetime.utcnow()

    # Если кэш свежий (меньше 30 секунд) — возвращаем его
    if cached_balance["value"] is not None and cached_balance["updated_at"]:
        if now - cached_balance["updated_at"] < timedelta(seconds=30):
            return cached_balance["value"]

    try:
        balance = await bytecoin_api.get_balance()
    except Exception as e:
        logger.error(f"API timeout: {e}")
        balance = Decimal("0")

    cached_balance["value"] = balance
    cached_balance["updated_at"] = now

    return balance

async def generate_deal_number() -> str:
    """Генерирует номер сделки: #1, #2, #3..."""
    async with async_session() as session:
        count = await session.scalar(select(func.count()).select_from(Deal))
        return f"#{count + 1}"

async def get_user_payment_methods(user_id: int) -> list:
    """Возвращает все сохранённые реквизиты пользователя"""
    async with async_session() as session:
        result = await session.execute(
            select(PaymentMethod).where(PaymentMethod.user_id == user_id)
        )
        return result.scalars().all()


async def add_payment_method(user_id: int, method_type: str, **kwargs):
    """Добавляет или обновляет способ оплаты"""
    async with async_session() as session:
        # Ищем существующий метод такого типа
        existing = await session.scalar(
            select(PaymentMethod).where(
                PaymentMethod.user_id == user_id,
                PaymentMethod.method_type == method_type
            ).order_by(PaymentMethod.id.desc())
        )

        if existing:
            # Обновляем существующий
            for key, value in kwargs.items():
                setattr(existing, key, value)
            await session.commit()
        else:
            # Создаём новый
            method = PaymentMethod(
                user_id=user_id,
                method_type=method_type,
                **kwargs
            )
            session.add(method)
            await session.commit()

async def finish_sell_deal(event, state: FSMContext, payment_method: str):
    """Завершает сделку продажи"""
    data = await state.get_data()
    coins_amount = data.get("coins_amount") or Decimal("0")  # ← Защита от None
    rub_amount = data.get("rub_amount") or Decimal("0")  # ← Защита от None

    user_id = event.from_user.id if hasattr(event, "from_user") else event.chat.id

    deal_number = await generate_deal_number()

    # Реквизиты для отображения
    if payment_method == "card":
        card = data.get("card_number", "")
        bank = data.get("card_bank", "")
        payment_info = f"💳 {bank} {card[-4:] if card else ''}"
    elif payment_method == "sbp":
        phone = data.get("sbp_phone", "")
        bank = data.get("sbp_bank", "")
        payment_info = f"📱 {bank} {phone}"
    else:
        payment_info = "⭐ Telegram Stars"

    # Создаём сделку
    deal = Deal(
        deal_number=deal_number,
        user_id=user_id,
        type="sell",
        coins_amount=coins_amount,
        rub_amount=rub_amount,
        rate=config.RATE_BUY,
        status="checking",
        payment_method=payment_method,
        transaction_id=data.get("transaction_id", ""),
        idempotency_key=f"sell-{uuid.uuid4()}"
    )

    async with async_session() as session:
        session.add(deal)
        await session.commit()

    user = await session.get(User, user_id)
    username = f"@{user.username}" if user and user.username else "Нет тега"
    first_name = user.first_name if user and user.first_name else "Пользователь"

    # Уведомляем админа
    await bot.send_message(
        config.ADMIN_ID,
        f"🔔 Новая продажа BC!\n\n"
        f"📋 Сделка: {deal_number}\n"
        f"👤 Чел: {first_name} {username}\n\n"
        f"💎 BC: {coins_amount:.0f}\n"
        f"💰 К оплате: {rub_amount:.2f}₽\n"
        f"{payment_info}\n\n"
        f"Выплатите деньги пользователю!"
    )

    # Отправляем сообщение пользователю

    if payment_method == "stars":
        stars = rub_amount / Decimal("1.63")
        payment_text = f"⭐ Вы получите: {stars:.0f} звёзд"
    else:
        payment_text = f"💰 Вы получите: {rub_amount:.2f}₽"

    text = (
        f'<tg-emoji emoji-id="5215538285438311443">💎</tg-emoji> Сделка создана!\n\n'
        f"📋 Сделка: {deal_number}\n"
        f"💎 BC: {coins_amount:.0f}\n"
        f"{payment_text}\n"
        f"{payment_info}\n\n"
        "Ожидайте выплату..."
    )

    if isinstance(event, Message):
        await event.answer(text)
    else:
        await event.message.answer(text)

    await state.clear()


async def handle_menu_buttons(message: Message, state: FSMContext) -> bool:
    print(f"DEBUG text = [{message.text}]")
    print(f"DEBUG in list = {message.text in MAIN_MENU_BUTTONS}")

    if message.text.startswith(("/start", "/admin")):
        await state.clear()
        if message.text == "/start":
            await cmd_start(message, state)
        elif message.text == "/admin":
            from admin import admin_panel
            await admin_panel(message, state)
        return True

    if message.text in MAIN_MENU_BUTTONS:
        await state.clear()

        if message.text == "Купить BC":
            await buy_bytecoin(message, state)
        elif message.text == "Продать BC":
            await sell_bytecoin(message, state)
        elif message.text == "Курс и лимиты":
            await show_rates_and_limits(message)
        elif message.text == "Топ покупателей":
            await top_buyers(message)
        elif message.text == "Мой профиль":
            await my_profile(message)
        elif message.text == "О сервисе":
            await about_cmd(message)
        return True
    return False


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()

    async with async_session() as session:
        user = await session.get(User, message.from_user.id)
        if not user:
            user = User(
                telegram_id=message.from_user.id,
                username=message.from_user.username,
                first_name=message.from_user.first_name
            )
            session.add(user)
            await session.commit()

    try:
        balance = await bytecoin_api.get_balance()
    except:
        balance = Decimal("0")

    def format_rate(rate):
        return f"{rate * 1000:.2f}".rstrip("0").rstrip(".")

    await message.answer(
        f'<tg-emoji emoji-id="5902335789798265487">👤</tg-emoji> <b>Добро пожаловать в Eyelliz Shop!</b>\n\n'
        f'<tg-emoji emoji-id="5197572355634781614">💎</tg-emoji> Здесь вы можете купить или продать BC\n\n'
        f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> <b>Наши курсы:</b>\n\n'
        f'<tg-emoji emoji-id="5429651785352501917">📈</tg-emoji> <b>Купить:</b> <code>{format_rate(config.RATE_SELL)}₽ / 1000 BC</code>\n'
        f'<tg-emoji emoji-id="5429518319243775957">📉</tg-emoji> <b>Продать:</b> <code>{format_rate(config.RATE_BUY)}₽ / 1000 BC</code>\n\n'
        f"Выберите действие:",
        parse_mode="HTML",
        reply_markup=main_menu_kb()
    )


@router.message(F.text == "Купить BC")
async def buy_bytecoin(message: Message, state: FSMContext):
    balance = await get_cached_balance()
    max_sell = Decimal(await get_setting("max_sell_coins", "9999999999"))
    available = min(balance, max_sell)

    if available < 1:
        await message.answer(
            "⚠️ Недостаточно BC для продажи.\n"
            f"Доступно: {available:.0f} BC"
        )
        return

    max_rub_from_coins = available * config.RATE_SELL
    max_rub = min(max_rub_from_coins, config.MAX_DEAL_RUB)

    buttons = [
        [
            InlineKeyboardButton(text="150₽", callback_data="quick_buy:150"),
            InlineKeyboardButton(text="250₽", callback_data="quick_buy:250"),
            InlineKeyboardButton(text="500₽", callback_data="quick_buy:500")
        ],
        [
            InlineKeyboardButton(text="1к", callback_data="quick_buy:1000"),
            InlineKeyboardButton(text="5к", callback_data="quick_buy:5000"),
            InlineKeyboardButton(text=f"Макс ({format_num(int(max_rub))}₽)", callback_data=f"quick_buy:{int(max_rub)}")
        ]
    ]

    await message.answer(
        f'<tg-emoji emoji-id="5197572355634781614">💎</tg-emoji> <b>Покупка BC</b>\n\n'
        f'<tg-emoji emoji-id="5429651785352501917">📈</tg-emoji> Курс: <code>1000 BC = {config.RATE_SELL * 1000:.2f}₽</code>\n\n'
        f'<tg-emoji emoji-id="5278467510604160626">📦</tg-emoji> Доступно: {format_num(available)} BC\n\n'
        f'<tg-emoji emoji-id="5427107837568360763">💵</tg-emoji> Введите сумму в рублях <i>(макс. {format_num(int(max_rub))}₽)</i>:',
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await state.set_state(BuyStates.waiting_amount)  # ← ВОТ ЭТУ СТРОКУ ДОБАВЬ


@router.callback_query(BuyStates.waiting_amount, F.data.startswith("quick_buy:"))
async def quick_buy(callback: CallbackQuery, state: FSMContext):
    amount_rub = Decimal(callback.data.split(":")[1])

    # Проверки
    balance = await get_cached_balance()
    max_sell = Decimal(await get_setting("max_sell_coins", "9999999999"))
    available_coins = min(balance, max_sell)

    if amount_rub < config.MIN_DEAL_RUB:
        await callback.answer(f"❌ Минимум: {config.MIN_DEAL_RUB}₽", show_alert=True)
        return

    if amount_rub > config.MAX_DEAL_RUB:
        await callback.answer(f"❌ Максимум: {config.MAX_DEAL_RUB}₽", show_alert=True)
        return

    coins_amount = amount_rub / config.RATE_SELL

    if coins_amount > available_coins:
        await callback.answer(
            f"❌ Недостаточно BC\nДоступно: {available_coins:.0f} BC",
            show_alert=True
        )
        return

    await state.update_data(
        amount_rub=amount_rub,
        coins_amount=coins_amount
    )

    buttons = [
        [
            InlineKeyboardButton(
                text="СБП",
                callback_data="continue_buy",
                icon_custom_emoji_id="5217961106554769883"
            )
        ]
    ]

    usdt_enabled = await get_setting("usdt_enabled", "1")
    if usdt_enabled == "1":
        buttons.append([
            InlineKeyboardButton(
                text="USDT",
                callback_data="pay_usdt",
                icon_custom_emoji_id="5242551409232069476"
            )
        ])

    stars_enabled = await get_setting("stars_enabled", "1")
    if stars_enabled == "1":
        buttons.append([
            InlineKeyboardButton(
                text="Звёзды",
                callback_data="pay_stars_after_amount",
                icon_custom_emoji_id="5952066863931331270"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="Изменить сумму",
            callback_data="change_buy_amount",
            icon_custom_emoji_id="5264727218734524899"
        )
    ])

    await callback.message.answer(
        f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> <b>Проверьте детали:</b>\n\n'
        f'<tg-emoji emoji-id="5224257782013769471">✅</tg-emoji> <b>Сумма</b>: <i>{amount_rub:.2f}₽</i>\n'
        f'<tg-emoji emoji-id="5197572355634781614">💎</tg-emoji> <b>Получите</b>: <i>{coins_amount:.0f} BC</i>\n'
        f'<tg-emoji emoji-id="5429651785352501917">📈</tg-emoji> <b>Курс</b>: <i>1000 BC = {config.RATE_SELL * 1000:.2f}₽</i>\n\n'
        f"Выберите способ оплаты:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await state.set_state(BuyStates.waiting_confirm)
    await callback.answer()


@router.message(F.text == "Курс и лимиты")
async def show_rates_and_limits(message: Message):
    try:
        balance = await get_cached_balance()
    except Exception as e:
        await message.answer(f"❌ Ошибка получения баланса: {str(e)}")
        return

    rub_balance = Decimal(await get_setting("rub_balance", "0"))
    max_buy_rub = Decimal(await get_setting("max_buy_rub", "15000"))
    max_sell_coins = Decimal(await get_setting("max_sell_coins", "9999999999"))

    available_to_sell_coins = min(balance, max_sell_coins)
    available_to_buy_rub = min(rub_balance, max_buy_rub)
    available_to_buy_coins = available_to_buy_rub / config.RATE_BUY if config.RATE_BUY else Decimal("0")

    def format_num(num):
        return f"{num:,.0f}".replace(",", " ")

    def format_rate(rate):
        # Форматируем курс без лишних нулей
        return f"{rate * 1000:.2f}".rstrip("0").rstrip(".")

    text = (
        f'<tg-emoji emoji-id="5260742580005530450">📊</tg-emoji> <b>Информация</b>\n\n'
        f'<tg-emoji emoji-id="5429651785352501917">📈</tg-emoji> <b>Купить BC:</b> <code>{format_rate(config.RATE_SELL)}₽ / 1000 BC</code>\n'
        f'<tg-emoji emoji-id="5429518319243775957">📉</tg-emoji> <b>Продать BC:</b> <code>{format_rate(config.RATE_BUY)}₽ / 1000 BC</code>\n\n'
        f'<tg-emoji emoji-id="5278467510604160626">📦</tg-emoji> <b>Продадим:</b> <code>{format_num(available_to_sell_coins)} BC</code>\n'
        f'<tg-emoji emoji-id="5440457429147997980">💸</tg-emoji> <b>Выкупим:</b> <code>{format_num(available_to_buy_coins)} BC</code>\n'
        f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> <b>Резерв:</b> <code>{format_num(available_to_buy_rub)}₽</code>'
    )

    await message.answer(text, parse_mode="HTML")

@router.callback_query(F.data == "sell_amount")
async def sell_choose_amount(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "💎 Введите количество BC для продажи:"
    )
    await state.set_state(SellStates.waiting_amount)

@router.callback_query(F.data == "sell_link")
async def sell_choose_link(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        f'<tg-emoji emoji-id="5463424023734014980">🔗</tg-emoji> Переведите BC через ссылку:\n'
        f"https://t.me/byteappbot/app?startapp=transfer-dffc2867ec8c2a106e4e87da\n\n"
        f"После перевода нажмите:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Я перевёл",
                        callback_data="confirm_sell",
                        icon_custom_emoji_id="5215538285438311443"
                    ),
                    InlineKeyboardButton(
                        text="Отмена",
                        callback_data="cancel_sell",
                        icon_custom_emoji_id="5280803324273115630"
                    )
                ]
            ]
        )
    )
    await state.set_state(SellStates.waiting_transfer)


@router.message(F.text == "Топ покупателей")
async def top_buyers(message: Message):
    async with async_session() as session:
        hidden = await session.execute(select(HiddenUser.user_id))
        hidden_ids = [h for h in hidden.scalars().all()]

        result = await session.execute(
            select(User).where(
                User.total_bought_week > 0,
                User.telegram_id.notin_(hidden_ids),
                User.telegram_id.notin_(config.ADMIN_IDS)
            ).order_by(User.total_bought_week.desc()).limit(10)
        )
        users = result.scalars().all()

        if not users:
            await message.answer(
                f'<tg-emoji emoji-id="5409008750893734809">🏆</tg-emoji> Пока нет покупателей за неделю',
                parse_mode="HTML"
            )
            return

        today = datetime.utcnow()
        start_of_week = today - timedelta(days=today.weekday())
        end_of_week = start_of_week + timedelta(days=6)

        start_str = start_of_week.strftime("%d.%m")
        end_str = end_of_week.strftime("%d.%m.%Y")

        text = f'<tg-emoji emoji-id="5409008750893734809">🏆</tg-emoji> <b>ТОП ПОКУПАТЕЛЕЙ</b>\n'

        for i, user in enumerate(users, 1):
            if i == 1:
                medal = '<tg-emoji emoji-id="5280735858926822987">🥇</tg-emoji>'
            elif i == 2:
                medal = '<tg-emoji emoji-id="5283195573812340110">🥈</tg-emoji>'
            elif i == 3:
                medal = '<tg-emoji emoji-id="5282750778409233531">🥉</tg-emoji>'
            else:
                medal = f'<b>{i}.</b>'

            name = html.escape(user.first_name or "Пользователь")
            bought = format_decimal(user.total_bought_week)

            text += f'{medal} {name}\n'
            text += f'   <tg-emoji emoji-id="5197572355634781614">💎</tg-emoji> <b>{bought} BC</b>\n'
            text += '\n'

        text += '━━━━━━━━━━━━━━━━━━\n\n'

        prize = await get_setting("top_prize", "")
        if prize:
            text += f'<tg-emoji emoji-id="5193085063998224234">🎁</tg-emoji> <b>Приз:</b>\n'
            text += f'<i>{prize}</i>\n\n'

        text += f'<b> ТОП </b><i>с {start_str} по {end_str}</i>\n'

        await message.answer(text, parse_mode="HTML")

@router.callback_query(F.data == "change_payment")
async def change_payment(callback: CallbackQuery, state: FSMContext):
    """Изменение реквизитов — просто меняем, без продажи"""
    await callback.message.answer(
        "🔄 Изменение реквизитов\n\n"
        "Выберите новый способ выплаты:",
        reply_markup=payment_method_sell_kb()
    )
    await state.set_state(SellStates.waiting_payment_method)


@router.message(BuyStates.waiting_amount)
async def process_buy_amount(message: Message, state: FSMContext):
    if message.text.startswith(("/start", "/admin")):
        await state.clear()
        return

    if await handle_menu_buttons(message, state):
        return

    try:
        amount_rub = parse_amount(message.text)

        if amount_rub < config.MIN_DEAL_RUB:
            await message.answer(f"❌ Минимальная сумма: {config.MIN_DEAL_RUB}₽")
            return

        if amount_rub > config.MAX_DEAL_RUB:
            await message.answer(f"❌ Максимальная сумма: {config.MAX_DEAL_RUB}₽")
            return

        balance = await get_cached_balance()
        max_sell = Decimal(await get_setting("max_sell_coins", "9999999999"))
        available_coins = min(balance, max_sell)

        coins_amount = amount_rub / config.RATE_SELL

        if coins_amount > available_coins:
            await message.answer(
                f"❌ Недостаточно BC для продажи\n"
                f"Доступно: {available_coins:.0f} BC\n"
                f"Максимальная сумма: {available_coins * config.RATE_SELL:.2f}₽"
            )
            return

        await state.update_data(
            amount_rub=amount_rub,
            coins_amount=coins_amount
        )

        # Формируем кнопки
        buttons = [
            [
                InlineKeyboardButton(
                    text="СБП",
                    callback_data="continue_buy",
                    icon_custom_emoji_id="5265074015868822600"
                )
            ],
        ]

        usdt_enabled = await get_setting("usdt_enabled", "1")
        if usdt_enabled == "1":
            buttons.append([InlineKeyboardButton(text="🪙 USDT", callback_data="pay_usdt")])

        stars_enabled = await get_setting("stars_enabled", "1")
        if stars_enabled == "1":
            buttons.append([InlineKeyboardButton(text="⭐ Звёзды", callback_data="pay_stars_after_amount")])

        buttons.append([InlineKeyboardButton(text="🔄 Изменить сумму", callback_data="change_buy_amount")])

        await message.answer(
            f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> <b>Проверьте детали:</b>\n\n'
            f'<tg-emoji emoji-id="5224257782013769471">✅</tg-emoji> Сумма: {amount_rub:.2f}₽\n'
            f'<tg-emoji emoji-id="5197572355634781614">💎</tg-emoji> Получите: {coins_amount:.0f} BC\n'
            f'<tg-emoji emoji-id="5429651785352501917">📈</tg-emoji> Курс: 1000 BC = {config.RATE_SELL * 1000:.2f}₽\n\n'
            f"Выберите способ оплаты:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )
        await state.set_state(BuyStates.waiting_confirm)

    except (ValueError, InvalidOperation):
        await message.answer("❌ Введите сумму в рублях (просто число)")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

@router.callback_query(F.data == "cancel_sell")
async def cancel_sell_transfer(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        f'<tg-emoji emoji-id="5280803324273115630">❌</tg-emoji> Сделка отменена',
        parse_mode="HTML"
    )


@router.callback_query(BuyStates.waiting_confirm, F.data == "continue_buy")
async def continue_buy(callback: CallbackQuery, state: FSMContext):
    """Продолжаем — показываем реквизиты"""
    data = await state.get_data()
    amount_rub = data.get("amount_rub")
    coins_amount = data.get("coins_amount")

    deal_number = await generate_deal_number()
    idempotency_key = f"buy-{uuid.uuid4()}"

    async with async_session() as session:
        deal = Deal(
            deal_number=deal_number,
            user_id=callback.from_user.id,
            type="buy",
            coins_amount=coins_amount,
            rub_amount=amount_rub,
            rate=config.RATE_SELL,
            status="pending",
            payment_method="sbp",
            idempotency_key=idempotency_key
        )
        session.add(deal)
        await session.commit()

    requisites = await get_setting("payment_requisites", "+7 958 238-99-88 (СБЕРБАНК)")

    await callback.message.answer(
        f'<tg-emoji emoji-id="5265074015868822600">📱</tg-emoji> <b>Оплата по СБП</b>\n\n'
        f'<tg-emoji emoji-id="5224257782013769471">✅</tg-emoji> Сумма: {amount_rub:.2f}₽\n'
        f'<tg-emoji emoji-id="5197572355634781614">💎</tg-emoji> Получите: {coins_amount:.0f} BC\n'
        f'<tg-emoji emoji-id="5440457429147997980">📋</tg-emoji> Сделка: {deal_number}\n\n'
        f"‼️<code>{requisites}</code>‼️\n\n"
        f'<tg-emoji emoji-id="5215277915930896212">⏳</tg-emoji> Перевести в течении часа!\n\n'
        f"После оплаты нажмите:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Я оплатил",
                        callback_data="confirm_buy_payment",
                        icon_custom_emoji_id="5215538285438311443"
                    )
                ],
                [
                    InlineKeyboardButton(text="❓ Оплатил, но не пришло", url="https://t.me/m/BWVc5SHcOTgy")
                ]
            ]
        )
    )
    await state.clear()


@router.callback_query(BuyStates.waiting_confirm, F.data == "change_buy_amount")
async def change_buy_amount(callback: CallbackQuery, state: FSMContext):
    """Изменяем сумму"""
    await callback.message.answer(
        "💵 Введите новую сумму в рублях:"
    )
    await state.set_state(BuyStates.waiting_amount)
    await callback.answer()

@router.callback_query(F.data == "sell_specific_amount")
async def sell_specific_amount(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    available_coins = data.get("available_coins", Decimal("0"))
    user_balance = data.get("user_balance", Decimal("0"))

    max_can_sell = min(available_coins, user_balance)

    await callback.message.answer(
        f'<tg-emoji emoji-id="5258334778389710253">🔢</tg-emoji> Укажите количество BC\n\n'
        f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Максимум: {format_num(max_can_sell)} BC\n\n'
        f"<i>Можно: <code>1кк</code>,<code>100к</code>,<code>2кк</code></i>\n\n"
        f'<tg-emoji emoji-id="5197572355634781614">💎</tg-emoji> Введите количество:',
        parse_mode="HTML",
    )
    await state.set_state(SellStates.waiting_amount)

@router.callback_query(BuyStates.waiting_confirm, F.data == "pay_sbp")
async def pay_with_sbp(callback: CallbackQuery, state: FSMContext):
    balance = await get_cached_balance()
    max_sell = Decimal(await get_setting("max_sell_coins", "9999999999"))
    available = min(balance, max_sell)
    available_rub = available * config.RATE_SELL

    await callback.message.answer(
        "💳 <b>Оплата через СБП</b>\n\n"
        f"📦 Доступно: {format_num(available)} BC\n\n"
        f"📈 Курс: <code>1000 BC = {config.RATE_SELL * 1000:.2f}₽</code>\n\n"
        f"Введите сумму в рублях(<i>макс {available_rub:.2f}₽</i>):\n"
        f"Пример: <code>200</code> ; <code>500</code> ; <code>1к</code>\n\n"
        f"<i>Минимум: {config.MIN_DEAL_RUB}₽ | Максимум: {config.MAX_DEAL_RUB}₽</i>",
        parse_mode="HTML"
    )
    await state.set_state(BuyStates.waiting_amount)
    await callback.answer()

@router.callback_query(BuyStates.waiting_confirm, F.data == "pay_stars")
async def pay_with_stars(callback: CallbackQuery, state: FSMContext):
    stars_enabled = await get_setting("stars_enabled", "1")
    if stars_enabled != "1":
        await callback.answer("❌ Оплата звёздами отключена", show_alert=True)
        return
    await callback.message.answer(
        "⭐ <b>Оплата звёздами</b>\n\n"
        f"Курс: <code>1 звезда = {config.STAR_PRICE_BUY}₽</code>\n"
        f"<i>Минимум: {config.STAR_MIN_AMOUNT} звёзд</i>\n\n"
        f"Введите количество звёзд:",
        parse_mode="HTML"
    )
    await state.set_state(BuyStates.waiting_stars_amount)
    await callback.answer()


@router.message(BuyStates.waiting_stars_amount)
async def process_stars_amount(message: Message, state: FSMContext):
    if message.text.startswith(("/start", "/admin")):
        await state.clear()
        return

    if await handle_menu_buttons(message, state):
        return

    try:
        stars_amount = int(parse_amount(message.text))  # ← Парсим

        if stars_amount < config.STAR_MIN_AMOUNT:
            await message.answer(f"❌ Минимум: {config.STAR_MIN_AMOUNT} звёзд")
            return

        # Считаем
        rub_amount = Decimal(stars_amount) * config.STAR_PRICE_BUY
        coins_amount = rub_amount / config.RATE_SELL

        await state.update_data(
            coins_amount=coins_amount,
            stars_amount=stars_amount,
            amount_rub=rub_amount
        )

        await message.answer(
            f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> <b>Проверьте:</b>\n\n'
            f'<tg-emoji emoji-id="5952066863931331270">⭐</tg-emoji> Звёзд: {stars_amount}\n'
            f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Эквивалент: {rub_amount:.2f}₽\n'
            f'<tg-emoji emoji-id="5197572355634781614">💎</tg-emoji> Получите: {coins_amount:.0f} BC\n\n'
            f"Нажмите кнопку для оплаты:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"⭐ Оплатить {stars_amount} звёзд",
                            callback_data=f"pay_stars_confirm:{stars_amount}"
                        )
                    ]
                ]
            )
        )
        await state.set_state(BuyStates.waiting_confirm)

    except ValueError:
        await message.answer("❌ Введите целое число звёзд")


@router.callback_query(F.data.startswith("pay_stars_confirm:"))
async def pay_stars_confirm(callback: CallbackQuery, state: FSMContext):
    stars_amount = int(callback.data.split(":")[1])
    data = await state.get_data()
    coins_amount = data.get("coins_amount")

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="Покупка BC",
        description=f"Покупка {coins_amount:.0f} BC за {stars_amount} звёзд",
        payload=f"buy_bc_{callback.from_user.id}_{uuid.uuid4()}",
        provider_token="",
        currency="XTR",
        prices=[{"label": "Покупка BC", "amount": stars_amount}]
    )

    await callback.answer("⭐ Счёт выставлен!")

@router.message(F.text == "Продать BC")
async def sell_bytecoin(message: Message, state: FSMContext):
    rub_balance = Decimal(await get_setting("rub_balance", "0"))
    max_buy_rub = Decimal(await get_setting("max_buy_rub", "15000"))
    available_rub = min(rub_balance, max_buy_rub)
    available_coins = available_rub / config.RATE_BUY if config.RATE_BUY else Decimal("0")

    user_info = await bytecoin_api.get_user_info([message.from_user.id])
    user_balance = Decimal("0")
    if user_info and user_info.get("items"):
        user_balance = Decimal(user_info["items"][0].get("balance", "0"))

    if available_rub <= 0:
        await message.answer(
            "❌ <b>Недостаточно резерва</b>\n\n"
            "Мы временно не выкупаем BC.\n"
            "⏳ Попробуйте позже.",
            parse_mode="HTML"
        )
        return

    await state.update_data(
        available_coins=available_coins,
        user_balance=user_balance
    )

    methods = await get_user_payment_methods(message.from_user.id)

    # Если есть сохранённые реквизиты — создаём PendingSell
    if methods:
        async with async_session() as session:
            # Удаляем старые pending
            old_pendings = await session.execute(
                select(PendingSell).where(PendingSell.user_id == message.from_user.id)
            )
            for old in old_pendings.scalars().all():
                await session.delete(old)

            # Создаём новый
            pending = PendingSell(user_id=message.from_user.id)
            session.add(pending)
            await session.commit()

    await message.answer(
        f'<tg-emoji emoji-id="5197572355634781614">💎</tg-emoji> Продажа BC\n\n'
        f'<tg-emoji emoji-id="5429518319243775957">📉</tg-emoji> Курс: <code>1000 BC = {config.RATE_BUY * 1000:.2f}₽</code>\n\n'
        f'<tg-emoji emoji-id="5278467510604160626">📦</tg-emoji> Ваш баланс: {format_num(user_balance)} BC\n\n'
        f'<tg-emoji emoji-id="5224257782013769471">✅</tg-emoji> Мы можем выкупить до: <code>{format_num(available_coins)}</code> BC\n'
        f'Минимум: <code>{config.MIN_SELL_RUB}₽</code> | Максимум: <code>{config.MAX_DEAL_RUB}₽</code>\n\n'
        f"Выберите реквизиты для выплаты:",
        parse_mode="HTML",
        reply_markup=saved_payments_kb(methods) if methods else payment_method_sell_kb()
    )
    await state.set_state(SellStates.waiting_payment_method)


@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@router.message(F.content_type == ContentType.SUCCESSFUL_PAYMENT)
async def process_successful_payment(message: Message, state: FSMContext):
    """Оплата звёздами прошла успешно"""
    stars_amount = Decimal(message.successful_payment.total_amount)
    rub_amount = stars_amount * config.STAR_PRICE_BUY
    coins_amount = rub_amount / config.RATE_SELL

    deal_number = await generate_deal_number()
    deal = Deal(
        deal_number=deal_number,
        user_id=message.from_user.id,
        type="buy",
        coins_amount=coins_amount,
        rub_amount=rub_amount,
        rate=config.RATE_SELL,
        status="completed",
        payment_method="stars",
        idempotency_key=f"buy-stars-{uuid.uuid4()}"
    )

    async with async_session() as session:
        session.add(deal)
        await session.commit()

        user = await session.get(User, message.from_user.id)
        if user:
            user.total_bought_coins = (user.total_bought_coins or 0) + coins_amount
            user.total_bought_rub = (user.total_bought_rub or 0) + rub_amount
            await session.commit()

    result = await bytecoin_api.transfer_to_user(
        user_id=message.from_user.id,
        sum_coins=coins_amount,
        idempotency_key=f"transfer-stars-{uuid.uuid4()}"
    )

    if result.get("status") == "ok":
        await bot.send_message(
            config.ADMIN_ID,
            f'<tg-emoji emoji-id="5952066863931331270">⭐</tg-emoji> <b>Оплата звёздами!</b>\n\n'
            f'<tg-emoji emoji-id="5440457429147997980">📋</tg-emoji> Сделка: {deal_number}\n'
            f'<tg-emoji emoji-id="5902335789798265487">👤</tg-emoji> User: <code>{message.from_user.id}</code>\n'
            f'<tg-emoji emoji-id="5952066863931331270">⭐</tg-emoji> Звёзд: {stars_amount:.0f}\n'
            f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Рублей: {rub_amount:.2f}₽\n'
            f'<tg-emoji emoji-id="5197572355634781614">💎</tg-emoji> BC начислено: {coins_amount:.0f}\n'
            f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Статус: Завершена',
            parse_mode="HTML"
        )

        await message.answer(
            f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Оплата звёздами успешна!\n\n'
            f'<tg-emoji emoji-id="5440457429147997980">📋</tg-emoji> Сделка: {deal_number}\n'
            f'<tg-emoji emoji-id="5952066863931331270">⭐</tg-emoji> Потрачено: {stars_amount:.0f} звёзд\n'
            f'<tg-emoji emoji-id="5197572355634781614">💎</tg-emoji> Вы получили: {coins_amount:.0f} BC',
            parse_mode="HTML"
        )
    else:
        await message.answer(f"❌ Ошибка: {result.get('error', 'Unknown')}")

@router.callback_query(F.data.startswith("use_payment:"))
async def use_saved_payment(callback: CallbackQuery, state: FSMContext):
    payment_id = callback.data.split(":")[1]

    async with async_session() as session:
        method = await session.get(PaymentMethod, int(payment_id))

        if method:
            if method.method_type == "card":
                await state.update_data(
                    card_number=method.card_number,
                    card_bank=method.card_bank,
                    payment_method="card"
                )
                payment_info = f"💳 {method.card_bank} •••• {method.card_number[-4:]}"
            elif method.method_type == "sbp":
                await state.update_data(
                    sbp_phone=method.sbp_phone,
                    sbp_bank=method.sbp_bank,
                    payment_method="sbp"
                )
                payment_info = f"📱 {method.sbp_bank} {method.sbp_phone}"
            else:
                await state.update_data(payment_method="stars")
                payment_info = "⭐ Telegram Stars"

            pending = PendingSell(user_id=callback.from_user.id)
            session.add(pending)
            await session.commit()

            # НЕ создаём сделку! Просто показываем инструкцию
            await callback.message.answer(
                f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Выбрано: {payment_info}\n\n'
                f"Переведите BC любым для вас удобным методом:\n\n"
                f'<tg-emoji emoji-id="5258334778389710253">🔢</tg-emoji> Указать количество — если хотите узнать сумму выплаты в рублях.\n\n'
                f'<tg-emoji emoji-id="5463424023734014980">🔗</tg-emoji> По ссылке — переведите любую сумму, мы автоматически посчитаем выплату.\n\n'
                f"После перевода нажмите:",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Указать количество",
                                callback_data="sell_specific_amount",
                                icon_custom_emoji_id="5258334778389710253"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="Перевести по ссылке",
                                url="https://t.me/byteappbot/app?startapp=transfer-dffc2867ec8c2a106e4e87da",
                                icon_custom_emoji_id="5463424023734014980"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="Я перевёл",
                                callback_data="confirm_payment",
                                icon_custom_emoji_id="5215538285438311443"
                            )
                        ],
                        [
                            InlineKeyboardButton(text="❓ Перевёл, но не пришло", url="https://t.me/m/BWVc5SHcOTgy")
                        ]
                    ]
                )
            )
            await state.set_state(SellStates.waiting_amount)


@router.message(SellStates.waiting_amount)
async def process_sell_amount(message: Message, state: FSMContext):
    if message.text.startswith(("/start", "/admin")):
        await state.clear()
        if message.text == "/start":
            await cmd_start(message, state)
        elif message.text == "/admin":
            await message.answer("Введите /admin для доступа к админ-панели")
        return

    if await handle_menu_buttons(message, state):
        return

    try:
        coins_amount = parse_amount(message.text)  # ← Парсим с "к" и "кк"
        rub_amount = coins_amount * config.RATE_BUY

        # Проверяем лимиты
        if rub_amount < config.MIN_SELL_RUB:
            min_coins = config.MIN_SELL_RUB / config.RATE_BUY
            await message.answer(
                f"❌ Минимальная сумма продажи: {config.MIN_SELL_RUB}₽\n"
                f"Нужно минимум: {min_coins:.0f} BC"
            )
            return

        if rub_amount > config.MAX_DEAL_RUB:
            max_coins = config.MAX_DEAL_RUB / config.RATE_BUY
            await message.answer(
                f"❌ Максимальная сумма: {config.MAX_DEAL_RUB}₽\n"
                f"🟠 Максимум: {max_coins:.0f} BC"
            )
            return

        # Проверяем баланс пользователя
        user_info = await bytecoin_api.get_user_info([message.from_user.id])
        user_balance = Decimal("0")
        if user_info and user_info.get("items"):
            user_balance = Decimal(user_info["items"][0].get("balance", "0"))

        if coins_amount > user_balance:
            await message.answer(
                f"❌ У вас недостаточно BC\n"
                f"Ваш баланс: {user_balance:.0f} BC"
            )
            return

        # Проверяем наш лимит на выкуп
        rub_balance = Decimal(await get_setting("rub_balance", "0"))
        max_buy_rub = Decimal(await get_setting("max_buy_rub", "15000"))
        available_rub = min(rub_balance, max_buy_rub)

        if rub_amount > available_rub:
            await message.answer(
                f"❌ Мы сейчас можем выкупить только на {available_rub}₽\n"
                f"Попробуйте уменьшить сумму"
            )
            return

        await state.update_data(
            coins_amount=coins_amount,
            rub_amount=rub_amount
        )

        # Получаем реквизиты из state
        data = await state.get_data()
        payment_method = data.get("payment_method", "sbp")

        # НЕ СОЗДАЁМ СДЕЛКУ! Просто показываем инструкцию
        await message.answer(
            f'<tg-emoji emoji-id="5445355530111437729">📤</tg-emoji> Перевод BC\n\n'
            f'<tg-emoji emoji-id="5197572355634781614">💎</tg-emoji> Количество: {format_num(coins_amount)} BC\n'
            f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Вы получите: {rub_amount:.2f}₽\n\n'
            f"🟠 Переведите BC по ссылке:\n"
            f"https://t.me/byteappbot/app?startapp=transfer-dffc2867ec8c2a106e4e87da\n\n"
            f"После перевода нажмите:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Перевести BC",
                            url="https://t.me/byteappbot/app?startapp=transfer-dffc2867ec8c2a106e4e87da",
                            icon_custom_emoji_id="5463424023734014980"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="Я перевёл",
                            callback_data="confirm_payment",
                            icon_custom_emoji_id="5215538285438311443"
                        ),
                        InlineKeyboardButton(
                            text="Отмена",
                            callback_data="cancel_deal",
                            icon_custom_emoji_id="5280803324273115630"
                        )
                    ],
                    [
                        InlineKeyboardButton(text="❓ Перевёл, но не пришло", url="https://t.me/m/BWVc5SHcOTgy")
                    ]
                ]
            )
        )
        await state.clear()

    except (ValueError, InvalidOperation):
        await message.answer("❌ Введите корректное количество")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")


@router.callback_query(F.data == "confirm_payment")
async def confirm_sell_payment(callback: CallbackQuery, state: FSMContext):
    """Ждём webhook — он сам создаст сделку"""

    await callback.message.answer(
        f'<tg-emoji emoji-id="5215277915930896212">⏳</tg-emoji> Ожидаем подтверждение перевода...\n\n'
        "Как только BC поступят на сервис, сделка будет создана автоматически.",
        parse_mode="HTML"
    )
    await callback.answer("⏳ Ожидайте...")


@router.callback_query(F.data == "cancel_deal")
async def cancel_deal(callback: CallbackQuery):
    # Сбрасываем кэш баланса
    cached_balance["value"] = None
    cached_balance["updated_at"] = None

    """Отмена сделки пользователем"""
    from database import async_session, Deal
    from sqlalchemy import select

    async with async_session() as session:
        deal = await session.scalar(
            select(Deal).where(
                Deal.user_id == callback.from_user.id,
                Deal.status.in_(["pending", "checking"])
            ).order_by(Deal.created_at.desc())
        )

        if deal:
            deal.status = "cancelled"
            await session.commit()
            await callback.message.edit_text(
                f'<tg-emoji emoji-id="5280803324273115630">❌</tg-emoji> Сделка отменена',
                parse_mode="HTML"
            )
        else:
            await callback.answer("Нет активных сделок", show_alert=True)


@router.callback_query(F.data == "top_week")
async def top_week(callback: CallbackQuery):
    async with async_session() as session:
        hidden = await session.execute(select(HiddenUser.user_id))
        hidden_ids = [h for h in hidden.scalars().all()]

        result = await session.execute(
            select(User).where(
                User.total_bought_week > 0,
                User.telegram_id.notin_(hidden_ids),
                User.telegram_id.notin_(config.ADMIN_IDS)
            ).order_by(User.total_bought_week.desc()).limit(10)
        )
        users = result.scalars().all()

        if not users:
            await callback.answer("Пока нет покупателей за неделю", show_alert=True)
            return

        today = datetime.utcnow()
        start_of_week = today - timedelta(days=today.weekday())
        end_of_week = start_of_week + timedelta(days=6)

        start_str = start_of_week.strftime("%d.%m")
        end_str = end_of_week.strftime("%d.%m.%Y")

        text = f'<tg-emoji emoji-id="5409008750893734809">🏆</tg-emoji> <b>ТОП ПОКУПАТЕЛЕЙ</b>\n'

        for i, user in enumerate(users, 1):
            if i == 1:
                medal = '<tg-emoji emoji-id="5280735858926822987">🥇</tg-emoji>'
            elif i == 2:
                medal = '<tg-emoji emoji-id="5283195573812340110">🥈</tg-emoji>'
            elif i == 3:
                medal = '<tg-emoji emoji-id="5282750778409233531">🥉</tg-emoji>'
            else:
                medal = f'<b>{i}.</b>'

            name = html.escape(user.first_name or "Пользователь")
            bought = format_decimal(user.total_bought_week)

            text += f'{medal} {name}\n'
            text += f'   <tg-emoji emoji-id="5197572355634781614">💎</tg-emoji> <b>{bought} BC</b>\n'
            text += '\n'

        text += '━━━━━━━━━━━━━━━━━━\n\n'

        prize = await get_setting("top_prize", "")
        if prize:
            text += f'<tg-emoji emoji-id="5193085063998224234">🎁</tg-emoji> <b>Приз:</b>\n'
            text += f'<i>{prize}</i>\n\n'

        text += f'<b> ТОП </b><i>с {start_str} по {end_str}</i>\n'

        await callback.message.edit_text(text, parse_mode="HTML")
        await callback.answer()


@router.message(F.text == "Мой профиль")
async def my_profile(message: Message):
    async with async_session() as session:
        user = await session.get(User, message.from_user.id)

        if not user:
            await message.answer("Вы ещё не зарегистрированы. Нажмите /start")
            return

        bought_coins = format_decimal(user.total_bought_coins)
        sold_coins = format_decimal(user.total_sold_coins)
        bought_rub = format_decimal(user.total_bought_rub)
        sold_rub = format_decimal(user.total_sold_rub)

        await message.answer(
            f'<tg-emoji emoji-id="5902335789798265487">👤</tg-emoji> Мой профиль\n\n'
            f'<tg-emoji emoji-id="5429651785352501917">📈</tg-emoji> Всего куплено: {bought_coins} BC\n'
            f'<tg-emoji emoji-id="5429518319243775957">📉</tg-emoji> Всего продано: {sold_coins} BC\n'
            f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Потрачено: {bought_rub}₽\n'
            f'<tg-emoji emoji-id="5440457429147997980">💸</tg-emoji> Получено: {sold_rub}₽\n'
            f"Дата регистрации: {user.created_at.strftime('%d.%m.%Y')}",
            parse_mode="HTML"
        )


@router.callback_query(F.data == "sell_card")
async def sell_choose_card(callback: CallbackQuery, state: FSMContext):
    await state.update_data(payment_method="card")

    await callback.message.answer(
        "💳 Введите номер карты (16 цифр):"
    )
    await state.set_state(SellStates.waiting_card_number)


@router.callback_query(F.data == "sell_sbp")
async def sell_choose_sbp(callback: CallbackQuery, state: FSMContext):
    await state.update_data(payment_method="sbp")

    await callback.message.answer(
        "📱 Введите номер телефона для СБП:"
    )
    await state.set_state(SellStates.waiting_sbp_phone)


async def get_available_info():
    """Возвращает доступное количество BC для покупки и продажи"""
    balance = await bytecoin_api.get_balance()

    # Сколько можем продать (пользователь покупает у нас)
    available_to_sell = balance

    # Сколько можем купить (зависит от нашего баланса рублей)
    # Пока без лимита, или можно задать фиксированный
    available_to_buy = Decimal("1000000")  # 1 млн Bytecoin

    return available_to_sell, available_to_buy

@router.message(F.text == "О сервисе")
async def about_cmd(message: Message):
    await message.answer(
        f'<tg-emoji emoji-id="5334544901428229844">ℹ️</tg-emoji> Eyelliz Shop\n\n'
        "Сервис для покупки и продажи игровой валюты BC.\n\n"
        f'<tg-emoji emoji-id="5197572355634781614">💎</tg-emoji> Как купить BC:\n'
        "1. Нажмите 'Купить BC'\n"
        f'2. <tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Введите сумму\n'
        "3. Оплатите через СБП\n"
        f'4. <tg-emoji emoji-id="5197572355634781614">💎</tg-emoji> Получите BC\n\n'
        f'<tg-emoji emoji-id="5197572355634781614">💎</tg-emoji> Как продать BC:\n'
        "1. Нажмите 'Продать BC'\n"
        f'2. <tg-emoji emoji-id="5197572355634781614">💎</tg-emoji> Введите количество\n'
        "3. Переведите BC\n"
        f'4. <tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Получите деньги\n\n'
        "По вопросам: @EyellizSUP",
        parse_mode="HTML"
    )


@router.message(SellStates.waiting_card_number)
async def process_card_number(message: Message, state: FSMContext):
    if await handle_menu_buttons(message, state):
        return

    card_number = "".join(c for c in message.text if c.isdigit())

    if len(card_number) != 16:
        await message.answer("❌ Введите 16 цифр карты")
        return

    await state.update_data(card_number=card_number)
    await message.answer(
        "🏦 Введите банк карты:\n"
        "Например: Сбербанк, Тинькофф, ВТБ"
    )
    await state.set_state(SellStates.waiting_card_bank)


@router.message(SellStates.waiting_card_bank)
async def process_card_bank(message: Message, state: FSMContext):
    if await handle_menu_buttons(message, state):
        return

    bank = message.text.strip()
    data = await state.get_data()
    card_number = data.get("card_number", "")

    # ВСЕГДА сохраняем в БД
    await add_payment_method(
        user_id=message.from_user.id,
        method_type="card",
        card_number=card_number,
        card_bank=bank
    )

    async with async_session() as session:
        pending = PendingSell(user_id=message.from_user.id)
        session.add(pending)
        await session.commit()

    await state.update_data(card_bank=bank, card_number=card_number)

    await message.answer(
        f"💳 Карта: {card_number} ({bank})\n\n"
        f"Сохранить эти реквизиты для будущих продаж?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Сохранить",
                        callback_data="save_payment:card",
                        icon_custom_emoji_id="5215538285438311443"
                    ),
                    InlineKeyboardButton(
                        text="Нет",
                        callback_data="dont_save_payment",
                        icon_custom_emoji_id="5280803324273115630"
                    )
                ]
            ]
        )
    )


@router.callback_query(F.data.startswith("save_payment:"))
async def save_payment(callback: CallbackQuery, state: FSMContext):
    method_type = callback.data.split(":")[1]
    data = await state.get_data()

    await add_payment_method(
        user_id=callback.from_user.id,
        method_type=method_type,
        card_number=data.get("card_number") if method_type == "card" else None,
        card_bank=data.get("card_bank") if method_type == "card" else None,
        sbp_phone=data.get("sbp_phone") if method_type == "sbp" else None,
        sbp_bank=data.get("sbp_bank") if method_type == "sbp" else None
    )

    await callback.message.answer(
        f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Реквизиты сохранены!\n\n'
        f"Теперь переведите BC:\n\n"
        f'<tg-emoji emoji-id="5258334778389710253">🔢</tg-emoji> Указать количество — если хотите чтобы мы посчитали сколько будет ваша выплата.\n\n'
        f'<tg-emoji emoji-id="5463424023734014980">🔗</tg-emoji> По ссылке — переведите любую сумму, мы автоматически посчитаем выплату.\n\n'
        f"Выберите способ:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Указать количество",
                        callback_data="sell_specific_amount",
                        icon_custom_emoji_id="5258334778389710253"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Перевести по ссылке",
                        url="https://t.me/byteappbot/app?startapp=transfer-dffc2867ec8c2a106e4e87da",
                        icon_custom_emoji_id="5463424023734014980"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Я перевёл",
                        callback_data="confirm_payment",
                        icon_custom_emoji_id="5215538285438311443"
                    )
                ]
            ]
        )
    )
    await state.set_state(SellStates.waiting_amount)


@router.callback_query(F.data == "confirm_buy_payment")
async def confirm_buy_payment(callback: CallbackQuery, state: FSMContext):
    """Подтверждение оплаты при покупке BC"""

    async with async_session() as session:
        deal = await session.scalar(
            select(Deal).where(
                Deal.user_id == callback.from_user.id,
                Deal.type == "buy",
                Deal.status == "pending"
            ).order_by(Deal.created_at.desc())
        )

        if not deal:
            await callback.answer("❌ Нет активных сделок", show_alert=True)
            return

        deal.status = "checking"
        await session.commit()

        # Уведомляем админа с кнопками
        admin_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Подтвердить",
                        callback_data=f"approve_buy:{deal.id}",
                        icon_custom_emoji_id="5215538285438311443"
                    ),
                    InlineKeyboardButton(
                        text="Отклонить",
                        callback_data=f"reject_buy:{deal.id}",
                        icon_custom_emoji_id="5280803324273115630"
                    )
                ]
            ]
        )

        # Получаем пользователя
        user = await session.get(User, callback.from_user.id)
        username = f"@{html.escape(user.username)}" if user and user.username else "Нет тега"
        first_name = html.escape(user.first_name) if user and user.first_name else "Пользователь"

        notification_messages = {}

        # Уведомляем админа
        for admin_id in config.ADMIN_IDS:
            try:
                msg = await bot.send_message(
    admin_id,
    f'<tg-emoji emoji-id="5258342814273513092">🔔</tg-emoji> Новая покупка BC!\n\n'
    f'<tg-emoji emoji-id="5440457429147997980">📋</tg-emoji> Сделка: {deal.deal_number}\n'
    f'<tg-emoji emoji-id="5902335789798265487">👤</tg-emoji> Пользователь: {first_name} {username}\n'
    f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Сумма: {format_decimal(deal.rub_amount)}₽\n'
    f'<tg-emoji emoji-id="5197572355634781614">💎</tg-emoji> BC: {format_decimal(deal.coins_amount)}\n'
    f"📝 Метод: {deal.payment_method}\n\n"
    f"Проверьте оплату и подтвердите:",
    parse_mode="HTML",
    reply_markup=admin_kb
)
                notification_messages[admin_id] = msg.message_id
            except Exception as e:
                import logging
                logging.error(f"Failed to notify admin {admin_id}: {e}")
        await set_setting(f"notify_{deal.id}", json.dumps(notification_messages))
    await callback.message.answer(
        '<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Заявка отправлена на проверку!\n\n'
        '<tg-emoji emoji-id="5215277915930896212">⏳</tg-emoji> Ожидайте, пока администратор проверит оплату.\n'
        "<i> В среднем от 2-ух до 15-минут.</i>",
        parse_mode="HTML",
    )
    await callback.answer("✅ Заявка отправлена!")

@router.callback_query(F.data == "dont_save_payment")
async def dont_save_payment(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()

    # ВСЁ РАВНО сохраняем для текущей сделки
    await add_payment_method(
        user_id=callback.from_user.id,
        method_type=data.get("payment_method", "sbp"),
        card_number=data.get("card_number"),
        card_bank=data.get("card_bank"),
        sbp_phone=data.get("sbp_phone"),
        sbp_bank=data.get("sbp_bank")
    )

    # ... остальной код
    await callback.message.answer(
        f"Ок, реквизиты не сохранены.\n\n"
        f"Теперь переведите BC:\n\n"
        f'<tg-emoji emoji-id="5463424023734014980">🔗</tg-emoji> По ссылке — переведите любую сумму, мы автоматически посчитаем выплату.\n'
        f'<tg-emoji emoji-id="5258334778389710253">🔢</tg-emoji> Указать количество — если хотите знать конкретную сумму выплаты.\n\n'
        f"Выберите способ:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Перевести по ссылке",
                        url="https://t.me/byteappbot/app?startapp=transfer-dffc2867ec8c2a106e4e87da",
                        icon_custom_emoji_id="5463424023734014980"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Указать количество",
                        callback_data="sell_specific_amount",
                        icon_custom_emoji_id="5258334778389710253"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Я перевёл",
                        callback_data="confirm_payment",
                        icon_custom_emoji_id="5215538285438311443"
                    )
                ]
            ]
        )
    )
    await state.set_state(SellStates.waiting_amount)

@router.message(SellStates.waiting_sbp_phone)
async def process_sbp_phone(message: Message, state: FSMContext):
    if await handle_menu_buttons(message, state):
        return

    phone = "".join(c for c in message.text if c.isdigit())

    if len(phone) < 10:
        await message.answer(
            f'<tg-emoji emoji-id="5280803324273115630">❌</tg-emoji> Введите корректный номер телефона',
            parse_mode="HTML"
        )
        return

    await state.update_data(sbp_phone=phone)
    await message.answer(
        f'<tg-emoji emoji-id="5264895611517300926">🏦</tg-emoji> Введите банк для СБП:\n'
        "Например: Сбербанк, Тинькофф, ВТБ",
        parse_mode="HTML"
    )
    await state.set_state(SellStates.waiting_sbp_bank)


@router.message(SellStates.waiting_sbp_bank)
async def process_sbp_bank(message: Message, state: FSMContext):
    if await handle_menu_buttons(message, state):
        return

    bank = message.text.strip()
    data = await state.get_data()
    phone = data.get("sbp_phone", "")

    await add_payment_method(
        user_id=message.from_user.id,
        method_type="sbp",
        sbp_phone=phone,
        sbp_bank=bank
    )

    async with async_session() as session:
        pending = PendingSell(user_id=message.from_user.id)
        session.add(pending)
        await session.commit()

    await state.update_data(sbp_bank=bank, sbp_phone=phone)

    await message.answer(
        f'<tg-emoji emoji-id="5265074015868822600">📱</tg-emoji> СБП: {phone} ({bank})\n\n'
        f"Сохранить эти реквизиты для будущих продаж?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Сохранить",
                        callback_data="save_payment:sbp",
                        icon_custom_emoji_id="5215538285438311443"
                    ),
                    InlineKeyboardButton(
                        text="Нет",
                        callback_data="dont_save_payment",
                        icon_custom_emoji_id="5280803324273115630"
                    )
                ]
            ]
        )
    )

@router.callback_query(F.data == "add_new_payment")
async def add_new_payment(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "Выберите способ выплаты:",
        reply_markup=payment_method_sell_kb()
    )
    await state.set_state(SellStates.waiting_payment_method)

@router.callback_query(F.data == "pay_stars_after_amount")
async def pay_stars_after_amount(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "⭐ <b>Оплата звёздами</b>\n\n"
        f"Курс: <code>1 звезда = {config.STAR_PRICE_BUY}₽</code>\n"
        f"<i>Минимум: {config.STAR_MIN_AMOUNT} звёзд</i>\n\n"
        f"Введите количество звёзд:",
        parse_mode="HTML"
    )
    await state.set_state(BuyStates.waiting_stars_amount)
    await callback.answer()


@router.callback_query(BuyStates.waiting_confirm, F.data == "pay_usdt")
async def pay_with_usdt(callback: CallbackQuery, state: FSMContext):
    import crypto_instance

    usdt_enabled = await get_setting("usdt_enabled", "1")
    if usdt_enabled != "1":
        await callback.answer("❌ Оплата USDT отключена", show_alert=True)
        return

    if crypto_instance.crypto is None:
        await callback.answer("⏳ Подождите, бот загружается...", show_alert=True)
        return

    data = await state.get_data()
    amount_rub = data.get("amount_rub")
    coins_amount = data.get("coins_amount")

    if amount_rub is None:
        await callback.answer("❌ Сумма не указана", show_alert=True)
        return

    usdt_amount = amount_rub / config.USDT_RATE

    # === СОЗДАЁМ СДЕЛКУ В БД ===
    deal_number = await generate_deal_number()

    async with async_session() as session:
        deal = Deal(
            deal_number=deal_number,
            user_id=callback.from_user.id,
            type="buy",
            coins_amount=coins_amount,
            rub_amount=amount_rub,
            rate=config.RATE_SELL,
            status="pending",
            payment_method="usdt",
            idempotency_key=f"crypto-{uuid.uuid4()}"
        )
        session.add(deal)
        await session.commit()
        await session.refresh(deal)
        deal_id = deal.id  # ← Получаем ID сделки

    # === СОЗДАЁМ СЧЁТ С deal_id В PAYLOAD ===
    invoice = await crypto_instance.crypto.create_invoice(
        asset="USDT",
        amount=float(usdt_amount),
        description=f"Покупка {coins_amount:.0f} BC",
        payload=f"buy_{deal_id}_{callback.from_user.id}",  # ← deal_id + user_id
        expires_in=3600
    )

    await callback.message.answer(
        f'<tg-emoji emoji-id="5242551409232069476">💲</tg-emoji> <b>Оплата USDT</b>\n\n'
        f'<tg-emoji emoji-id="5278467510604160626">📦</tg-emoji> Получите: {coins_amount:.0f} BC\n'
        f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Сумма: {amount_rub:.2f}₽\n'
        f'<tg-emoji emoji-id="5242551409232069476">💲</tg-emoji> В USDT: {usdt_amount:.2f}\n'
        f'<tg-emoji emoji-id="5440457429147997980">📋</tg-emoji> Сделка: {deal_number}\n\n'
        f"Нажмите кнопку для оплаты:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Оплатить через CryptoBot",
                        url=invoice.bot_invoice_url,
                        icon_custom_emoji_id="5242551409232069476"
                    )
                ]
            ]
        )
    )
    await callback.answer()