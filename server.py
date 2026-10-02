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

from bytecoin_api import bytecoin_api
from database import async_session, WebhookEvent, User, PaymentMethod, Deal, get_setting, set_setting, PendingSell, format_decimal
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


def check_crypto_signature(token: str, body: str, headers: dict) -> bool:
    """Проверяет подпись вебхука CryptoBot"""
    signature = headers.get("crypto-pay-api-signature")
    if not signature:
        return False

    secret = hashlib.sha256(token.encode()).digest()
    expected = hmac.new(
        secret,
        body.encode(),
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


def verify_signature(timestamp: str, event_id: str, raw_body: bytes, signature: str) -> bool:
    """Проверяет подпись Bytecoin webhook"""
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
    """Обрабатывает входящие переводы BC"""
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

            rate_buy = Decimal(await get_setting("rate_buy", str(config.RATE_BUY)))
            rub_amount = sum_coins * rate_buy

            user = await session.get(User, user_id)
            username = f"@{user.username}" if user and user.username else "Нет тега"
            first_name = user.first_name if user and user.first_name else "Пользователь"

            method = await session.scalar(
                select(PaymentMethod).where(
                    PaymentMethod.user_id == user_id
                ).order_by(PaymentMethod.id.desc())
            )

            pending = await session.scalar(
                select(PendingSell).where(
                    PendingSell.user_id == user_id
                ).order_by(PendingSell.id.desc())
            )

            if pending and method:
                if rub_amount < config.MIN_SELL_RUB:
                    await bot.send_message(
                        user_id,
                        f"❌ <b>Минимальная сумма продажи: {config.MIN_SELL_RUB}₽</b>\n\n"
                        f"Вы отправили: {format_decimal(rub_amount)}₽\n\n"
                        f"Обратитесь в поддержку: @EyellizSUP",
                        parse_mode="HTML"
                    )
                    for admin_id in config.ADMIN_IDS:
                        try:
                            await bot.send_message(
                                admin_id,
                                f"⚠️ Пользователь {first_name} {username}\n"
                                f"Отправил меньше минимума!\n"
                                f"💎 BC: {sum_coins:.0f}\n"
                                f"💰 Сумма: {rub_amount:.2f}₽\n"
                                f"Минимум: {config.MIN_SELL_RUB}₽"
                            )
                        except:
                            pass

                    await session.delete(pending)
                    await session.commit()
                    return {"status": "ok"}

                deal_number = await generate_deal_number()
                deal = Deal(
                    deal_number=deal_number,
                    user_id=user_id,
                    type="sell",
                    coins_amount=sum_coins,
                    rub_amount=rub_amount,
                    rate=rate_buy,
                    status="checking",
                    payment_method=method.method_type,
                    transaction_id=transaction_id,
                    idempotency_key=f"sell-{uuid.uuid4()}"
                )
                session.add(deal)
                await session.commit()

                await session.delete(pending)
                await session.commit()

                if method.method_type == "card":
                    payment_info = f"💳 Карта: {method.card_number}\n🏦 Банк: {method.card_bank}"
                elif method.method_type == "sbp":
                    payment_info = f"📱 СБП: {method.sbp_phone}\n🏦 Банк: {method.sbp_bank}"
                else:
                    payment_info = "Не указано"

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

                notification_messages = {}
                for admin_id in config.ADMIN_IDS:
                    try:
                        msg = await bot.send_message(
                            admin_id,
                            f"🔔 Новая продажа BC!\n\n"
                            f"📋 Сделка: {deal_number}\n"
                            f"👤 Пользователь: {first_name} {username}\n"
                            f"💎 BC: {sum_coins:.0f}\n"
                            f"💰 К оплате: {rub_amount:.2f}₽\n\n"
                            f"Реквизиты:\n{payment_info}\n\n"
                            f"Подтвердите выплату:",
                            reply_markup=admin_kb
                        )
                        notification_messages[admin_id] = msg.message_id
                    except:
                        pass

                await set_setting(f"notify_{deal.id}", json.dumps(notification_messages))

                user.total_sold_coins = (user.total_sold_coins or 0) + sum_coins
                user.total_sold_rub = (user.total_sold_rub or 0) + rub_amount
                await session.commit()

                await bot.send_message(
                    user_id,
                    f"✅ Перевод получен!\n\n"
                    f"📋 Сделка: {deal_number}\n"
                    f"💎 BC: {sum_coins:.0f}\n"
                    f"💰 Вы получите: {rub_amount:.2f}₽\n\n"
                    "Ожидайте выплату..."
                )

            else:
                for admin_id in config.ADMIN_IDS:
                    try:
                        await bot.send_message(
                            admin_id,
                            f"📥 Пополнение баланса!\n\n"
                            f"👤 Пользователь: {first_name} {username}\n"
                            f"💎 BC: {sum_coins:.0f}\n"
                            f"💰 Эквивалент: {rub_amount:.2f}₽\n\n"
                            f"Простое пополнение без заявки."
                        )
                    except:
                        pass

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


@app.post("/crypto-webhook")
async def crypto_webhook(request: Request):
    """Обрабатывает вебхуки от CryptoBot (оплата USDT)"""
    try:
        raw_body = await request.body()
        raw_str = raw_body.decode("utf-8")

        if not check_crypto_signature(config.CRYPTO_PAY_TOKEN, raw_str, dict(request.headers)):
            logger.error("CryptoBot webhook: Invalid signature")
            raise HTTPException(status_code=400, detail="Invalid signature")

        data = json.loads(raw_str)

        if data.get("update_type") == "invoice_paid":
            payload = data.get("payload", {})
            invoice_payload = payload.get("payload")
            invoice_id = payload.get("invoice_id")

            parts = invoice_payload.split("_")
            user_id = int(parts[1])
            coins_amount = Decimal(parts[2])

            result = await bytecoin_api.transfer_to_user(
                user_id=user_id,
                sum_coins=coins_amount,
                idempotency_key=f"crypto-{invoice_id}"
            )

            if result.get("status") == "ok":
                deal_number = f"#crypto-{invoice_id}"

                async with async_session() as session:
                    deal = Deal(
                        deal_number=deal_number,
                        user_id=user_id,
                        type="buy",
                        coins_amount=coins_amount,
                        rub_amount=coins_amount * config.RATE_SELL,
                        rate=config.RATE_SELL,
                        status="completed",
                        payment_method="usdt",
                        idempotency_key=f"crypto-{invoice_id}"
                    )
                    session.add(deal)

                    user = await session.get(User, user_id)
                    if user:
                        user.total_bought_coins = (user.total_bought_coins or 0) + coins_amount
                        user.total_bought_week = (user.total_bought_week or 0) + coins_amount
                        user.total_bought_rub = (user.total_bought_rub or 0) + (coins_amount * config.RATE_SELL)
                    await session.commit()

                await bot.send_message(
                    user_id,
                    f"✅ Оплата USDT получена!\n\n"
                    f"📋 Сделка: {deal_number}\n"
                    f"💎 Вы получили: {coins_amount:.0f} BC"
                )

                for admin_id in config.ADMIN_IDS:
                    try:
                        await bot.send_message(
                            admin_id,
                            f"💰 Оплата USDT!\n\n"
                            f"👤 User ID: {user_id}\n"
                            f"💎 BC: {coins_amount:.0f}\n"
                            f"✅ Завершена автоматически"
                        )
                    except:
                        pass

        return {"ok": True}

    except Exception as e:
        logger.error(f"CryptoBot webhook error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    return {"status": "ok"}