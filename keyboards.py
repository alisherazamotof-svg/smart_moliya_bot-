from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

def get_main_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💰 Balans"), KeyboardButton(text="📊 Hisobot")],
            [KeyboardButton(text="➕ Kirim"), KeyboardButton(text="➖ Chiqim")],
            [KeyboardButton(text="📜 Tarix"), KeyboardButton(text="💳 Qarzlar")],
            [KeyboardButton(text="🤖 AI yordamchi"), KeyboardButton(text="⚙️ Sozlamalar")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_confirmation_keyboard(action_id: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"confirm_{action_id}"),
                InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"cancel_{action_id}")
            ]
        ]
    )
