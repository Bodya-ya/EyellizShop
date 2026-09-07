import os
import json
import hmac
import hashlib
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
import logging

from fastapi import FastAPI, Request, HTTPException
from sqlalchemy import select, func
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from database import async_session, WebhookEvent, User, PaymentMethod, Deal, get_setting, set_setting, PendingSell
from config import config
from bot_instance import bot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Bytecoin Bot Webhook")


async def generate_deal_number() -> str:
    """Генерирует номер сделки"""
    async with async_session() as session:
        count = await session.scalar(select(func.count()).select_from(Deal))
        return f"#{count + 1}"


def verify_signature(timestamp: str, event_id: str, raw_body: bytes, signature: str) -> bool:
    """Проверяет подпись webhook"""
    try:
        message = f"{timestamp}.{event_id}.{raw_body.decode()}"
        expected = hmac.new(
            config.BYTECOIN_WEBHOOK_SECRET.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        sig_value = signature.split("=")[1] if "=" in signature else signature

        return hmac.compare_digest(expected, sig_value)
    except Exception as e:
        logger.error(f"Signature verification error: {e}")
        return False


@app.post("/webhook/bytecoin")
async def bytecoin_webhook(request: Request):
    """Обрабатывает входящие переводы"""
    try:
        event_id = request.headers.get("X-Bytecoin-Event-ID")
        timestamp = request.headers.get("X-Bytecoin-Timestamp")
        signature = request.headers.get("X-Bytecoin-Signature")

        if not all([event_id, timestamp, signature]):
            raise HTTPException(status_code=400, detail="Missing headers")

        raw_body = await request.body()
        body = json.loads(raw_body)

        if not verify_signature(timestamp, event_id, raw_body, signature):
            logger.error("Invalid signature")
            raise HTTPException(status_code=401, detail="Invalid signature")

        if body.get("event") != "transfer.received":
            return {"status": "ok", "message": "Not transfer event"}

        data = body.get("data", {})
        transaction_id = data.get("transaction_id")
        user_id = data.get("user_id")
        sum_coins = Decimal(data.get("sum", "0"))

        logger.info(f"Received transfer: {transaction_id} from {user_id} amount {sum_coins}")

        async with async_session() as session:
            existing = await session.get(WebhookEvent, event_id)
            if existing:
                return {"status": "ok", "message": "Duplicate"}

            event = WebhookEvent(
                event_id=event_id,
                transaction_id=transaction_id,
                user_id=user_id,
                sum=sum_coins,
                processed=True
            )
            session.add(event)
            await session.commit()

            rub_amount = sum_coins * config.RATE_BUY

            # Ищем пользователя
            user = await session.get(User, user_id)
            username = f"@{user.username}" if user and user.username else "Нет тега"
            first_name = user.first_name if user and user.first_name else "Пользователь"

            # Ищем реквизиты
            method = await session.scalar(
                select(PaymentMethod).where(
                    PaymentMethod.user_id == user_id
                ).order_by(PaymentMethod.id.desc())
            )

            # === ПРОВЕРЯЕМ, ЕСТЬ ЛИ ЗАЯВКА ===
            pending = await session.scalar(
                select(PendingSell).where(
                    PendingSell.user_id == user_id
                ).order_by(PendingSell.id.desc())
            )

            if pending and method:
                # === ЭТО СДЕЛКА — создаём ===
                deal_number = await generate_deal_number()
                deal = Deal(
                    deal_number=deal_number,
                    user_id=user_id,
                    type="sell",
                    coins_amount=sum_coins,
                    rub_amount=rub_amount,
                    rate=config.RATE_BUY,
                    status="checking",
                    payment_method=method.method_type,
                    transaction_id=transaction_id,
                    idempotency_key=f"sell-{uuid.uuid4()}"
                )
                session.add(deal)
                await session.commit()

                # Удаляем pending
                await session.delete(pending)
                await session.commit()

                if method.method_type == "card":
                    payment_info = f"💳 Карта: {method.card_number}\n🏦 Банк: {method.card_bank}"
                elif method.method_type == "sbp":
                    payment_info = f"📱 СБП: {method.sbp_phone}\n🏦 Банк: {method.sbp_bank}"
                else:
                    payment_info = "Не указано"

                # Кнопка подтверждения для админа
                admin_kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="✅ Подтвердить выплату",
                                callback_data=f"approve_sell:{deal.id}"
                            ),
                            InlineKeyboardButton(
                                text="❌ Отклонить",
                                callback_data=f"reject_sell:{deal.id}"
                            )
                        ]
                    ]
                )

                await bot.send_message(
                    config.ADMIN_ID,
                    f"🔔 Новая продажа BC!\n\n"
                    f"📋 Сделка: {deal_number}\n"
                    f"👤 Пользователь: {first_name} {username}\n"
                    f"💎 BC: {sum_coins:.0f}\n"
                    f"💰 К оплате: {rub_amount:.2f}₽\n\n"
                    f"Реквизиты:\n{payment_info}\n\n"
                    f"Подтвердите выплату:",
                    reply_markup=admin_kb
                )

                # Обновляем статистику
                user.total_sold_coins = (user.total_sold_coins or 0) + sum_coins
                user.total_sold_rub = (user.total_sold_rub or 0) + rub_amount
                await session.commit()

                # Обновляем резерв
                rub_balance = Decimal(await get_setting("rub_balance", "0"))
                new_rub_balance = rub_balance + rub_amount
                await set_setting("rub_balance", str(new_rub_balance))

                # Уведомляем пользователя — СДЕЛКА
                await bot.send_message(
                    user_id,
                    f"✅ Перевод получен!\n\n"
                    f"📋 Сделка: {deal_number}\n"
                    f"💎 BC: {sum_coins:.0f}\n"
                    f"💰 Вы получите: {rub_amount:.2f}₽\n\n"
                    "Ожидайте выплату..."
                )

            else:
                # === ПРОСТО ПОПОЛНЕНИЕ ===
                await bot.send_message(
                    config.ADMIN_ID,
                    f"📥 Пополнение баланса!\n\n"
                    f"👤 Пользователь: {first_name} {username}\n"
                    f"💎 BC: {sum_coins:.0f}\n"
                    f"💰 Эквивалент: {rub_amount:.2f}₽\n\n"
                    f"Простое пополнение без заявки."
                )

                # Уведомляем пользователя — ПОПОЛНЕНИЕ
                await bot.send_message(
                    user_id,
                    f"✅ Вы пополнили резерв бота на {sum_coins:.0f} BC!\n\n"
                    f"Если это было ошибочно, обратитесь в поддержку бота⬇️\n\n"
                    f"Support: @EyellizSUP"
                )

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Webhook error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    return {"status": "ok"}