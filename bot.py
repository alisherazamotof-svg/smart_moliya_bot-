import asyncio
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from dotenv import load_dotenv
from groq import AsyncGroq

# ==================== SOZLAMALAR ====================

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

groq_client = AsyncGroq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

DB_PATH = BASE_DIR / "smart_moliya.db"
TZ = ZoneInfo("Asia/Tashkent")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

router = Router()

# Tasdiqlanishi kutilayotgan yozuvlar.
pending_transactions = {}


# ==================== MENYU ====================

main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="💰 Balans"),
            KeyboardButton(text="➕ Kirim"),
        ],
        [
            KeyboardButton(text="➖ Chiqim"),
            KeyboardButton(text="📊 Hisobot"),
        ],
        [
            KeyboardButton(text="📜 Tarix"),
            KeyboardButton(text="🤖 AI yordamchi"),
        ],
        [
            KeyboardButton(text="💳 Qarzlar"),
            KeyboardButton(text="⚙️ Sozlamalar"),
        ],
    ],
    resize_keyboard=True,
    input_field_placeholder="Masalan: 50 ming ovqatga ketdi",
)


class TransactionForm(StatesGroup):
    amount = State()
    note = State()


# ==================== MA'LUMOTLAR BAZASI ====================

def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def init_db():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('income', 'expense')),
                amount INTEGER NOT NULL CHECK(amount > 0),
                category TEXT NOT NULL,
                note TEXT NOT NULL,
                transaction_date TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_transactions_user_date
            ON transactions(user_id, transaction_date)
        """)

    logger.info("SQLite bazasi tayyor: %s", DB_PATH)


def now_local():
    return datetime.now(TZ)


def add_transaction(user_id, kind, amount, category, note, tx_date):
    now = now_local().isoformat(timespec="seconds")

    with get_connection() as conn:
        cursor = conn.execute("""
            INSERT INTO transactions
            (user_id, kind, amount, category, note,
             transaction_date, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            kind,
            amount,
            category,
            note,
            tx_date,
            now,
        ))

        return cursor.lastrowid


def get_balance(user_id):
    with get_connection() as conn:
        row = conn.execute("""
            SELECT
                COALESCE(SUM(
                    CASE WHEN kind = 'income' THEN amount ELSE 0 END
                ), 0) AS income,
                COALESCE(SUM(
                    CASE WHEN kind = 'expense' THEN amount ELSE 0 END
                ), 0) AS expense
            FROM transactions
            WHERE user_id = ?
        """, (user_id,)).fetchone()

    return row["income"], row["expense"]


def get_period_totals(user_id, start_date, end_date):
    with get_connection() as conn:
        row = conn.execute("""
            SELECT
                COALESCE(SUM(
                    CASE WHEN kind = 'income' THEN amount ELSE 0 END
                ), 0) AS income,
                COALESCE(SUM(
                    CASE WHEN kind = 'expense' THEN amount ELSE 0 END
                ), 0) AS expense
            FROM transactions
            WHERE user_id = ?
              AND transaction_date >= ?
              AND transaction_date <= ?
        """, (user_id, start_date, end_date)).fetchone()

    return row["income"], row["expense"]


def get_recent_transactions(user_id, limit=10):
    with get_connection() as conn:
        return conn.execute("""
            SELECT *
            FROM transactions
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
        """, (user_id, limit)).fetchall()


def get_today_category_totals(user_id, tx_date):
    with get_connection() as conn:
        return conn.execute("""
            SELECT category, SUM(amount) AS total
            FROM transactions
            WHERE user_id = ?
              AND kind = 'expense'
              AND transaction_date = ?
            GROUP BY category
            ORDER BY total DESC
        """, (user_id, tx_date)).fetchall()


def delete_transaction(user_id, transaction_id):
    with get_connection() as conn:
        cursor = conn.execute("""
            DELETE FROM transactions
            WHERE id = ? AND user_id = ?
        """, (transaction_id, user_id))
        return cursor.rowcount > 0


# ==================== SUMMA VA MATN ====================

def format_money(amount):
    return f"{amount:,}".replace(",", " ") + " so'm"


def parse_amount(text):
    value = text.lower().strip().replace("\u00a0", " ")

    match = re.search(
        r"(?<!\w)(\d+(?:[.,]\d+)?)\s*"
        r"(million|mln|ming|тыс|k|so['’ʻ`]?m|sum|сум)"
        r"(?!\w)",
        value,
        re.IGNORECASE,
    )

    if match:
        number = float(match.group(1).replace(",", "."))
        unit = match.group(2).lower()

        if unit in ("million", "mln"):
            multiplier = 1_000_000
        elif unit in ("ming", "тыс", "k"):
            multiplier = 1_000
        else:
            multiplier = 1

        amount = int(number * multiplier)
        return amount if amount > 0 else None

    match = re.search(
        r"(?<!\w)(\d{4,})(?!\w)",
        value,
    )

    if match:
        amount = int(match.group(1))
        return amount if amount > 0 else None

    return None


def detect_category(text, kind):
    text = text.lower()
    categories = {
        "Ovqat": ["ovqat", "taom", "osh", "non", "tushlik", "kechki ovqat", "kafe", "restoran"],
        "Transport": ["benzin", "taksi", "avtobus", "metro", "yo'l", "yo‘l", "transport", "gaz"],
        "Do'kon": ["do'kon", "dokon", "market", "xarid", "sotib oldim"],
        "Uy": ["ijara", "elektr", "svet", "suv", "kommunal", "internet"],
        "Oylik": ["oylik", "maosh", "ish haqi", "ishdan"],
        "Sovg'a": ["sovg'a", "sovga", "tug'ilgan kun"],
        "Ta'lim": ["kitob", "kurs", "o'qish", "maktab"],
        "Salomatlik": ["dori", "shifokor", "dorixona"],
    }

    for category, words in categories.items():
        if any(word in text for word in words):
            return category

    return "Boshqa kirim" if kind == "income" else "Boshqa chiqim"


# --- GROQ AI ORQALI TAHLIL QILISH ---
async def parse_with_groq(text: str):
    if not groq_client:
        return None

    prompt = f"""
    Sen moliyaviy yordamchi botning aqlli tahlilchisisan. Foydalanuvchi kiritgan matndan kirim yoki chiqim operatsiyasini aniqlagin.
    Matn: "{text}"
    
    Quyidagi JSON formatida aniq javob qaytargin (boshqa matn yozma):
    {{
      "kind": "income" yoki "expense" (agar moliyaviy operatsiya bo'lmasa null),
      "amount": raqam ko'rinishidagi summa (masalan 50000),
      "category": "Mos keluvchi kategoriya (masalan: Ovqat, Transport, Do'kon, Oylik, Uy, Boshqa chiqim va hokazo)",
      "note": "Asl izoh matni"
    }}
    Agar bu matn moliyaviy xarajat yoki kirim bo'lmasa, faqat {{"kind": null}} qaytar.
    """
    try:
        response = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        res_data = json.loads(response.choices[0].message.content)
        if res_data.get("kind") in ("income", "expense") and res_data.get("amount"):
            return {
                "kind": res_data["kind"],
                "amount": int(res_data["amount"]),
                "category": res_data.get("category", "Boshqa"),
                "note": text.strip(),
                "tx_date": now_local().date().isoformat(),
            }
    except Exception as e:
        logger.error(f"Groq AI tahlilida xato: {e}")
    
    return None


async def parse_natural_transaction(text):
    # 1. Oldin Groq AI orqali harakat qilib ko'ramiz
    ai_result = await parse_with_groq(text)
    if ai_result:
        return ai_result

    # 2. Agar AI ishlamasa yoki ulanmagan bo'lsa, oddiy qoidalar asosidagi tahlilga o'tamiz
    lowered = text.lower().replace("’", "'").replace("ʻ", "'")

    if re.search(r"\b(minut|minutda|daqiqa|soat)\b", lowered):
        return None

    amount = parse_amount(lowered)
    if amount is None:
        return None

    income_words = ["oylik", "maosh", "daromad", "ish haqi", "pul tushdi", "foyda oldim"]
    expense_words = ["ketdi", "ketti", "sarfladim", "ishlatdim", "to'ladim", "xarajat", "xarj"]

    is_income = any(word in lowered for word in income_words)
    is_expense = any(word in lowered for word in expense_words)

    if is_income and not is_expense:
        kind = "income"
    elif is_expense and not is_income:
        kind = "expense"
    else:
        return None

    tx_date = now_local().date()
    if "kecha" in lowered:
        tx_date -= timedelta(days=1)

    category = detect_category(lowered, kind)
    note = text.strip()

    return {
        "kind": kind,
        "amount": amount,
        "category": category,
        "note": note,
        "tx_date": tx_date.isoformat(),
    }


# ==================== TASDIQLASH ====================

def confirmation_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Tasdiqlash", callback_data="tx:confirm"),
                InlineKeyboardButton(text="❌ Bekor qilish", callback_data="tx:cancel"),
            ]
        ]
    )


async def ask_confirmation(message, user_id, data):
    pending_transactions[user_id] = data
    kind_label = "➕ Kirim" if data["kind"] == "income" else "➖ Chiqim"

    await message.answer(
        "Quyidagi operatsiyani tekshir:\n\n"
        f"📌 Turi: {kind_label}\n"
        f"💵 Summa: {format_money(data['amount'])}\n"
        f"🏷 Kategoriya: {data['category']}\n"
        f"📝 Izoh: {data['note']}\n"
        f"📅 Sana: {data['tx_date']}\n\n"
        "Bazaga saqlashni tasdiqlaysanmi?",
        reply_markup=confirmation_keyboard(),
    )


@router.callback_query(F.data == "tx:confirm")
async def confirm_transaction(callback: CallbackQuery):
    user_id = callback.from_user.id
    data = pending_transactions.pop(user_id, None)

    if not data:
        await callback.answer("Tasdiqlanadigan yozuv topilmadi.", show_alert=True)
        return

    try:
        add_transaction(
            user_id=user_id,
            kind=data["kind"],
            amount=data["amount"],
            category=data["category"],
            note=data["note"],
            tx_date=data["tx_date"],
        )
    except Exception:
        logger.exception("Operatsiyani saqlashda xato")
        await callback.answer("Saqlashda xato yuz berdi.", show_alert=True)
        return

    await callback.answer("Saqlandi!")
    if callback.message:
        await callback.message.edit_text(
            "✅ Operatsiya muvaffaqiyatli saqlandi!\n\n"
            f"{format_money(data['amount'])}\n"
            f"Kategoriya: {data['category']}\n"
            f"Sana: {data['tx_date']}"
        )


@router.callback_query(F.data == "tx:cancel")
async def cancel_transaction(callback: CallbackQuery):
    pending_transactions.pop(callback.from_user.id, None)
    await callback.answer("Bekor qilindi.")
    if callback.message:
        await callback.message.edit_text("❌ Operatsiya saqlanmadi.")


# ==================== START VA YORDAM ====================

@router.message(CommandStart())
async def start_handler(message: Message, state: FSMContext):
    await state.clear()
    first_name = message.from_user.first_name or "do'stim"
    await message.answer(
        f"Assalomu alaykum, {first_name}! 👋\n\n"
        "💎 Smart Moliya Bot'ga xush kelibsiz!\n\n"
        "Kirim va chiqimlarni qayd eting, balans va hisobotlarni ko'ring.\n"
        "Operatsiyalarni tugmalar yoki oddiy matn orqali kiriting.\n"
        "Masalan: 50 ming ovqatga ketdi",
        reply_markup=main_menu,
    )


@router.message(Command("help"))
async def help_handler(message: Message):
    await message.answer(
        "📌 BOT YORDAMI\n\n"
        "➕ Kirim — daromad qo'shish\n"
        "➖ Chiqim — xarajat qo'shish\n"
        "💰 Balans — jami kirim va chiqim\n"
        "📊 Hisobot — bugungi va oylik natijalar\n"
        "📜 Tarix — oxirgi operatsiyalar\n\n"
        "Oddiy matn misollari:\n"
        "• 50 ming ovqatga ketdi\n"
        "• 2 mln oylik oldim"
    )


@router.message(Command("cancel"))
async def cancel_form(message: Message, state: FSMContext):
    await state.clear()
    pending_transactions.pop(message.from_user.id, None)
    await message.answer("Amal bekor qilindi.", reply_markup=main_menu)


# ==================== QO'LDA KIRIM / CHIQIM ====================

async def start_manual_transaction(message, state, kind):
    await state.clear()
    await state.update_data(kind=kind)
    await state.set_state(TransactionForm.amount)
    label = "kirim" if kind == "income" else "chiqim"

    await message.answer(
        f"{'➕ KIRIM' if kind == 'income' else '➖ CHIQIM'}\n\n"
        f"{label.capitalize()} summasini yubor.\n"
        "Masalan: 50000 yoki 50 ming\n\n"
        "Bekor qilish: /cancel"
    )


@router.message(F.text == "➕ Kirim")
async def manual_income(message: Message, state: FSMContext):
    await start_manual_transaction(message, state, "income")


@router.message(F.text == "➖ Chiqim")
async def manual_expense(message: Message, state: FSMContext):
    await start_manual_transaction(message, state, "expense")


@router.message(TransactionForm.amount)
async def manual_amount(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    amount = parse_amount(text)

    if amount is None and text.isdigit() and int(text) > 0:
        amount = int(text)

    if amount is None or amount <= 0:
        await message.answer("Summani tushunmadim. Masalan: 50000, 50 ming yoki 2 mln")
        return

    if amount > 10**12:
        await message.answer("Summa juda katta. Iltimos, tekshirib qayta kiriting.")
        return

    await state.update_data(amount=amount)
    await state.set_state(TransactionForm.note)
    await message.answer("📝 Endi izoh yoki kategoriya yoz.\nMasalan: ovqat, oylik, benzin.\n\nBekor qilish: /cancel")


@router.message(TransactionForm.note)
async def manual_note(message: Message, state: FSMContext):
    note = (message.text or "").strip()
    if not note:
        await message.answer("Iltimos, izoh yoz. Masalan: ovqat yoki oylik.")
        return

    form_data = await state.get_data()
    await state.clear()
    kind = form_data["kind"]

    data = {
        "kind": kind,
        "amount": form_data["amount"],
        "category": detect_category(note, kind),
        "note": note[:300],
        "tx_date": now_local().date().isoformat(),
    }
    await ask_confirmation(message, message.from_user.id, data)


# ==================== BALANS ====================

@router.message(F.text == "💰 Balans")
async def balance_handler(message: Message):
    income, expense = get_balance(message.from_user.id)
    balance = income - expense

    await message.answer(
        "💰 SIZNING BALANSINGIZ\n\n"
        f"🟢 Jami kirim: {format_money(income)}\n"
        f"🔴 Jami chiqim: {format_money(expense)}\n"
        "━━━━━━━━━━━━━━\n"
        f"💵 Qoldiq: {format_money(balance)}",
        reply_markup=main_menu,
    )


# ==================== HISOBOT ====================

@router.message(F.text == "📊 Hisobot")
async def report_handler(message: Message):
    user_id = message.from_user.id
    today = now_local().date()
    month_start = today.replace(day=1)

    today_income, today_expense = get_period_totals(user_id, today.isoformat(), today.isoformat())
    month_income, month_expense = get_period_totals(user_id, month_start.isoformat(), today.isoformat())
    categories = get_today_category_totals(user_id, today.isoformat())

    text = (
        "📊 MOLIYAVIY HISOBOT\n\n"
        "📅 BUGUN\n"
        f"🟢 Kirim: {format_money(today_income)}\n"
        f"🔴 Chiqim: {format_money(today_expense)}\n\n"
        f"📆 SHU OY ({month_start.strftime('%d.%m')} — {today.strftime('%d.%m.%Y')})\n"
        f"🟢 Kirim: {format_money(month_income)}\n"
        f"🔴 Chiqim: {format_money(month_expense)}\n"
        f"📈 Oylik farq: {format_money(month_income - month_expense)}"
    )

    if categories:
        text += "\n\n🏷 BUGUNGI CHIQIM KATEGORIYALARI:"
        for row in categories:
            text += f"\n• {row['category']}: {format_money(row['total'])}"

    await message.answer(text, reply_markup=main_menu)


# ==================== TARIX VA O'CHIRISH ====================

@router.message(F.text == "📜 Tarix")
async def history_handler(message: Message):
    rows = get_recent_transactions(message.from_user.id)
    if not rows:
        await message.answer("📜 Hozircha operatsiyalar yo'q.", reply_markup=main_menu)
        return

    text = "📜 OXIRGI OPERATSIYALAR\n\n"
    for row in rows:
        sign = "+" if row["kind"] == "income" else "−"
        text += (
            f"#{row['id']} | {row['transaction_date']}\n"
            f"{sign}{format_money(row['amount'])}\n"
            f"🏷 {row['category']} | {row['note']}\n\n"
        )

    text += "Operatsiyani o'chirish uchun /delete ID yoz.\nMasalan: /delete 12"
    await message.answer(text, reply_markup=main_menu)


@router.message(Command("delete"))
async def delete_handler(message: Message):
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Foydalanish: /delete ID\nMasalan: /delete 12")
        return

    transaction_id = int(parts[1])
    with get_connection() as conn:
        row = conn.execute("""
            SELECT id, kind, amount, category, note
            FROM transactions
            WHERE id = ? AND user_id = ?
        """, (transaction_id, message.from_user.id)).fetchone()

    if not row:
        await message.answer("Bu ID bilan o'zingizga tegishli operatsiya topilmadi.")
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🗑 Ha, o'chir", callback_data=f"delete:yes:{transaction_id}"),
                InlineKeyboardButton(text="❌ Bekor qilish", callback_data="delete:no"),
            ]
        ]
    )
    await message.answer(
        f"Quyidagi operatsiyani o'chiraymi?\n\nID: #{row['id']}\nSumma: {format_money(row['amount'])}\nKategoriya: {row['category']}\nIzoh: {row['note']}",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("delete:"))
async def delete_callback(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) < 2:
        await callback.answer()
        return

    if parts[1] == "no":
        await callback.answer("Bekor qilindi.")
        if callback.message:
            await callback.message.edit_text("❌ O'chirish bekor qilindi.")
        return

    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    transaction_id = int(parts[2])
    deleted = delete_transaction(callback.from_user.id, transaction_id)
    await callback.answer("Operatsiya o'chirildi." if deleted else "Operatsiya topilmadi.")
    if callback.message:
        await callback.message.edit_text(
            "🗑 Operatsiya o'chirildi." if deleted else "Operatsiya topilmadi yoki oldin o'chirilgan."
        )


# ==================== AI BO'LIMI ====================

@router.message(F.text == "🤖 AI yordamchi")
async def ai_handler(message: Message):
    await message.answer(
        "🤖 **AI Moliya Yordamchisi**\n\n"
        "Menga ixtiyoriy matn shaklida xarajat yoki daromadingizni yozing "
        "(masalan: *'Kecha 45 mingga nonushta qildim'*), bot uni sun'iy intellekt orqali o'zi tahlil qilib tasdiqlashga chiqaradi!",
        parse_mode="Markdown"
    )


# ==================== SOZLAMALAR ====================

@router.message(F.text == "⚙️ Sozlamalar")
async def settings_handler(message: Message):
    await message.answer(
        "⚙️ SOZLAMALAR\n\n"
        "Valyuta: so'm\n"
        "Vaqt zonasi: Asia/Tashkent\n"
        "Ma'lumotlar bazasi: SQLite\n"
        "AI Modeli: Groq Llama 3\n\n"
        "Maxfiy kalitlaringizni hech kimga yubormang."
    )


# ==================== ODDIY MATN TAHLILI ====================

@router.message(F.text)
async def natural_message_handler(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        return

    data = await parse_natural_transaction(text)

    if data is not None:
        await ask_confirmation(message, message.from_user.id, data)
        return

    await message.answer(
        "Xabaringni tushunishga harakat qildim, lekin operatsiya turini "
        "yoki summani ishonchli aniqlay olmadim.\n\n"
        "Masalan:\n"
        "• 50 ming ovqatga ketdi\n"
        "• 2 mln oylik oldim\n\n"
        "Yoki menyudan ➕ Kirim / ➖ Chiqim tanla.",
        reply_markup=main_menu,
    )


# ==================== ISHGA TUSHIRISH ====================

async def main():
    if not BOT_TOKEN or BOT_TOKEN == "BU_YERGA_TELEGRAM_BOT_TOKEN":
        raise RuntimeError(".env faylida BOT_TOKEN to'g'ri kiritilmagan.")

    init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    try:
        me = await bot.get_me()
        logger.info("Bot ishga tushdi: @%s", me.username)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot to'xtatildi.")
