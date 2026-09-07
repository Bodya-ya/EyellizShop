import os
import uuid  # Добавь в начало
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery,InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from decimal import Decimal

from bot_instance import bot
from config import config
from bytecoin_api import bytecoin_api
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from database import async_session, User, Deal, get_setting, set_setting, format_decimal
from user_kb import admin_menu_kb, main_menu_kb
from sqlalchemy import select, func
from datetime import datetime
router = Router()

class AdminStates(StatesGroup):
    waiting_rub_balance = State()
    waiting_max_buy = State()
    waiting_max_sell = State()
    waiting_rate_buy = State()
    waiting_rate_sell = State()
    waiting_min_limit = State()
    waiting_max_limit = State()
    waiting_broadcast = State()
    waiting_withdraw = State()  # ← Новое
    waiting_top_prize = State()





@router.message(Command("admin"))
async def admin_panel(message: Message, state: FSMContext):
    print(f"DEBUG: user_id={message.from_user.id}")
    print(f"DEBUG: ADMIN_IDS={config.ADMIN_IDS}")
    print(f"DEBUG: check={message.from_user.id not in config.ADMIN_IDS}")
    # Сбрасываем состояние
    await state.clear()
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔️ Недостаточно прав")
        return

    await message.answer(
        "🔑 <b>Админ-панель</b>\n\n"
        "Выберите действие:",
        reply_markup=admin_menu_kb()
    )


@router.message(F.text == "📊 Статистика")
async def admin_stats(message: Message):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    async with async_session() as session:
        # Количество пользователей
        users_count = await session.scalar(select(func.count()).select_from(User))

        # Количество сделок
        deals_count = await session.scalar(select(func.count()).select_from(Deal))

        # Завершённые сделки
        completed_deals = await session.scalar(
            select(func.count()).select_from(Deal).where(Deal.status == "completed")
        )

        await message.answer(
            f"📊 <b>Статистика</b>\n\n"
            f"Всего пользователей: {users_count}\n"
            f"Всего сделок: {deals_count}\n"
            f"Завершено сделок: {completed_deals}"
        )


@router.message(F.text == "💰 Баланс")
async def admin_balance(message: Message):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    # Получаем балансы
    rub_balance = await get_setting("rub_balance", "0")
    max_buy_rub = await get_setting("max_buy_rub", "15000")
    max_sell_coins = await get_setting("max_sell_coins", "9999999999")

    balance = await bytecoin_api.get_balance()

    await message.answer(
        "💰 Баланс и лимиты\n\n"
        f"Bytecoin: {balance:.0f}\n"
        f"Рубли (бюджет на выкуп): {rub_balance}₽\n"
        f"Макс. покупка: {max_buy_rub}₽\n"
        f"Макс. продажа: {max_sell_coins} Bytecoin\n\n"
        "Для изменения:\n"
        "/set_rub_balance [сумма] - установить баланс рублей\n"
        "/set_max_buy [сумма] - макс. выкуп в рублях\n"
        "/set_max_sell [количество] - макс. продажа в Bytecoin"
    )


@router.message(F.text == "📜 История сделок")
async def admin_history(message: Message):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    async with async_session() as session:
        deals = await session.execute(
            select(Deal).order_by(Deal.created_at.desc()).limit(20)
        )
        deals = deals.scalars().all()

        if not deals:
            await message.answer("Нет сделок")
            return

        text = "📜 <b>История сделок</b>\n\n"
        for deal in deals:
            if deal.type == "buy":
                type_text = "🟢 Покупка"
            else:
                type_text = "🔴 Продажа"

            if deal.status == "completed":
                status_text = "✅ Завершена"
            elif deal.status == "checking":
                status_text = "⏳ На проверке"
            elif deal.status == "pending":
                status_text = "🕐 Ожидает"
            else:
                status_text = "❌ Отменена"

            text += f"{type_text} | {deal.deal_number}\n"
            text += f"👤 User: <code>{deal.user_id}</code>\n"
            text += f"💎 BC: {format_decimal(deal.coins_amount)}\n"
            text += f"💰 Сумма: {format_decimal(deal.rub_amount)}₽\n"
            text += f"📝 Метод: {deal.payment_method}\n"
            text += f"Статус: {status_text}\n"
            text += "➖➖➖➖➖➖➖➖\n"

        await message.answer(text, parse_mode="HTML")

@router.message(Command("set_rub_balance"))
async def set_rub_balance_start(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    await message.answer(
        "💰 Введите новый баланс рублей (бюджет на выкуп):\n"
        "Например: 10000"
    )
    await state.set_state(AdminStates.waiting_rub_balance)


@router.message(AdminStates.waiting_rub_balance)
async def set_rub_balance_finish(message: Message, state: FSMContext):
    try:
        amount = Decimal(message.text.strip())
        if amount < 0:
            await message.answer("❌ Сумма не может быть отрицательной")
            return

        await set_setting("rub_balance", str(amount))
        await message.answer(f"✅ Баланс рублей обновлён: {amount}₽")
        await state.clear()
    except:
        await message.answer("❌ Введите корректное число")


@router.message(Command("set_max_buy"))
async def set_max_buy_start(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    await message.answer(
        "📥 Введите максимальную сумму выкупа (рублей):\n"
        "Например: 15000"
    )
    await state.set_state(AdminStates.waiting_max_buy)


@router.message(AdminStates.waiting_max_buy)
async def set_max_buy_finish(message: Message, state: FSMContext):
    try:
        amount = Decimal(message.text.strip())
        if amount < 0:
            await message.answer("❌ Сумма не может быть отрицательной")
            return

        await set_setting("max_buy_rub", str(amount))
        await message.answer(f"✅ Максимальный выкуп: {amount}₽")
        await state.clear()
    except:
        await message.answer("❌ Введите корректное число")


@router.message(Command("set_max_sell"))
async def set_max_sell_start(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    await message.answer(
        "📦 Введите максимальное количество Bytecoin для продажи:\n"
        "Например: 100000"
    )
    await state.set_state(AdminStates.waiting_max_sell)


@router.message(AdminStates.waiting_max_sell)
async def set_max_sell_finish(message: Message, state: FSMContext):
    try:
        amount = Decimal(message.text.strip())
        if amount < 0:
            await message.answer("❌ Количество не может быть отрицательным")
            return

        await set_setting("max_sell_coins", str(amount))
        await message.answer(f"✅ Максимальная продажа: {amount} Bytecoin")
        await state.clear()
    except:
        await message.answer("❌ Введите корректное число")

@router.message(F.text == "📈 Курс")
async def admin_rate(message: Message):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    await message.answer(
        f"📈 Текущие курсы\n\n"
        f"Покупка (у пользователя): 1000 Bytecoin = {config.RATE_BUY * 1000}₽\n"
        f"Продажа (пользователю): 1000 Bytecoin = {config.RATE_SELL * 1000}₽\n\n"
        f"Для изменения курса используйте команду:\n"
        f"/set_rate"
    )


@router.message(Command("withdraw_bc"))
async def withdraw_bc_start(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    await message.answer(
        "📤 Вывод BC\n\n"
        "Введите количество BC для вывода:"
    )
    await state.set_state(AdminStates.waiting_withdraw)


@router.message(AdminStates.waiting_withdraw)
async def withdraw_bc_finish(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    try:
        amount = Decimal(message.text.strip())

        # Твой Telegram ID (куда выводить)
        your_id = config.ADMIN_ID

        # Переводим BC на твой аккаунт
        result = await bytecoin_api.transfer_to_user(
            user_id=your_id,
            sum_coins=amount,
            idempotency_key=f"withdraw-{uuid.uuid4()}"
        )

        if result.get("status") == "ok":
            await message.answer(
                f"✅ Выведено: {amount:.0f} BC\n"
                f"Transaction: {result.get('transaction_id', 'N/A')[:12]}"
            )
        else:
            await message.answer(
                f"❌ Ошибка: {result.get('error', 'Unknown')}"
            )

        await state.clear()
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

@router.message(Command("set_rate"))
async def set_rate_start(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    await message.answer(
        "📈 Введите курс покупки (за 1000 Bytecoin):\n"
        "Например: 0.87"
    )
    await state.set_state(AdminStates.waiting_rate_buy)


@router.message(AdminStates.waiting_rate_buy)
async def set_rate_buy_finish(message: Message, state: FSMContext):
    try:
        rate = Decimal(message.text.strip())
        # Переводим в формат "за 1 монету"
        rate_per_coin = rate / 1000

        await state.update_data(rate_buy=rate_per_coin)
        await message.answer(
            "📈 Теперь введите курс продажи (за 1000 Bytecoin):\n"
            "Например: 1.1"
        )
        await state.set_state(AdminStates.waiting_rate_sell)
    except:
        await message.answer("❌ Введите корректное число")


@router.message(AdminStates.waiting_rate_sell)
async def set_rate_sell_finish(message: Message, state: FSMContext):
    try:
        rate = Decimal(message.text.strip())
        rate_per_coin = rate / 1000

        data = await state.get_data()
        rate_buy = data.get("rate_buy")

        config.RATE_BUY = rate_buy
        config.RATE_SELL = rate_per_coin

        await message.answer(
            f"✅ Курсы обновлены!\n\n"
            f"Покупка: 1000 Bytecoin = {rate_buy * 1000}₽\n"
            f"Продажа: 1000 Bytecoin = {rate_per_coin * 1000}₽"
        )
        await state.clear()
    except:
        await message.answer("❌ Введите корректное число")


@router.message(F.text == "👥 Пользователи")
async def admin_users(message: Message):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    async with async_session() as session:
        users = await session.execute(
            select(User).order_by(User.created_at.desc()).limit(10)
        )
        users = users.scalars().all()

        if not users:
            await message.answer("Нет пользователей")
            return

        text = "👥 <b>Последние 10 пользователей:</b>\n\n"
        for user in users:
            text += f"ID: {user.telegram_id}\n"
            text += f"Имя: {user.first_name}\n"
            text += f"Куплено: {user.total_bought_coins:.3f} BCN\n"
            text += f"Продано: {user.total_sold_coins:.3f} BCN\n"
            text += "➖➖➖➖➖➖➖➖\n"

        await message.answer(text)


@router.message(F.text == "📋 Сделки")
async def admin_deals(message: Message):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    async with async_session() as session:
        deals = await session.execute(
            select(Deal).order_by(Deal.created_at.desc()).limit(10)
        )
        deals = deals.scalars().all()

        if not deals:
            await message.answer("Нет сделок")
            return

        text = "📋 <b>Последние 10 сделок:</b>\n\n"
        for deal in deals:
            status_emoji = "✅" if deal.status == "completed" else "⏳"
            text += f"{status_emoji} {deal.type.upper()} | {deal.rub_amount}₽ | {deal.coins_amount:.3f} BCN\n"
            text += f"Статус: {deal.status}\n"
            text += f"ID: {deal.deal_number}\n"
            text += "➖➖➖➖➖➖➖➖\n"

        await message.answer(text)


@router.callback_query(F.data.startswith("approve_deal:"))
async def approve_deal(callback: CallbackQuery):
    """Админ подтверждает сделку"""
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("⛔️ Недостаточно прав", show_alert=True)
        return

    deal_id = callback.data.split(":")[1]

    async with async_session() as session:
        deal = await session.get(Deal, int(deal_id))

        if not deal:
            await callback.answer("❌ Сделка не найдена", show_alert=True)
            return

        if deal.status != "checking":
            await callback.answer("❌ Сделка не на проверке", show_alert=True)
            return

        try:
            # Отправляем монеты пользователю через API
            result = await bytecoin_api.transfer_to_user(
                user_id=deal.user_id,
                sum_coins=deal.coins_amount,
                idempotency_key=deal.idempotency_key
            )

            if result.get("status") == "ok":
                # Обновляем сделку
                deal.status = "completed"
                deal.transaction_id = result.get("transaction_id")
                deal.completed_at = datetime.utcnow()

                # Обновляем статистику пользователя
                user = await session.get(User, deal.user_id)
                if user:
                    user.total_bought_coins = (user.total_bought_coins or 0) + deal.coins_amount
                    user.total_bought_rub = (user.total_bought_rub or 0) + deal.rub_amount

                await session.commit()

                # Уведомляем админа
                await callback.message.edit_text(
                    f"✅ <b>Сделка подтверждена!</b>\n\n"
                    f"ID: <code>{deal.deal_number}</code>\n"
                    f"Монеты отправлены пользователю\n"
                    f"Transaction: <code>{result.get('transaction_id', 'N/A')}</code>"
                )

                # Уведомляем пользователя
                from main import bot
                await bot.send_message(
                    deal.user_id,
                    f"✅ <b>Оплата подтверждена!</b>\n\n"
                    f"Вы получили: {deal.coins_amount:.3f} BCN\n"
                    f"Сумма: {deal.rub_amount}₽\n"
                    f"ID транзакции: <code>{result.get('transaction_id', 'N/A')[:12]}</code>"
                )

            else:
                await callback.answer(
                    f"❌ Ошибка API: {result.get('error', 'Unknown')}",
                    show_alert=True
                )

        except Exception as e:
            await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@router.message(Command("set_limits"))
async def set_limits_start(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    await message.answer(
        "📏 Введите минимальную сумму сделки (рублей):\n"
        "Например: 25"
    )
    await state.set_state(AdminStates.waiting_min_limit)


@router.message(AdminStates.waiting_min_limit)
async def set_min_limit(message: Message, state: FSMContext):
    try:
        min_limit = Decimal(message.text.strip())
        if min_limit < 0:
            await message.answer("❌ Сумма не может быть отрицательной")
            return

        await state.update_data(min_limit=min_limit)
        await message.answer(
            "📏 Теперь введите максимальную сумму сделки (рублей):\n"
            "Например: 15000"
        )
        await state.set_state(AdminStates.waiting_max_limit)
    except:
        await message.answer("❌ Введите корректное число")


@router.message(AdminStates.waiting_max_limit)
async def set_max_limit(message: Message, state: FSMContext):
    try:
        max_limit = Decimal(message.text.strip())
        if max_limit < 0:
            await message.answer("❌ Сумма не может быть отрицательной")
            return

        data = await state.get_data()
        min_limit = data.get("min_limit")

        config.MIN_DEAL_RUB = min_limit
        config.MAX_DEAL_RUB = max_limit

        await message.answer(
            f"✅ Лимиты обновлены!\n\n"
            f"Минимальная сумма: {min_limit}₽\n"
            f"Максимальная сумма: {max_limit}₽"
        )
        await state.clear()
    except:
        await message.answer("❌ Введите корректное число")


@router.message(Command("broadcast"))
async def broadcast_start(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    await message.answer(
        "📢 Введите текст для рассылки:\n"
        "Он будет отправлен всем пользователям бота."
    )
    await state.set_state(AdminStates.waiting_broadcast)


@router.message(AdminStates.waiting_broadcast)
async def broadcast_send(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    text = message.text

    # Получаем всех пользователей
    async with async_session() as session:
        users = await session.execute(select(User))
        users = users.scalars().all()

        success = 0
        failed = 0

        from bot_instance import bot

        for user in users:
            try:
                await bot.send_message(user.telegram_id, text)
                success += 1
            except:
                failed += 1

        await message.answer(
            f"📢 Рассылка завершена!\n\n"
            f"✅ Отправлено: {success}\n"
            f"❌ Ошибок: {failed}"
        )
        await state.clear()

@router.message(F.text == "📋 Активные сделки")
async def admin_active_deals(message: Message):

    """Показывает сделки на проверке"""
    if message.from_user.id not in config.ADMIN_IDS:
        return

    async with async_session() as session:
        deals = await session.execute(
            select(Deal).where(
                Deal.status == "checking",
                Deal.idempotency_key.like("buy-%")  # Только сделки из бота
            ).order_by(Deal.created_at.desc()).limit(10)
        )
        deals = deals.scalars().all()

        if not deals:
            await message.answer("✅ Нет активных сделок")
            return

        for deal in deals:
            kb = InlineKeyboardMarkup(
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

            user = await session.get(User, deal.user_id)  # ← Вот!
            username = f"@{user.username}" if user and user.username else "Нет тега"
            first_name = user.first_name if user and user.first_name else "Пользователь"

            await message.answer(
                f"📋 <b>Сделка {deal.deal_number}</b>\n\n"
                f"👤 Чел: <code>{first_name} aka. {username}\n</code>\n"
                f"💰 Сумма: {deal.rub_amount}₽\n"
                f"💎 BC: {deal.coins_amount:.0f}\n"
                f"📝 Метод: {deal.payment_method}\n"
                f"⏰ Создана: {deal.created_at.strftime('%H:%M')}",
                parse_mode="HTML",
                reply_markup=kb
            )


@router.callback_query(F.data.startswith("approve_sell:"))
async def approve_sell_deal(callback: CallbackQuery):
    """Админ подтверждает выплату за продажу BC"""
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("⛔️ Недостаточно прав", show_alert=True)
        return

    deal_id = callback.data.split(":")[1]

    async with async_session() as session:
        deal = await session.get(Deal, int(deal_id))

        if not deal:
            await callback.answer("❌ Сделка не найдена", show_alert=True)
            return

        deal.status = "completed"
        deal.completed_at = datetime.utcnow()
        await session.commit()

        await callback.message.edit_text(
            f"✅ Сделка {deal.deal_number} подтверждена!\n"
            f"Выплата произведена."
        )

        await bot.send_message(
            deal.user_id,
            f"✅ Выплата произведена!\n\n"
            f"📋 Сделка: {deal.deal_number}\n"
            f"💰 Вы получили: {deal.rub_amount:.2f}₽"
        )

        await callback.answer("✅ Подтверждено!")


@router.callback_query(F.data.startswith("reject_sell:"))
async def reject_sell_deal(callback: CallbackQuery):
    """Админ отклоняет продажу"""
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("⛔️ Недостаточно прав", show_alert=True)
        return

    deal_id = callback.data.split(":")[1]

    async with async_session() as session:
        deal = await session.get(Deal, int(deal_id))

        if not deal:
            await callback.answer("❌ Сделка не найдена", show_alert=True)
            return

        deal.status = "cancelled"
        await session.commit()

        await callback.message.edit_text(
            f"❌ Сделка {deal.deal_number} отклонена"
        )

        await bot.send_message(
            deal.user_id,
            f"❌ Выплата отклонена\n\n"
            f"📋 Сделка: {deal.deal_number}\n"
            f"Обратитесь в поддержку: @EyellizSUP"
        )

        await callback.answer("Отклонено")

@router.callback_query(F.data.startswith("reject_deal:"))
async def reject_deal(callback: CallbackQuery):
    """Админ отклоняет сделку"""
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("⛔️ Недостаточно прав", show_alert=True)
        return

    deal_id = callback.data.split(":")[1]

    async with async_session() as session:
        deal = await session.get(Deal, deal_id)

        if not deal:
            await callback.answer("❌ Сделка не найдена", show_alert=True)
            return

        deal.status = "cancelled"
        await session.commit()

        # Уведомляем админа
        await callback.message.edit_text(
            f"❌ <b>Сделка отклонена</b>\n\n"
            f"ID: <code>{deal.deal_number}</code>\n"
            f"Сумма: {deal.rub_amount}₽"
        )

        # Уведомляем пользователя
        from bot_instance import bot
        await bot.send_message(
            deal.user_id,
            f"❌ <b>Оплата не подтверждена</b>\n\n"
            f"ID сделки: <code>{deal.deal_number}</code>\n"
            f"Сумма: {deal.rub_amount}₽\n\n"
            "Если вы оплатили, обратитесь в поддержку.\n\n"
            "Support : @EyellizSUP"
        )

        await callback.answer("Сделка отклонена", show_alert=True)

@router.message(F.text == "🔧 Настройки")
async def admin_settings(message: Message):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    await message.answer(
        "🔧 Настройки\n\n"
        f"Минимальная сумма: {config.MIN_DEAL_RUB}₽\n"
        f"Максимальная сумма: {config.MAX_DEAL_RUB}₽\n\n"
        "Команды:\n"
        "/set_rub_balance - баланс рублей\n"
        "/set_max_buy - макс. выкуп\n"
        "/set_max_sell - макс. продажа\n"
        "/set_limits - лимиты сделок\n"
        "/set_rate - курсы\n"
        "/broadcast - рассылка"
    )


@router.message(F.text == "⬅️ В меню")
async def back_to_menu(message: Message):
    await message.answer(
        "Главное меню:",
        reply_markup=main_menu_kb()
    )


@router.callback_query(F.data.startswith("approve_buy:"))
async def approve_buy_deal(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("⛔️ Недостаточно прав", show_alert=True)
        return

    deal_id = callback.data.split(":")[1]

    async with async_session() as session:
        deal = await session.get(Deal, int(deal_id))

        if not deal:
            await callback.answer("❌ Сделка не найдена", show_alert=True)
            return

        try:
            result = await bytecoin_api.transfer_to_user(
                user_id=int(deal.user_id),
                sum_coins=Decimal(deal.coins_amount),
                idempotency_key=f"approve-buy-{deal.id}-{datetime.utcnow().timestamp()}"
            )

            if result.get("status") == "ok":
                deal.status = "completed"
                deal.transaction_id = result.get("transaction_id")
                deal.completed_at = datetime.utcnow()

                user = await session.get(User, deal.user_id)
                if user:
                    user.total_bought_coins = (user.total_bought_coins or 0) + deal.coins_amount
                    user.total_bought_rub = (user.total_bought_rub or 0) + deal.rub_amount

                await session.commit()

                # === ВЫЧИТАЕМ ИЗ РЕЗЕРВА ===
                rub_balance = Decimal(await get_setting("rub_balance", "0"))
                new_rub_balance = rub_balance - Decimal(deal.rub_amount)
                await set_setting("rub_balance", str(new_rub_balance))
                # ==========================

                await callback.message.edit_text(
                    f"✅ Сделка {deal.deal_number} подтверждена!\n"
                    f"BC начислены пользователю.\n"
                    f"Резерв уменьшен на {deal.rub_amount}₽"
                )

                await bot.send_message(
                    deal.user_id,
                    f"✅ Оплата подтверждена!\n\n"
                    f"📋 Сделка: {deal.deal_number}\n"
                    f"💎 Вы получили: {deal.coins_amount:.0f} BC\n"
                    f"Transaction: {result.get('transaction_id', 'N/A')[:12]}"
                )

                await callback.answer("✅ BC начислены!")
            else:
                error_text = result.get("error", "Unknown")
                await callback.answer(
                    f"❌ Ошибка API: {error_text}",
                    show_alert=True
                )

        except Exception as e:
            await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)

@router.message(Command("set_top_prize"))
async def set_top_prize_start(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return
    await message.answer("🎁 Введите текст приза для топа:")
    await state.set_state(AdminStates.waiting_top_prize)

@router.message(AdminStates.waiting_top_prize)
async def set_top_prize_finish(message: Message, state: FSMContext):
    await set_setting("top_prize", message.text)
    await message.answer("✅ Приз обновлён!")
    await state.clear()

@router.callback_query(F.data.startswith("reject_buy:"))
async def reject_buy_deal(callback: CallbackQuery):
    """Админ отклоняет покупку"""
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("⛔️ Недостаточно прав", show_alert=True)
        return

    deal_id = callback.data.split(":")[1]

    async with async_session() as session:
        deal = await session.get(Deal, int(deal_id))

        if not deal:
            await callback.answer("❌ Сделка не найдена", show_alert=True)
            return

        deal.status = "cancelled"
        await session.commit()

        await callback.message.edit_text(
            f"❌ Сделка {deal.deal_number} отклонена"
        )

        await bot.send_message(
            deal.user_id,
            f"❌ Оплата не подтверждена\n\n"
            f"📋 Сделка: {deal.deal_number}\n"
            f"Обратитесь в поддержку.\n\n"
            "Support : @EyellizSUP"
        )

        await callback.answer("Сделка отклонена")