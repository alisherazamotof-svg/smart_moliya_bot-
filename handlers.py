from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime, timedelta

from database import async_session, User, add_transaction, get_balance, get_recent_transactions, delete_transaction_by_id
from ai_service import parse_financial_message
from keyboards import get_main_keyboard

router = Router()

class Form(StatesGroup):
    waiting_for_input = State()

@router.message(F.text == "/start")
async def cmd_start(message: Message):
    async with async_session() as session:
        user = await session.get(User, message.from_user.id)
        if not user:
            new_user = User(telegram_id=message.from_user.id, full_name=message.from_user.full_name)
            session.add(new_user)
            await session.commit()
            
    await message.answer(
        f"Assalomu alaykum, {message.from_user.first_name}!\n"
        "Men sizning shaxsiy **Smart Moliya Bot**ingizman.\n\n"
        "Mengativ istalgan moliyaviy operatsiyani erkin yozing:\n"
        "• *'50 ming ovqatga ketti'*\n"
        "• *'2 mln oylik oldim'*\n"
        "• *'Bugun qancha pul ishlatdim?'*\n\n"
        "Yoki pastdagi tugmalardan foydalaning:",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )

@router.message(F.text == "💰 Balans")
async def show_balance(message: Message):
    balance, income, expense = await get_balance(message.from_user.id)
    text = (
        f"📊 **Sizning moliyaviy balansingiz:**\n\n"
        f"💚 Jami kirim: `{income:,.0f}` so'm\n"
        f"❤️ Jami chiqim: `{expense:,.0f}` so'm\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💼 **Sof balans: `{balance:,.0f}` so'm**"
    )
    await message.answer(text, parse_mode="Markdown")

@router.message(F.text == "📜 Tarix")
async def show_history(message: Message):
    txs = await get_recent_transactions(message.from_user.id, limit=10)
    if not txs:
        await message.answer("Sizda hali operatsiyalar tarixi mavjud emas.")
        return
    
    text = "📜 **Oxirgi operatsiyalaringiz:**\n\n"
    for tx in txs:
        emoji = "💚" if tx.type == "income" else "❤️"
        date_str = tx.date.strftime("%d.%m.%Y %H:%M")
        text += f"{emoji} **{tx.amount:,.0f} {tx.currency}** | {tx.category}\n   └ *{tx.note or 'Izoh yo‘q'}* ({date_str})\n\n"
    
    await message.answer(text, parse_mode="Markdown")

@router.message(F.text == "🤖 AI yordamchi")
async def ai_helper_info(message: Message):
    await message.answer(
        "🤖 **AI Moliya Yordamchisi faol!**\n\n"
        "Mengayoxshi tushunishim uchun oddiy tilda yozing:\n"
        "• *'Oxirgi 7 kunda eng ko‘p pul nimaga ketdi?'*\n"
        "• *'Do‘stimga 100 ming qarz berdim'*\n"
        "• *'10 ming emas, 15 ming edi'*\n"
        "Hohlagan matningizni yuboring, men tahlil qilaman!"
    )

@router.message()
async def handle_natural_language(message: Message):
    text = message.text
    if not text:
        return

    # AI tahlili
    parsed = await parse_financial_message(text)
    intent = parsed.get("intent")
    confidence = parsed.get("confidence", 0.0)

    if intent == "balance_query":
        await show_balance(message)
        return
    
    if intent == "report_query":
        balance, income, expense = await get_balance(message.from_user.id)
        await message.answer(f"📈 **Hisobot tahlili:**\nJami daromad: {income:,.0f} so'm\nJami xarajat: {expense:,.0f} so'm")
        return

    if parsed.get("clarification_needed") or confidence < 0.6:
        clarification = parsed.get("clarification_message") or "Kechirasiz, xabaringizni to‘liq tushunmadim. Miqdor va operatsiya turini aniqroq yozing."
        await message.answer(f"⚠️ {clarification}")
        return

    if intent in ["income", "expense"]:
        amount = parsed.get("amount")
        category = parsed.get("category", "umumiy")
        currency = parsed.get("currency", "so'm")
        note = parsed.get("note", text)

        if not amount:
            await message.answer("Summa aniqlanmadi. Iltimos, miqdorini aniq yozing.")
            return

        t_type = "income" if intent == "income" else "expense"
        await add_transaction(message.from_user.id, t_type, float(amount), category, note)
        
        emoji = "💚 Kirim" if t_type == "income" else "❤️ Chiqim"
        await message.answer(
            f"✅ **Muvaffaqiyatli saqlandi!**\n\n"
            f"Turi: {emoji}\n"
            f"Summa: **{amount:,.0f} {currency}**\n"
            f"Kategoriya: `{category}`\n"
            f"Izoh: _{note}_",
            parse_mode="Markdown"
        )
    else:
        await message.answer("Tushunarli, lekin hozircha bu buyruqni to'g'ridan-to'g'ri bajara olmayman. Xarajat yoki kirim sifatida yozib yuboring!")
