import os
import uuid
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from decimal import Decimal
import html
from bot_instance import bot
from config import config
from bytecoin_api import bytecoin_api
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from database import async_session, User, Deal, get_setting, set_setting, format_decimal, HiddenUser
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
    waiting_withdraw = State()
    waiting_top_prize = State()
    waiting_balance_threshold = State()
    waiting_min_sell = State()
    waiting_hide_user = State()
    waiting_requisites = State()


@router.message(Command("admin"))
async def admin_panel(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔️ Недостаточно прав")
        return

    await message.answer(
        '🔑 <b>Админ-панель</b>\n\n'
        "Выберите действие:",
        parse_mode="HTML",
        reply_markup=admin_menu_kb()
    )


@router.message(F.text == "📊 Статистика")
async def admin_stats(message: Message):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    async with async_session() as session:
        users_count = await session.scalar(select(func.count()).select_from(User))
        deals_count = await session.scalar(select(func.count()).select_from(Deal))
        completed_deals = await session.scalar(
            select(func.count()).select_from(Deal).where(Deal.status == "completed")
        )

        await message.answer(
            f'<tg-emoji emoji-id="5260742580005530450">📊</tg-emoji> <b>Статистика</b>\n\n'
            f"Всего пользователей: {users_count}\n"
            f"Всего сделок: {deals_count}\n"
            f"Завершено сделок: {completed_deals}",
            parse_mode="HTML"
        )


@router.message(F.text == "💰 Баланс")
async def admin_balance(message: Message):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    rub_balance = await get_setting("rub_balance", "0")
    max_buy_rub = await get_setting("max_buy_rub", "15000")
    max_sell_coins = await get_setting("max_sell_coins", "9999999999")

    balance = await bytecoin_api.get_balance()

    await message.answer(
        f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Баланс и лимиты\n\n'
        f"Bytecoin: {balance:.0f}\n"
        f"Рубли (бюджет на выкуп): {rub_balance}₽\n"
        f"Макс. покупка: {max_buy_rub}₽\n"
        f"Макс. продажа: {max_sell_coins} Bytecoin\n\n"
        "Для изменения:\n"
        "/set_rub_balance [сумма] - установить баланс рублей\n"
        "/set_max_buy [сумма] - макс. выкуп в рублях\n"
        "/set_max_sell [количество] - макс. продажа в Bytecoin",
        parse_mode="HTML"
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

        text = f'<tg-emoji emoji-id="5440457429147997980">📋</tg-emoji> <b>История сделок</b>\n\n'
        for deal in deals:
            if deal.type == "buy":
                type_text = "🟢 Покупка"
            else:
                type_text = "🔴 Продажа"

            if deal.status == "completed":
                status_text = '<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Завершена'
            elif deal.status == "checking":
                status_text = '<tg-emoji emoji-id="5215277915930896212">⏳</tg-emoji> На проверке'
            elif deal.status == "pending":
                status_text = "🕐 Ожидает"
            else:
                status_text = '<tg-emoji emoji-id="5280803324273115630">❌</tg-emoji> Отменена'

            text += f"{type_text} | {deal.deal_number}\n"
            text += f'<tg-emoji emoji-id="5902335789798265487">👤</tg-emoji> User: <code>{deal.user_id}</code>\n'
            text += f'<tg-emoji emoji-id="5197572355634781614">💎</tg-emoji> BC: {format_decimal(deal.coins_amount)}\n'
            text += f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Сумма: {format_decimal(deal.rub_amount)}₽\n'
            text += f"📝 Метод: {deal.payment_method}\n"
            text += f"Статус: {status_text}\n"
            text += "➖➖➖➖➖➖➖➖\n"

        await message.answer(text, parse_mode="HTML")


@router.message(Command("set_rub_balance"))
async def set_rub_balance_start(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    await message.answer(
        '<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Введите новый баланс рублей (бюджет на выкуп):\n'
        "Например: 10000",
        parse_mode="HTML"
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
        await message.answer(
            f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Баланс рублей обновлён: {amount}₽',
            parse_mode="HTML"
        )
        await state.clear()
    except:
        await message.answer("❌ Введите корректное число")


@router.message(Command("set_max_buy"))
async def set_max_buy_start(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    await message.answer(
        '<tg-emoji emoji-id="5443127283898405358">📥</tg-emoji> Введите максимальную сумму выкупа (рублей):\n'
        "Например: 15000",
        parse_mode="HTML"
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
        await message.answer(
            f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Максимальный выкуп: {amount}₽',
            parse_mode="HTML"
        )
        await state.clear()
    except:
        await message.answer("❌ Введите корректное число")


@router.message(Command("set_max_sell"))
async def set_max_sell_start(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    await message.answer(
        '<tg-emoji emoji-id="5278467510604160626">📦</tg-emoji> Введите максимальное количество Bytecoin для продажи:\n'
        "Например: 100000",
        parse_mode="HTML"
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
        await message.answer(
            f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Максимальная продажа: {amount} Bytecoin',
            parse_mode="HTML"
        )
        await state.clear()
    except:
        await message.answer("❌ Введите корректное число")


@router.message(F.text == "💳 Реквизиты")
async def admin_requisites(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    current = await get_setting("payment_requisites", "")

    await message.answer(
        f'<tg-emoji emoji-id="5265074015868822600">📱</tg-emoji> <b>Реквизиты для оплаты</b>\n\n'
        f"Текущие: <code>{current or 'Не установлены'}</code>\n\n"
        f"Введите новые реквизиты:\n"
        f"<i>Например: +7 958 238-99-88 (Ю-МАНИ КОШЕЛЁК)</i>",
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_requisites)


@router.message(AdminStates.waiting_requisites)
async def set_requisites(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    await set_setting("payment_requisites", message.text)
    await message.answer(
        f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Реквизиты обновлены:\n\n{message.text}',
        parse_mode="HTML"
    )
    await state.clear()


@router.message(F.text == "📈 Курс")
async def admin_rate(message: Message):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    await message.answer(
        f'<tg-emoji emoji-id="5429651785352501917">📈</tg-emoji> Текущие курсы\n\n'
        f"Покупка (у пользователя): 1000 Bytecoin = {config.RATE_BUY * 1000}₽\n"
        f"Продажа (пользователю): 1000 Bytecoin = {config.RATE_SELL * 1000}₽\n\n"
        f"Для изменения курса используйте команду:\n"
        f"/set_rate",
        parse_mode="HTML"
    )


@router.message(Command("withdraw_bc"))
async def withdraw_bc_start(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    await message.answer(
        '<tg-emoji emoji-id="5445355530111437729">📤</tg-emoji> Вывод BC\n\n'
        "Введите количество BC для вывода:",
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_withdraw)


@router.message(AdminStates.waiting_withdraw)
async def withdraw_bc_finish(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    try:
        amount = Decimal(message.text.strip())
        your_id = config.ADMIN_IDS[0] if config.ADMIN_IDS else config.ADMIN_ID

        result = await bytecoin_api.transfer_to_user(
            user_id=your_id,
            sum_coins=amount,
            idempotency_key=f"withdraw-{uuid.uuid4()}"
        )

        if result.get("status") == "ok":
            await message.answer(
                f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Выведено: {amount:.0f} BC\n'
                f"Transaction: {result.get('transaction_id', 'N/A')[:12]}",
                parse_mode="HTML"
            )
        else:
            await message.answer(
                f'<tg-emoji emoji-id="5280803324273115630">❌</tg-emoji> Ошибка: {result.get("error", "Unknown")}',
                parse_mode="HTML"
            )

        await state.clear()
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")


@router.message(Command("set_rate"))
async def set_rate_start(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    await message.answer(
        '<tg-emoji emoji-id="5429651785352501917">📈</tg-emoji> Введите курс покупки (за 1000 Bytecoin):\n'
        "Например: 0.87",
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_rate_buy)


@router.message(AdminStates.waiting_rate_buy)
async def set_rate_buy_finish(message: Message, state: FSMContext):
    try:
        rate = Decimal(message.text.strip())
        rate_per_coin = rate / 1000

        await state.update_data(rate_buy=rate_per_coin)
        await message.answer(
            '<tg-emoji emoji-id="5429651785352501917">📈</tg-emoji> Теперь введите курс продажи (за 1000 Bytecoin):\n'
            "Например: 1.1",
            parse_mode="HTML"
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

        await set_setting("rate_buy", str(rate_buy))
        await set_setting("rate_sell", str(rate_per_coin))

        await message.answer(
            f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Курсы обновлены!\n\n'
            f"Покупка: 1000 Bytecoin = {rate_buy * 1000}₽\n"
            f"Продажа: 1000 Bytecoin = {rate_per_coin * 1000}₽",
            parse_mode="HTML"
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

        text = '<tg-emoji emoji-id="5902335789798265487">👤</tg-emoji> <b>Последние 10 пользователей:</b>\n\n'
        for user in users:
            text += f"ID: {user.telegram_id}\n"
            text += f"Имя: {html.escape(user.first_name or '')}\n"
            text += f"Куплено: {user.total_bought_coins:.3f} BCN\n"
            text += f"Продано: {user.total_sold_coins:.3f} BCN\n"
            text += "➖➖➖➖➖➖➖➖\n"

        await message.answer(text, parse_mode="HTML")


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

        text = '<tg-emoji emoji-id="5440457429147997980">📋</tg-emoji> <b>Последние 10 сделок:</b>\n\n'
        for deal in deals:
            status_emoji = '<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji>' if deal.status == "completed" else '<tg-emoji emoji-id="5215277915930896212">⏳</tg-emoji>'
            text += f"{status_emoji} {deal.type.upper()} | {deal.rub_amount}₽ | {deal.coins_amount:.3f} BCN\n"
            text += f"Статус: {deal.status}\n"
            text += f"ID: {deal.deal_number}\n"
            text += "➖➖➖➖➖➖➖➖\n"

        await message.answer(text, parse_mode="HTML")


@router.message(Command("set_limits"))
async def set_limits_start(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    await message.answer(
        '<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Введите минимальную сумму сделки (рублей):\n'
        "Например: 1",
        parse_mode="HTML"
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
            '<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Теперь введите максимальную сумму сделки (рублей):\n'
            "Например: 17500",
            parse_mode="HTML"
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

        await state.update_data(max_limit=max_limit)
        await message.answer(
            '<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Теперь введите минимальную сумму продажи BC (рублей):\n'
            "Например: 50",
            parse_mode="HTML"
        )
        await state.set_state(AdminStates.waiting_min_sell)
    except:
        await message.answer("❌ Введите корректное число")


@router.message(AdminStates.waiting_min_sell)
async def set_min_sell(message: Message, state: FSMContext):
    try:
        min_sell = Decimal(message.text.strip())
        if min_sell < 0:
            await message.answer("❌ Сумма не может быть отрицательной")
            return

        data = await state.get_data()
        min_limit = data.get("min_limit")
        max_limit = data.get("max_limit")

        config.MIN_DEAL_RUB = min_limit
        config.MAX_DEAL_RUB = max_limit
        config.MIN_SELL_RUB = min_sell

        await message.answer(
            f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Лимиты обновлены!\n\n'
            f"Минимум покупки: {min_limit}₽\n"
            f"Максимум: {max_limit}₽\n"
            f"Минимум продажи: {min_sell}₽",
            parse_mode="HTML"
        )
        await state.clear()
    except:
        await message.answer("❌ Введите корректное число")


@router.message(Command("broadcast"))
async def broadcast_start(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    await message.answer(
        '<tg-emoji emoji-id="5258342814273513092">🔔</tg-emoji> Введите текст для рассылки:\n'
        "Он будет отправлен всем пользователям бота.",
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_broadcast)


@router.message(AdminStates.waiting_broadcast)
async def broadcast_send(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    text = message.text

    async with async_session() as session:
        users = await session.execute(select(User))
        users = users.scalars().all()

        success = 0
        failed = 0

        for user in users:
            try:
                await bot.send_message(user.telegram_id, text)
                success += 1
            except:
                failed += 1

        await message.answer(
            f'<tg-emoji emoji-id="5258342814273513092">🔔</tg-emoji> Рассылка завершена!\n\n'
            f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Отправлено: {success}\n'
            f'<tg-emoji emoji-id="5280803324273115630">❌</tg-emoji> Ошибок: {failed}',
            parse_mode="HTML"
        )
        await state.clear()


@router.message(F.text == "📋 Активные сделки")
async def admin_active_deals(message: Message):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    async with async_session() as session:
        deals = await session.execute(
            select(Deal).where(
                Deal.status == "checking",
                Deal.idempotency_key.like("buy-%")
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

            user = await session.get(User, deal.user_id)
            first_name = html.escape(user.first_name) if user and user.first_name else "Пользователь"
            username = f"@{html.escape(user.username)}" if user and user.username else "Нет тега"

            await message.answer(
                f'<tg-emoji emoji-id="5440457429147997980">📋</tg-emoji> <b>Сделка {deal.deal_number}</b>\n\n'
                f'<tg-emoji emoji-id="5902335789798265487">👤</tg-emoji> Чел: <code>{first_name} aka. {username}</code>\n'
                f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Сумма: {deal.rub_amount}₽\n'
                f'<tg-emoji emoji-id="5197572355634781614">💎</tg-emoji> BC: {deal.coins_amount:.0f}\n'
                f"📝 Метод: {deal.payment_method}\n"
                f'<tg-emoji emoji-id="5215277915930896212">⏳</tg-emoji> Создана: {deal.created_at.strftime("%H:%M")}',
                parse_mode="HTML",
                reply_markup=kb
            )


@router.callback_query(F.data.startswith("approve_sell:"))
async def approve_sell_deal(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
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

        user = await session.get(User, deal.user_id)
        first_name = html.escape(user.first_name) if user and user.first_name else "Пользователь"
        username = f"@{html.escape(user.username)}" if user and user.username else "Нет тега"

        rub_balance = Decimal(await get_setting("rub_balance", "0"))
        new_rub_balance = rub_balance - Decimal(deal.rub_amount)
        await set_setting("rub_balance", str(new_rub_balance))

        notify_data = await get_setting(f"notify_{deal.id}", "")
        if notify_data:
            import json
            messages = json.loads(notify_data)
            for admin_id, msg_id in messages.items():
                try:
                    await bot.edit_message_text(
                        chat_id=int(admin_id),
                        message_id=int(msg_id),
                        text=f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Сделка {deal.deal_number} подтверждена!\n'
                             f'<tg-emoji emoji-id="5902335789798265487">👤</tg-emoji> {first_name} ({username})\n'
                             f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Выплачено: {format_decimal(deal.rub_amount)}₽\n'
                             f"Подтвердил: {callback.from_user.first_name}",
                        parse_mode="HTML"
                    )
                except:
                    pass

        try:
            await bot.send_message(
                deal.user_id,
                f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Выплата произведена!\n\n'
                f'<tg-emoji emoji-id="5440457429147997980">📋</tg-emoji> Сделка: {deal.deal_number}\n'
                f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Вы получили: {format_decimal(deal.rub_amount)}₽',
                parse_mode="HTML"
            )
        except:
            pass

        await callback.answer("✅ Подтверждено!")


@router.callback_query(F.data.startswith("reject_sell:"))
async def reject_sell_deal(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
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
            f'<tg-emoji emoji-id="5280803324273115630">❌</tg-emoji> Сделка {deal.deal_number} отклонена',
            parse_mode="HTML"
        )

        await bot.send_message(
            deal.user_id,
            f'<tg-emoji emoji-id="5280803324273115630">❌</tg-emoji> Выплата отклонена\n\n'
            f'<tg-emoji emoji-id="5440457429147997980">📋</tg-emoji> Сделка: {deal.deal_number}\n'
            f"Обратитесь в поддержку: @EyellizSUP",
            parse_mode="HTML"
        )

        await callback.answer("Отклонено")


@router.message(Command("hide_user"))
async def hide_user_start(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return
    await message.answer("Введите ID пользователя для скрытия из топа:")
    await state.set_state(AdminStates.waiting_hide_user)


@router.message(AdminStates.waiting_hide_user)
async def hide_user_finish(message: Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())
        async with async_session() as session:
            existing = await session.scalar(
                select(HiddenUser).where(HiddenUser.user_id == user_id)
            )
            if existing:
                await message.answer("❌ Уже скрыт")
                return

            hidden = HiddenUser(user_id=user_id)
            session.add(hidden)
            await session.commit()

        await message.answer(
            f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Пользователь {user_id} скрыт из топа',
            parse_mode="HTML"
        )
        await state.clear()
    except:
        await message.answer("❌ Введите корректный ID")


@router.message(F.text == "🔧 Настройки")
async def admin_settings(message: Message):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    await message.answer(
        f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Настройки\n\n'
        f"Минимум покупки: {config.MIN_DEAL_RUB}₽\n"
        f"Максимум: {config.MAX_DEAL_RUB}₽\n"
        f"Минимум продажи: {config.MIN_SELL_RUB}₽\n\n"
        "Команды:\n"
        "/set_rub_balance - баланс рублей\n"
        "/set_max_buy - макс. выкуп\n"
        "/set_max_sell - макс. продажа\n"
        "/set_limits - лимиты сделок\n"
        "/set_rate - курсы\n"
        "/broadcast - рассылка",
        parse_mode="HTML"
    )


@router.message(F.text == "⬅️ В меню")
async def back_to_menu(message: Message):
    await message.answer(
        "Главное меню:",
        reply_markup=main_menu_kb()
    )


@router.message(F.text == "🎁 Приз топа")
async def set_top_prize_button(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    current_prize = await get_setting("top_prize", "")

    await message.answer(
        f'<tg-emoji emoji-id="5193085063998224234">🎁</tg-emoji> <b>Приз для топа</b>\n\n'
        f"Текущий приз: <code>{current_prize or 'Не установлен'}</code>\n\n"
        f"Введите новый текст приза:",
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_top_prize)


@router.message(AdminStates.waiting_top_prize)
async def set_top_prize_finish(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    await set_setting("top_prize", message.text)
    await message.answer(
        f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Приз обновлён!\n\n{message.text}',
        parse_mode="HTML"
    )
    await state.clear()


@router.callback_query(F.data.startswith("approve_buy:"))
async def approve_buy_deal(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
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
                    user.total_bought_week = (user.total_bought_week or 0) + deal.coins_amount
                    user.total_bought_rub = (user.total_bought_rub or 0) + deal.rub_amount

                await session.commit()

                notify_data = await get_setting(f"notify_{deal.id}", "")
                if notify_data:
                    import json
                    messages = json.loads(notify_data)
                    for admin_id, msg_id in messages.items():
                        try:
                            await bot.edit_message_text(
                                chat_id=int(admin_id),
                                message_id=int(msg_id),
                                text=f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Сделка {deal.deal_number} подтверждена!\n'
                                     f"BC начислены пользователю.\n"
                                     f"Подтвердил: {callback.from_user.id}",
                                parse_mode="HTML"
                            )
                        except:
                            pass

                rub_balance = Decimal(await get_setting("rub_balance", "0"))
                new_rub_balance = rub_balance + Decimal(deal.rub_amount)
                await set_setting("rub_balance", str(new_rub_balance))

                user = await session.get(User, deal.user_id)
                first_name = html.escape(user.first_name) if user and user.first_name else "Пользователь"
                username = f"@{html.escape(user.username)}" if user and user.username else "Нет тега"

                await callback.message.edit_text(
                    f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Сделка {deal.deal_number} подтверждена!\n'
                    f'<tg-emoji emoji-id="5902335789798265487">👤</tg-emoji> {first_name} ({username})\n'
                    f"BC начислены пользователю.\n"
                    f"Резерв уменьшен на {deal.rub_amount}₽",
                    parse_mode="HTML"
                )

                for admin_id in config.ADMIN_IDS:
                    if admin_id != callback.from_user.id:
                        try:
                            await bot.send_message(
                                admin_id,
                                f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Сделка {deal.deal_number} уже подтверждена.',
                                parse_mode="HTML"
                            )
                        except:
                            pass

                await bot.send_message(
                    deal.user_id,
                    f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Оплата подтверждена!\n\n'
                    f'<tg-emoji emoji-id="5440457429147997980">📋</tg-emoji> Сделка: {deal.deal_number}\n'
                    f'<tg-emoji emoji-id="5197572355634781614">💎</tg-emoji> Вы получили: {deal.coins_amount:.0f} BC\n'
                    f"Transaction: {result.get('transaction_id', 'N/A')[:12]}",
                    parse_mode="HTML"
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


@router.message(F.text == "⭐ Вкл/Выкл звёзды")
async def toggle_stars(message: Message):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    current = await get_setting("stars_enabled", "1")
    new_value = "0" if current == "1" else "1"
    await set_setting("stars_enabled", new_value)

    status = "включены" if new_value == "1" else "выключены"
    await message.answer(
        f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Покупки звёздами {status}',
        parse_mode="HTML"
    )


@router.message(F.text == "🔔 Порог баланса")
async def admin_balance_alert(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    thresholds = await get_setting("balance_alert_thresholds", "100000,50000,10000")

    await message.answer(
        f'<tg-emoji emoji-id="5258342814273513092">🔔</tg-emoji> <b>Пороги уведомления</b>\n\n'
        f"Текущие пороги: <code>{thresholds}</code>\n\n"
        f"Введите новые пороги через запятую:\n"
        f"<i>Например: 100000,50000,10000</i>",
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_balance_threshold)


@router.message(AdminStates.waiting_balance_threshold)
async def set_balance_threshold(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return

    try:
        thresholds = message.text.strip()
        [Decimal(x.strip()) for x in thresholds.split(",")]

        await set_setting("balance_alert_thresholds", thresholds)
        await message.answer(
            f'<tg-emoji emoji-id="5215538285438311443">✅</tg-emoji> Пороги обновлены: {thresholds}',
            parse_mode="HTML"
        )
        await state.clear()
    except:
        await message.answer("❌ Введите числа через запятую")


@router.callback_query(F.data.startswith("reject_buy:"))
async def reject_buy_deal(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
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
            f'<tg-emoji emoji-id="5280803324273115630">❌</tg-emoji> Сделка {deal.deal_number} отклонена',
            parse_mode="HTML"
        )

        await bot.send_message(
            deal.user_id,
            f'<tg-emoji emoji-id="5280803324273115630">❌</tg-emoji> Оплата не подтверждена\n\n'
            f'<tg-emoji emoji-id="5440457429147997980">📋</tg-emoji> Сделка: {deal.deal_number}\n'
            "Обратитесь в поддержку.\n\n"
            "Support : @EyellizSUP",
            parse_mode="HTML"
        )

        await callback.answer("Сделка отклонена")