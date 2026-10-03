from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="Купить BC",
                    icon_custom_emoji_id="5429651785352501917"  # 📈
                ),
                KeyboardButton(
                    text="Продать BC",
                    icon_custom_emoji_id="5429518319243775957"  # 📉
                )
            ],
            [
                KeyboardButton(
                    text="Курс и лимиты",
                    icon_custom_emoji_id="5260742580005530450"  # 📊
                ),
                KeyboardButton(
                    text="Топ покупателей",
                    icon_custom_emoji_id="5409008750893734809"  # 🏆
                )
            ],
            [
                KeyboardButton(
                    text="Мой профиль",
                    icon_custom_emoji_id="5902335789798265487"  # 👤
                ),
                KeyboardButton(
                    text="О сервисе",
                    icon_custom_emoji_id="5334544901428229844"  # ℹ️
                )
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
                KeyboardButton(text="⭐ Вкл/Выкл звёзды"),
                KeyboardButton(text="💲 Вкл/Выкл USDT")

            ],
            [
                KeyboardButton(text="📈 Курс"),
                KeyboardButton(text="📜 История сделок"),
                KeyboardButton(text="💳 Реквизиты")
            ],
            [
                KeyboardButton(text="🎁 Приз топа"),
                KeyboardButton(text="❌ Удалить приз")
            ],
            [
                KeyboardButton(text="👥 Пользователи"),
                KeyboardButton(text="🔧 Настройки"),
            ],
            [
                KeyboardButton(text="⬅️ В меню")
            ]
        ],
        resize_keyboard=True
    )


def saved_payments_kb(methods: list) -> InlineKeyboardMarkup:
    buttons = []

    for method in methods:
        if method.method_type == "card":
            buttons.append([
                InlineKeyboardButton(
                    text=f"{method.card_bank or 'Карта'} •••• {method.card_number[-4:] if method.card_number else ''}",
                    callback_data=f"use_payment:{method.id}",
                    icon_custom_emoji_id="5217961106554769883"
                )
            ])
        elif method.method_type == "sbp":
            buttons.append([
                InlineKeyboardButton(
                    text=f"{method.sbp_bank or 'СБП'} {method.sbp_phone}",
                    callback_data=f"use_payment:{method.id}",
                    icon_custom_emoji_id="5217961106554769883"
                )
            ])

    buttons.append([
        InlineKeyboardButton(text="➕ Добавить новый", callback_data="add_new_payment")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)