import os
import asyncio
import sqlite3
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram import Update

# Your bot’s credentials (from BotFather + my.telegram.org)
BOT_TOKEN = os.getenv("BOT_TOKEN")

# SQLite DB
con = sqlite3.connect("users.db", check_same_thread=False)
cur = con.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS users (
    tg_id INTEGER PRIMARY KEY,
    api_id INTEGER,
    api_hash TEXT,
    session TEXT
)""")

# ===== BOT COMMANDS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome! To connect your account:\n"
        "1. Get your API ID & Hash from https://my.telegram.org\n"
        "2. Send: /connect <api_id> <api_hash>"
    )

async def connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    try:
        api_id = int(context.args[0])
        api_hash = context.args[1]

        # Save temporarily until login is complete
        cur.execute("REPLACE INTO users (tg_id, api_id, api_hash, session) VALUES (?, ?, ?, ?)",
                    (tg_id, api_id, api_hash, None))
        con.commit()

        await update.message.reply_text("✅ API stored. Now send: /login <your_phone_number>")
    except Exception:
        await update.message.reply_text("❌ Usage: /connect <api_id> <api_hash>")

async def login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    try:
        phone = context.args[0]

        # Load API from DB
        cur.execute("SELECT api_id, api_hash FROM users WHERE tg_id=?", (tg_id,))
        row = cur.fetchone()
        if not row:
            await update.message.reply_text("❌ Use /connect first.")
            return
        api_id, api_hash = row

        client = TelegramClient(StringSession(), api_id, api_hash)
        await client.connect()
        sent = await client.send_code_request(phone)
        context.user_data["client"] = client
        context.user_data["phone"] = phone
        await update.message.reply_text("📩 Code sent to your Telegram app/SMS. Now send: /code <12345>")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    try:
        otp = context.args[0]
        client: TelegramClient = context.user_data.get("client")
        phone = context.user_data.get("phone")

        await client.sign_in(phone, otp)

        # If account has 2FA password
        if await client.is_user_authorized() is False:
            await update.message.reply_text("🔐 This account has 2FA enabled. Send: /2fa <your_password>")
            context.user_data["waiting_2fa"] = True
            return

        session = client.session.save()
        cur.execute("UPDATE users SET session=? WHERE tg_id=?", (session, tg_id))
        con.commit()

        await update.message.reply_text("✅ Connected! You can now use /creategroups <n>")
        await client.disconnect()
    except SessionPasswordNeededError:
        await update.message.reply_text("🔐 Send your 2FA password: /2fa <password>")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def twofa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    try:
        password = context.args[0]
        client: TelegramClient = context.user_data.get("client")
        phone = context.user_data.get("phone")

        await client.sign_in(password=password)
        session = client.session.save()
        cur.execute("UPDATE users SET session=? WHERE tg_id=?", (session, tg_id))
        con.commit()

        await update.message.reply_text("✅ 2FA success! You can now use /creategroups <n>")
        await client.disconnect()
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def creategroups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    cur.execute("SELECT api_id, api_hash, session FROM users WHERE tg_id=?", (tg_id,))
    row = cur.fetchone()
    if not row or row[2] is None:
        await update.message.reply_text("❌ You must /connect and /login first.")
        return

    api_id, api_hash, string_session = row
    try:
        n = int(context.args[0])
    except:
        await update.message.reply_text("❌ Usage: /creategroups <number>")
        return

    client = TelegramClient(StringSession(string_session), api_id, api_hash)
    await client.start()
    links = []
    for i in range(n):
        result = await client.create_supergroup(f"AutoGroup {i+1}", "Created by AutoBot")
        invite = await client.export_chat_invite_link(result.id)
        links.append(invite)

    await client.disconnect()
    await update.message.reply_text("\n".join(links))

# ===== BOT START =====
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("connect", connect))
    app.add_handler(CommandHandler("login", login))
    app.add_handler(CommandHandler("code", code))
    app.add_handler(CommandHandler("2fa", twofa))
    app.add_handler(CommandHandler("creategroups", creategroups))
    app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
