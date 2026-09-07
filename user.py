import os
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, PreCheckoutQuery, ContentType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from decimal import Decimal, InvalidOperation
import uuid
from bytecoin_api import bytecoin_api
from datetime import datetime  # ← Для datetime
import uuid
from bot_instance import bot  # ← Для bot
from sqlalchemy import select, func  # ← Для select
from database import async_session, User, Deal, format_decimal, get_setting, set_setting, PaymentMethod, PendingSell
from config import config
from user_kb import main_menu_kb, payment_method_sell_kb, confirm_kb, saved_payments_kb, payment_method_buy_kb

import logging
logger = logging.getLogger(__name__)

router = Router()

MAIN_MENU_BUTTONS = [
    "Купить BC 💎",
    "Продать BC 💎",
    "📊 Курс и лимиты",
    "🏆 Топ покупателей",  # ← Добавь
    "👤 Мой профиль",
    "ℹ️ О сервисе"
]

class BuyStates(StatesGroup):
    waiting_amount = State()
    waiting_confirm = State()
    waiting_stars_amount = State()  # ← Для ввода звёзд





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
        "⏳ Ожидаем подтверждение перевода...\n\n"
        "Как только BC поступят на сервис, сделка будет создана автоматически."
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
    # Отправляем сообщение пользователю
    if payment_method == "stars":
        stars = rub_amount / Decimal("1.63")
        payment_text = f"⭐ Вы получите: {stars:.0f} звёзд"
    else:
        payment_text = f"💰 Вы получите: {rub_amount:.2f}₽"

    text = (
        f"✅ Сделка создана!\n\n"
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
    if message.text.startswith(("/start", "/admin")):
        await state.clear()
        if message.text == "/start":
            await cmd_start(message, state)
        elif message.text == "/admin":
            # Импортируем admin_panel
            from admin import admin_panel
            await admin_panel(message, state)
        return True
    """Проверяет, нажал ли пользователь кнопку меню. Возвращает True, если обработано"""
    if message.text in MAIN_MENU_BUTTONS:
        await state.clear()

        if message.text == "Купить BC 💎":
            await buy_bytecoin(message, state)
        elif message.text == "Продать BC 💎":
            await sell_bytecoin(message, state)
        elif message.text == "📊 Курс и лимиты":
            await show_rates_and_limits(message)
        elif message.text == "👤 Мой профиль":
            await my_profile(message)
        elif message.text == "🏆 Топ покупателей":
            await top_buyers(message)
        elif message.text == "ℹ️ О сервисе":
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
        "👋 <b>Добро пожаловать в Eyelliz Shop!</b>\n\n"
        "Здесь вы можете купить или продать BC 💎\n\n"
        "💰 <b>Наши курсы:</b>\n"
        f"📈 <b>Купить:</b> <code>{format_rate(config.RATE_SELL)}₽ / 1000 BC</code>\n"
        f"📉 <b>Продать:</b> <code>{format_rate(config.RATE_BUY)}₽ / 1000 BC</code>\n\n"
        "Выберите действие:",
        parse_mode="HTML",
        reply_markup=main_menu_kb()
    )


@router.message(F.text == "Купить BC 💎")
async def buy_bytecoin(message: Message, state: FSMContext):
    balance = await get_cached_balance()
    max_sell = Decimal(await get_setting("max_sell_coins", "9999999999"))
    available = min(balance, max_sell)
    available_rub = available * config.RATE_SELL

    if available < 1:
        await message.answer(
            "⚠️ Недостаточно BC для продажи.\n"
            f"Доступно: {available:.0f} BC"
        )
        return

    await message.answer(
        "💎 <b>Покупка BC</b>\n\n"
        f"📈 Курс: <code>1000 BC = {config.RATE_SELL * 1000:.2f}₽</code>\n\n"
        f"📦 Доступно: {format_num(available)} BC\n\n"
        f"✅ Выберите способ оплаты:\n",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="💳 СБП", callback_data="pay_sbp")
                ],
                [
                    InlineKeyboardButton(text="⭐ ТГ Звёзды (минимум 10)", callback_data="pay_stars")
                ]
            ]
        )
    )
    await state.set_state(BuyStates.waiting_confirm)


@router.message(F.text == "📊 Курс и лимиты")
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
        "📊 <b>Информация</b>\n\n"
        f"📈 <b>Купить BC:</b> <code>{format_rate(config.RATE_SELL)}₽ / 1000 BC</code>\n"
        f"📉 <b>Продать BC:</b> <code>{format_rate(config.RATE_BUY)}₽ / 1000 BC</code>\n\n"
        f"⭐ <b>Звёзды при покупке:</b> <code>1 звезда = {config.STAR_PRICE_BUY}₽</code>\n"
        f"<i>Минимум: {config.STAR_MIN_AMOUNT} звёзд</i>\n\n"
        f"📦 <b>Продадим:</b> <code>{format_num(available_to_sell_coins)} BC</code>\n"
        f"💸 <b>Выкупим:</b> <code>{format_num(available_to_buy_coins)} BC</code>\n"
        f"💰 <b>Резерв:</b> <code>{format_num(available_to_buy_rub)}₽</code>"
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
        f"Переведите BC через ссылку:\n"
        f"https://t.me/byteappbot/app?startapp=transfer-dffc2867ec8c2a106e4e87da\n\n"
        f"После перевода нажмите '✅ Я перевёл'",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Я перевёл", callback_data="confirm_sell"),
                    InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_sell")
                ]
            ]
        )
    )
    await state.set_state(SellStates.waiting_transfer)

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
        # Парсим сумму с поддержкой "к" и "кк"
        amount_rub = parse_amount(message.text)

        if amount_rub < config.MIN_DEAL_RUB:
            await message.answer(f"❌ Минимальная сумма: {config.MIN_DEAL_RUB}₽")
            return

        if amount_rub > config.MAX_DEAL_RUB:
            await message.answer(f"❌ Максимальная сумма: {config.MAX_DEAL_RUB}₽")
            return

        # Проверяем доступный баланс BC
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

        await message.answer(
            f"✅ <b>Проверьте детали:</b>\n\n"
            f"💰 Сумма: {amount_rub:.2f}₽\n"
            f"💎 Получите: {coins_amount:.0f} BC\n"
            f"📈 Курс: 1000 BC = {config.RATE_SELL * 1000:.2f}₽\n\n"
            f"Всё верно?",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ Продолжить", callback_data="continue_buy"),
                        InlineKeyboardButton(text="🔄 Изменить сумму", callback_data="change_buy_amount")
                    ]
                ]
            )
        )
        await state.set_state(BuyStates.waiting_confirm)

    except (ValueError, InvalidOperation):
        await message.answer("❌ Введите сумму в рублях (просто число)")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

@router.callback_query(F.data == "cancel_sell")
async def cancel_sell_transfer(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Продажа отменена")


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

    await callback.message.answer(
        f"💳 <b>Оплата по СБП</b>\n\n"
        f"💰 Сумма: {amount_rub:.2f}₽\n"
        f"💎 Получите: {coins_amount:.0f} BC\n"
        f"📋 Сделка: {deal_number}\n\n"
        f"‼️<code>+7 958 238-99-88</code> (🅰️АЛЬФА-БАНК)‼️\n\n"
        f"⏰ Перевести в течении часа!\n\n"
        f"После оплаты нажмите:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Я оплатил", callback_data="confirm_buy_payment")
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
        f"🔢 Укажите количество BC\n\n"
        f"🟠 Максимум: {format_num(max_can_sell)} BC\n\n"
        f"<i>Можно: <code>1кк</code>,<code>100к</code>,<code>2кк</code></i>\n\n"
        f"💎 Введите количество:",
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
            f"✅ <b>Проверьте:</b>\n\n"
            f"⭐ Звёзд: {stars_amount}\n"
            f"💰 Эквивалент: {rub_amount:.2f}₽\n"
            f"💎 Получите: {coins_amount:.0f} BC\n\n"
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

@router.message(F.text == "Продать BC 💎")
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

    await message.answer(
        "💰 Продажа BC\n\n"
        f"📉 Курс: <code>1000 BC = {config.RATE_BUY * 1000:.2f}₽</code>\n\n"
        f"📦 Ваш баланс: {format_num(user_balance)} BC\n\n"
        f"💸 Мы можем выкупить до: <code>{format_num(available_coins)}</code> BC\n"
        f"💵 Минимум: <code>{config.MIN_DEAL_RUB}₽</code> | Максимум: <code>{config.MAX_DEAL_RUB}₽</code>\n\n"
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
            f"⭐ <b>Оплата звёздами!</b>\n\n"
            f"📋 Сделка: {deal_number}\n"
            f"👤 User: <code>{message.from_user.id}</code>\n"
            f"⭐ Звёзд: {stars_amount:.0f}\n"
            f"💰 Рублей: {rub_amount:.2f}₽\n"
            f"💎 BC начислено: {coins_amount:.0f}\n"
            f"✅ Статус: Завершена",
            parse_mode="HTML"
        )

        await message.answer(
            f"✅ Оплата звёздами успешна!\n\n"
            f"📋 Сделка: {deal_number}\n"
            f"⭐ Потрачено: {stars_amount:.0f} звёзд\n"
            f"💎 Вы получили: {coins_amount:.0f} BC"
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
                f"✅ Выбрано: {payment_info}\n\n"
                f"Переведите BC любым для вас удобным методом:\n\n"
                f"🔢 Указать количество — если хотите узнать сумму выплаты в рублях.\n\n"
                f"🔗 По ссылке — переведите любую сумму, мы автоматически посчитаем выплату.\n\n"
                f"После перевода нажмите '✅ Я перевёл'",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(text="🔢 Указать количество", callback_data="sell_specific_amount")
                        ],
                        [
                            InlineKeyboardButton(
                                text="🔗 Перевести по ссылке",
                                url="https://t.me/byteappbot/app?startapp=transfer-dffc2867ec8c2a106e4e87da"
                            )
                        ],
                        [
                            InlineKeyboardButton(text="✅ Я перевёл", callback_data="confirm_payment")
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
        if rub_amount < config.MIN_DEAL_RUB:
            min_coins = config.MIN_DEAL_RUB / config.RATE_BUY
            await message.answer(
                f"❌ Минимальная сумма: {config.MIN_DEAL_RUB}₽\n"
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
            f"📤 Перевод BC\n\n"
            f"💎 Количество: {format_num(coins_amount)} BC\n"
            f"💰 Вы получите: {rub_amount:.2f}₽\n\n"
            f"🟠 Переведите BC по ссылке:\n"
            f"https://t.me/byteappbot/app?startapp=transfer-dffc2867ec8c2a106e4e87da\n\n"
            f"После перевода нажмите '✅ Я перевёл'",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🔗 Перевести BC",
                            url="https://t.me/byteappbot/app?startapp=transfer-dffc2867ec8c2a106e4e87da"
                        )
                    ],
                    [
                        InlineKeyboardButton(text="✅ Я перевёл", callback_data="confirm_payment"),
                        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_deal")
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
        "⏳ Ожидаем подтверждение перевода...\n\n"
        "Как только BC поступят на сервис, сделка будет создана автоматически."
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
            await callback.message.edit_text("❌ Сделка отменена")
        else:
            await callback.answer("Нет активных сделок", show_alert=True)

@router.message(F.text == "🏆 Топ покупателей")
async def top_buyers(message: Message):
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.total_bought_coins > 0).order_by(User.total_bought_coins.desc()).limit(8)
        )
        users = result.scalars().all()

        if not users:
            return  # Просто ничего не показываем

        text = "🏆 Топ покупателей\n\n"
        for i, user in enumerate(users, 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            name = user.first_name or "Пользователь"
            bought = format_decimal(user.total_bought_coins)
            text += f"{medal} {name}: {bought} BC\n"

        prize = await get_setting("top_prize", "")
        if prize:
            text += f"\n🎁 Приз: {prize}\n"

        await message.answer(text)

@router.message(F.text == "👤 Мой профиль")
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
            f"👤 Мой профиль\n\n"
            f"📈 Всего куплено: {bought_coins} BC\n"
            f"📉 Всего продано: {sold_coins} BC\n"
            f"💰 Потрачено: {bought_rub}₽\n"
            f"💸 Получено: {sold_rub}₽\n"
            f"Дата регистрации: {user.created_at.strftime('%d.%m.%Y')}"
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

@router.message(F.text == "ℹ️ О сервисе")
async def about_cmd(message: Message):
    await message.answer(
        "ℹ️ Eyelliz Shop\n\n"
        "Сервис для покупки и продажи игровой валюты BC.\n\n"
        "Как купить Bytecoin:\n"
        "1. Нажмите 'Купить BC 💎 '\n"
        "2.💰 Введите сумму  \n"
        "3. Оплатите через СБП\n"
        "4.💎 Получите Bytecoin\n\n"
        "Как продать Bytecoin:\n"
        "1. Нажмите 'Продать BC 💎 '\n"
        "2. 💎 Введите количество\n"
        "3. Переведите Bytecoin\n"
        "4.💰 Получите деньги\n\n"
        "По вопросам: @EyellizSUP"
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
                    InlineKeyboardButton(text="✅ Сохранить", callback_data="save_payment:card"),
                    InlineKeyboardButton(text="❌ Нет", callback_data="dont_save_payment")
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
        f"✅ Реквизиты сохранены!\n\n"
        f"Теперь переведите BC:\n\n"
        f"🔢 Указать количество — если хотите чтобы мы посчитали сколько будет ваша выплата.\n\n"
        f"🔗 По ссылке — переведите любую сумму, мы автоматически посчитаем выплату.\n\n"
        f"Выберите способ:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔢 Указать количество", callback_data="sell_specific_amount")
                ],
                [
                    InlineKeyboardButton(
                        text="🔗 Перевести по ссылке",
                        url="https://t.me/byteappbot/app?startapp=transfer-dffc2867ec8c2a106e4e87da"
                    )
                ],
                [
                    InlineKeyboardButton(text="✅ Я перевёл", callback_data="confirm_payment")
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
                        text="✅ Подтвердить",
                        callback_data=f"approve_buy:{deal.id}"
                    ),
                    InlineKeyboardButton(
                        text="❌ Отклонить",
                        callback_data=f"reject_buy:{deal.id}"
                    )
                ]
            ]
        )

        # Получаем пользователя
        user = await session.get(User, callback.from_user.id)
        username = f"@{user.username}" if user and user.username else "Нет тега"
        first_name = user.first_name if user and user.first_name else "Пользователь"

        # Уведомляем админа
        for admin_id in config.ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"🔔 Новая покупка BC!\n\n"
                    f"📋 Сделка: {deal.deal_number}\n"
                    f"👤 Пользователь: {first_name} {username}\n"
                    f"💰 Сумма: {format_decimal(deal.rub_amount)}₽\n"
                    f"💎 BC: {format_decimal(deal.coins_amount)}\n"
                    f"📝 Метод: {deal.payment_method}\n\n"
                    f"Проверьте оплату и подтвердите:",
                    reply_markup=admin_kb
                )
            except Exception as e:
                logger.error(f"Failed to send to admin {admin_id}: {e}")

    await callback.message.answer(
        "✅ Заявка отправлена на проверку!\n\n"
        "⏰ Ожидайте, пока администратор проверит оплату.\n"
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
        f"🔗 По ссылке — переведите любую сумму, мы автоматически посчитаем выплату.\n"
        f"🔢 Указать количество — если хотите знать конкретную сумму выплаты.\n\n"
        f"Выберите способ:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔗 Перевести по ссылке",
                        url="https://t.me/byteappbot/app?startapp=transfer-dffc2867ec8c2a106e4e87da"
                    )
                ],
                [
                    InlineKeyboardButton(text="🔢 Указать количество", callback_data="sell_specific_amount")
                ],
                [
                    InlineKeyboardButton(text="✅ Я перевёл", callback_data="confirm_payment")
                ]
            ]
        )
    )
    await state.set_state(SellStates.waiting_amount)

@router.message(SellStates.waiting_sbp_phone)
async def process_sbp_phone(message: Message, state: FSMContext):

    # ...
    if await handle_menu_buttons(message, state):
        return

    phone = "".join(c for c in message.text if c.isdigit())

    if len(phone) < 10:
        await message.answer("❌ Введите корректный номер телефона")
        return

    await state.update_data(sbp_phone=phone)
    await message.answer(
        "🏦 Введите банк для СБП:\n"
        "Например: Сбербанк, Тинькофф, ВТБ"
    )
    await state.set_state(SellStates.waiting_sbp_bank)


@router.message(SellStates.waiting_sbp_bank)
async def process_sbp_bank(message: Message, state: FSMContext):
    if await handle_menu_buttons(message, state):
        return

    bank = message.text.strip()
    data = await state.get_data()
    phone = data.get("sbp_phone", "")

    # СОХРАНЯЕМ В БД
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
        f"📱 СБП: {phone} ({bank})\n\n"
        f"Сохранить эти реквизиты для будущих продаж?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Сохранить", callback_data="save_payment:sbp"),
                    InlineKeyboardButton(text="❌ Нет", callback_data="dont_save_payment")
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