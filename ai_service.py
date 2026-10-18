import os
import json
from groq import Groq
from dotenv import load_dotenv

load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

SYSTEM_PROMPT = """
Sen moliyaviy matnlarni tahlil qiluvchi AI agentisan. Foydalanuvchining o'zbek yoki rus tilidagi xabarini o'qib, uni qat'iy JSON formatida qaytarishing kerak.
JSON sxemasi:
{
  "intent": "income" | "expense" | "debt_give" | "debt_take" | "debt_repay" | "balance_query" | "report_query" | "delete_last" | "unknown",
  "amount": float or null,
  "currency": "so'm" yoki boshqa valyuta,
  "category": "ovqat", "transport", "ish haqi", "qarz", "kommunal", "boshqa" kabilar,
  "date_modifier": "bugun" | "kecha" | "aniq_sana",
  "person": qarz berilgan yoki olingan shaxs ismi yoki null,
  "note": "asl izoh matni",
  "confidence": 0.0 dan 1.0 gacha ishonchlilik darajasi,
  "clarification_needed": true/false,
  "clarification_message": "Agar ma'lumot yetarli bo'lmasa, foydalanuvchiga beriladigan savol"
}
Diqqat:
- Agar "10 min ishladim" desa va pul ma'nosi bo'lmasa, intent="unknown", clarification_needed=true qil.
- Agar "10 min ishlatdim" desa, pul ma'nosida 10000 so'm deb tushun.
- Hech qanday qo'shimcha matn yozma, faqat toza JSON qaytar!
"""

async def parse_financial_message(text: str) -> dict:
    if not client:
        return {"intent": "unknown", "confidence": 0.0, "note": text}
    
    try:
        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text}
            ],
            response_format={"type": "json_object"},
            temperature=0.1
        )
        response_text = completion.choices[0].message.content
        return json.loads(response_text)
    except Exception as e:
        print(f"AI Error: {e}")
        return {"intent": "unknown", "confidence": 0.0, "note": text}
