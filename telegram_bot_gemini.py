import os
import json
import asyncio

import google.generativeai as genai

from telegram import Update, Poll
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes, PollAnswerHandler
)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

user_histories = {}
user_quizzes = {}
poll_data = {}

SYSTEM_PROMPT = """Sen o'zbek tilida ishlaydigan aqlli shaxsiy yordamchisan.
Foydalanuvchiga har qanday savolda yordam ber: tarjima, matn yozish, hisob-kitob, maslahat.
Doimo o'zbek tilida javob ber. Qisqa, aniq va foydali bo'l."""


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    try:
        return pdf_bytes.decode('utf-8', errors='ignore')[:8000]
    except Exception:
        return pdf_bytes.decode('latin-1', errors='ignore')[:8000]


def ask_gemini(messages: list) -> str:
    history = []
    for msg in messages[:-1]:
        role = "user" if msg["role"] == "user" else "model"
        history.append({"role": role, "parts": [msg["content"]]})
    chat = model.start_chat(history=history)
    last = messages[-1]["content"]
    if len(messages) == 1:
        last = SYSTEM_PROMPT + "\n\n" + last
    response = chat.send_message(last)
    return response.text


def generate_quiz(text: str, num: int = 5) -> list:
    prompt = f"""Quyidagi matndan {num} ta test savoli tuz.
FAQAT JSON qaytар, boshqa hech narsa yozma.
FORMAT:
[
  {{
    "question": "Savol?",
    "options": ["A", "B", "C", "D"],
    "correct": 0,
    "explanation": "Izoh"
  }}
]
MATN:
{text}"""
    response = model.generate_content(prompt)
    raw = response.text.strip()
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()
    return json.loads(raw)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_histories[update.effective_user.id] = []
    await update.message.reply_text(
        "Salom! Men sizning shaxsiy AI yordamchingizman!\n\n"
        "Oddiy suhbat: Har qanday savol yozing\n"
        "PDF Test: PDF fayl yuboring va avtomatik test oling!\n\n"
        "Buyruqlar:\n"
        "/start - Qayta boshlash\n"
        "/clear - Suhbatni tozalash\n"
        "/help - Yordam"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Nima qila olaman:\n\n"
        "Tarjima: 'inglizchaga tarjima qil: ...'\n"
        "Matn: '... haqida matn yoz'\n"
        "Hisob: 'quyidagini hisobla: ...'\n"
        "Maslahat: 'maslahat ber: ...'\n\n"
        "PDF Test:\n"
        "1. PDF fayl yuboring\n"
        "2. Nechta savol (1-10) yozing\n"
        "3. Test boshlanadi!\n"
        "4. Oxirida natija chiqadi"
    )


async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_histories[update.effective_user.id] = []
    await update.message.reply_text("Suhbat tarixi tozalandi!")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("Faqat PDF fayl yuboring!")
        return
    await update.message.reply_text("PDF qabul qilindi! O'qilyapti...")
    try:
        f = await context.bot.get_file(doc.file_id)
        pdf_bytes = await f.download_as_bytearray()
        text = extract_text_from_pdf(bytes(pdf_bytes))
        if len(text.strip()) < 50:
            await update.message.reply_text("PDF dan matn o'qib bolmadi.")
            return
        context.user_data["pdf_text"] = text
        context.user_data["waiting_count"] = True
        await update.message.reply_text(
            f"PDF o'qildi! Matn: {len(text)} belgi\n\n"
            "Nechta savol tuzishni istaysiz? (1-10)"
        )
    except Exception as e:
        await update.message.reply_text(f"Xatolik: {str(e)}")


async def send_question(context, user_id, chat_id):
    quiz = user_quizzes.get(user_id)
    if not quiz:
        return
    idx = quiz["current"]
    total = len(quiz["questions"])
    if idx >= total:
        correct = quiz["correct"]
        percent = int((correct / total) * 100)
        emoji = "🏆" if percent >= 80 else "👍" if percent >= 60 else "📚"
        text = (
            f"{emoji} Test yakunlandi!\n\n"
            f"Natija: {correct}/{total}\n"
            f"Foiz: {percent}%\n\n"
        )
        if percent >= 80:
            text += "Ajoyib! Bilimlaringiz zo'r!"
        elif percent >= 60:
            text += "Yaxshi! Biroz takrorlang."
        else:
            text += "Mavzuni qayta o'rganing!"
        del user_quizzes[user_id]
        await context.bot.send_message(chat_id=chat_id, text=text)
        return
    q = quiz["questions"][idx]
    msg = await context.bot.send_poll(
        chat_id=chat_id,
        question=f"{idx+1}/{total}. {q['question']}",
        options=q["options"],
        type=Poll.QUIZ,
        correct_option_id=q["correct"],
        explanation=q.get("explanation", ""),
        is_anonymous=False,
        open_period=30
    )
    poll_data[msg.poll.id] = {
        "user_id": user_id,
        "chat_id": chat_id,
        "correct": q["correct"]
    }


async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ans = update.poll_answer
    data = poll_data.get(ans.poll_id)
    if not data:
        return
    quiz = user_quizzes.get(ans.user.id)
    if not quiz:
        return
    if ans.option_ids and ans.option_ids[0] == data["correct"]:
        quiz["correct"] += 1
    quiz["current"] += 1
    del poll_data[ans.poll_id]
    await asyncio.sleep(1)
    await send_question(context, ans.user.id, data["chat_id"])


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    if context.user_data.get("waiting_count"):
        try:
            count = int(text)
            if count < 1 or count > 10:
                await update.message.reply_text("1 dan 10 gacha son kiriting!")
                return
            context.user_data["waiting_count"] = False
            pdf_text = context.user_data.get("pdf_text", "")
            await update.message.reply_text(f"{count} ta savol tuzilmoqda... Kuting!")
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            questions = generate_quiz(pdf_text, count)
            user_quizzes[user_id] = {"questions": questions, "current": 0, "correct": 0}
            await update.message.reply_text(
                f"{len(questions)} ta savol tayyor!\nTest boshlanmoqda... 30 soniya vaqtingiz bor!"
            )
            await asyncio.sleep(1)
            await send_question(context, user_id, chat_id)
            return
        except ValueError:
            await update.message.reply_text("Faqat son kiriting (1-10)!")
            return

    if user_id not in user_histories:
        user_histories[user_id] = []
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    user_histories[user_id].append({"role": "user", "content": text})
    if len(user_histories[user_id]) > 20:
        user_histories[user_id] = user_histories[user_id][-20:]
    try:
        reply = ask_gemini(user_histories[user_id])
        user_histories[user_id].append({"role": "assistant", "content": reply})
        await update.message.reply_text(reply)
    except Exception as e:
        await update.message.reply_text(f"Xatolik: {str(e)}")


def main():
    print("Bot ishga tushmoqda...")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_document))
    app.add_handler(PollAnswerHandler(handle_poll_answer))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot ishga tushdi!")
    app.run_polling()


if __name__ == "__main__":
    main()
