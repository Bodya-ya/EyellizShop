from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="Купить BC 💎"),
                KeyboardButton(text="Продать BC 💎")
            ],
            [
                KeyboardButton(text="📊 Курс и лимиты"),
                KeyboardButton(text="🏆 Топ покупателей")
            ],
            [
                KeyboardButton(text="👤 Мой профиль"),
                KeyboardButton(text="ℹ️ О сервисе")
            ]
        ],
        resize_keyboard=True
    )
def payment_method_buy_kb() -> InlineKeyboardMarkup:
    """Клавиатура выбора способа оплаты при покупке за рубли"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💳 СБП", callback_data="pay_sbp")
            ]
        ]
    )


def payment_method_sell_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💳 Карта", callback_data="sell_card"),
                InlineKeyboardButton(text="📱 СБП", callback_data="sell_sbp")
            ]
        ]
    )

def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Я оплатил", callback_data="confirm_buy_payment"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_deal")
            ]
        ]
    )

def admin_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="💰 Баланс"),
                KeyboardButton(text="📈 Курс")
            ],
            [
                KeyboardButton(text="📋 Активные сделки"),
                KeyboardButton(text="📜 История сделок")  # ← Новая кнопка
            ],
            [
                KeyboardButton(text="📊 Статистика"),
                KeyboardButton(text="👥 Пользователи")
            ],
            [
                KeyboardButton(text="🔧 Настройки"),
                KeyboardButton(text="⬅️ В меню")
            ]
        ],
        resize_keyboard=True
    )


def saved_payments_kb(methods: list) -> InlineKeyboardMarkup:
    """Клавиатура с сохранёнными реквизитами"""
    buttons = []

    for method in methods:
        if method.method_type == "card":
            label = f"💳 {method.card_bank or 'Карта'} •••• {method.card_number[-4:] if method.card_number else ''}"
            callback = f"use_payment:{method.id}"
        elif method.method_type == "sbp":
            label = f"📱 {method.sbp_bank or 'СБП'} {method.sbp_phone}"
            callback = f"use_payment:{method.id}"

        buttons.append([InlineKeyboardButton(text=label, callback_data=callback)])

    buttons.append([
        InlineKeyboardButton(text="➕ Добавить новый", callback_data="add_new_payment")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)