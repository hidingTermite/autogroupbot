import os
import asyncio
import sqlite3
from contextlib import asynccontextmanager

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError
from telethon.tl import functions

from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import Update

# ========= CONFIG =========
BOT_TOKEN = os.getenv("BOT_TOKEN")  # set this env var before running

# Auto messages to send in each group (exactly 6, as requested)
AUTO_MESSAGES = [
    "Welcome to the group!",
    "Check pinned messages for rules.",
    "Invite your friends!",
    "Have fun chatting!",
    "Be respectful to everyone.",
    "Enjoy your stay!",
]

# ========= DATABASE =========
con = sqlite3.connect("users.db", check_same_thread=False)
cur = con.cursor()
cur.execute(
    """CREATE TABLE IF NOT EXISTS users (
        tg_id INTEGER PRIMARY KEY,
        api_id INTEGER,
        api_hash TEXT,
        session TEXT
    )"""
)
con.commit()


# ========= HELPERS =========
@asynccontextmanager
async def telethon_for_user(tg_id: int):
    """
    Opens a Telethon client for a stored user session, yields it, and closes it.
    Raises ValueError if the user is not connected yet.
    """
    cur.execute("SELECT api_id, api_hash, session FROM users WHERE tg_id=?", (tg_id,))
    row = cur.fetchone()
    if not row or row[2] is None:
        raise ValueError("You must /connect and /login first.")
    api_id, api_hash, string_session = row
    client = TelegramClient(StringSession(string_session), api_id, api_hash)
    await client.connect()
    try:
        yield client
    finally:
        await client.disconnect()


def args_or_none(context: ContextTypes.DEFAULT_TYPE, n: int):
    if not context.args or len(context.args) < n:
        return None
    return context.args


# ========= BOT COMMANDS =========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome!\n\n"
        "To connect your own Telegram account (not the bot account):\n"
        "1) Get API ID & API Hash from https://my.telegram.org\n"
        "2) Send: /connect <api_id> <api_hash>\n"
        "3) Then: /login <your_phone_number>\n"
        "4) When you receive the code: /code <12345>\n"
        "5) If 2FA is enabled: /2fa <your_password>\n\n"
        "After that, create groups: /creategroups <number>\n"
        "➡️ Groups are private, history visible, and will receive 6 auto messages."
    )


async def connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    a = args_or_none(context, 2)
    if not a:
        await update.message.reply_text("❌ Usage: /connect <api_id> <api_hash>")
        return

    tg_id = update.effective_user.id
    try:
        api_id = int(a[0])
        api_hash = a[1]
        cur.execute(
            "REPLACE INTO users (tg_id, api_id, api_hash, session) VALUES (?, ?, ?, ?)",
            (tg_id, api_id, api_hash, None),
        )
        con.commit()
        await update.message.reply_text("✅ API saved. Now send: /login <your_phone_number>")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to save API: {e}")


async def login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    a = args_or_none(context, 1)
    if not a:
        await update.message.reply_text("❌ Usage: /login <your_phone_number>")
        return

    tg_id = update.effective_user.id
    phone = a[0]

    cur.execute("SELECT api_id, api_hash FROM users WHERE tg_id=?", (tg_id,))
    row = cur.fetchone()
    if not row:
        await update.message.reply_text("❌ Use /connect first.")
        return

    api_id, api_hash = row
    client = TelegramClient(StringSession(), api_id, api_hash)

    try:
        await client.connect()
        await client.send_code_request(phone)
        # keep client alive in user_data for next step (/code)
        context.user_data["client"] = client
        context.user_data["phone"] = phone
        await update.message.reply_text("📩 Code sent. Now send: /code <12345>")
    except Exception as e:
        await client.disconnect()
        await update.message.reply_text(f"❌ Error sending code: {e}")


async def code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    a = args_or_none(context, 1)
    if not a:
        await update.message.reply_text("❌ Usage: /code <12345>")
        return

    tg_id = update.effective_user.id
    otp = a[0]
    client: TelegramClient = context.user_data.get("client")
    phone = context.user_data.get("phone")

    if not client or not phone:
        await update.message.reply_text("❌ Start with /login first.")
        return

    try:
        await client.sign_in(phone, otp)
        if not await client.is_user_authorized():
            # 2FA required
            await update.message.reply_text("🔐 2FA detected. Send: /2fa <password>")
            return

        session = client.session.save()
        cur.execute("UPDATE users SET session=? WHERE tg_id=?", (session, tg_id))
        con.commit()

        await update.message.reply_text("✅ Connected! Use /creategroups <n>")
    except SessionPasswordNeededError:
        await update.message.reply_text("🔐 2FA enabled. Send: /2fa <password>")
    except PhoneCodeInvalidError:
        await update.message.reply_text("❌ Invalid code. Try again with /code <12345>.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
    finally:
        await client.disconnect()
        context.user_data.pop("client", None)
        context.user_data.pop("phone", None)


async def twofa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    a = args_or_none(context, 1)
    if not a:
        await update.message.reply_text("❌ Usage: /2fa <password>")
        return

    tg_id = update.effective_user.id

    # We don't rely on context.user_data here; we re-open the same client to finish login
    cur.execute("SELECT api_id, api_hash FROM users WHERE tg_id=?", (tg_id,))
    row = cur.fetchone()
    if not row:
        await update.message.reply_text("❌ Use /connect and /login first.")
        return

    api_id, api_hash = row
    client = TelegramClient(StringSession(), api_id, api_hash)

    try:
        await client.connect()
        # If you're here, Telethon expects just the password after a previous sign_in attempt.
        await client.sign_in(password=a[0])

        session = client.session.save()
        cur.execute("UPDATE users SET session=? WHERE tg_id=?", (session, tg_id))
        con.commit()

        await update.message.reply_text("✅ 2FA success! Now use /creategroups <n>")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
    finally:
        await client.disconnect()


async def creategroups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    a = args_or_none(context, 1)
    if not a:
        await update.message.reply_text("❌ Usage: /creategroups <number>")
        return

    tg_id = update.effective_user.id
    try:
        n = max(1, int(a[0]))
    except ValueError:
        await update.message.reply_text("❌ Please provide a valid number, e.g. /creategroups 3")
        return

    try:
        links = []
        async with telethon_for_user(tg_id) as client:
            for i in range(n):
                # 1) Create a PRIVATE supergroup (megagroup=True and no username)
                result = await client(
                    functions.channels.CreateChannelRequest(
                        title=f"AutoGroup {i+1}",
                        about="Created by AutoBot",
                        megagroup=True  # supergroup
                    )
                )
                group = result.chats[0]

                # 2) Ensure chat history is VISIBLE to new members (explicitly)
                # enabled=True would HIDE history; we want it visible:
                await client(functions.channels.TogglePreHistoryHidden(peer=group, enabled=False))

                # 3) Send the 6 automatic messages
                for msg in AUTO_MESSAGES:
                    await client.send_message(group, msg)

                # 4) Export a private invite link
                export = await client(functions.messages.ExportChatInviteRequest(peer=group))
                links.append(export.link)

        await update.message.reply_text("✅ Created groups:\n" + "\n".join(links))
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to create groups: {e}")


# ========= ENTRYPOINT =========
def main():
    if not BOT_TOKEN:
        raise SystemExit("Set BOT_TOKEN environment variable first.")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("connect", connect))
    app.add_handler(CommandHandler("login", login))
    app.add_handler(CommandHandler("code", code))
    app.add_handler(CommandHandler("2fa", twofa))
    app.add_handler(CommandHandler("creategroups", creategroups))

    # run_polling manages the asyncio loop; do NOT wrap in asyncio.run()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
