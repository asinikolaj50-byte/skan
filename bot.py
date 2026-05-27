#!/usr/bin/env python3
"""
OSINT Focused Bot
Проверяет: Twitter/X, LinkedIn, Facebook, CryptoRank, OpenSea
Режимы: email (forgot-password) и username (прямая проверка профиля)
"""

import asyncio
import os
import re
import sys
import time

import httpx

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)
from telegram.constants import ParseMode

EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Прямые ссылки на профиль — всегда показываем пользователю
PROFILE_URLS = {
    "Twitter/X":  "https://x.com/{u}",
    "LinkedIn":   "https://www.linkedin.com/in/{u}",
    "Facebook":   "https://www.facebook.com/{u}",
    "CryptoRank": "https://cryptorank.io/profile/{u}",
    "OpenSea":    "https://opensea.io/{u}",
}

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def is_email(t: str) -> bool:
    return bool(EMAIL_RE.match(t.strip()))


# ─── KEYBOARDS ─────────────────────────────────────────────────────────────

def kb_main():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📧 Проверить Email", callback_data="mode:email"),
            InlineKeyboardButton("👤 Найти Username", callback_data="mode:user"),
        ],
        [
            InlineKeyboardButton("🔀 Оба сразу (email + ник)", callback_data="mode:both"),
        ],
        [
            InlineKeyboardButton("📄 Загрузить список (.txt)", callback_data="mode:file"),
            InlineKeyboardButton("❓ Помощь", callback_data="help"),
        ],
    ])


def kb_back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back:main")]])


def kb_cancel():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="back:main")]])


# ─── USERNAME CHECKERS ─────────────────────────────────────────────────────

async def check_twitter(username: str, client: httpx.AsyncClient) -> dict:
    """Проверяет существование профиля на Twitter/X."""
    url = f"https://x.com/{username}"
    try:
        r = await client.get(url)
        if r.status_code == 200:
            text = r.text
            # X возвращает 200 и для несуществующих профилей, проверяем контент
            if (f'"screen_name":"{username.lower()}"' in text.lower()
                    or f'@{username}' in text
                    or '"user":{"id"' in text):
                return {"found": True, "url": url}
            # Если страница содержит "This account doesn't exist"
            if "this account" in text.lower() and "exist" in text.lower():
                return {"found": False}
            # Неопределённо — X блокирует без авторизации, даём ссылку
            return {"found": "maybe", "url": url, "note": "Проверь вручную (X блокирует боты)"}
        if r.status_code == 404:
            return {"found": False}
        return {"error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"error": str(e)[:80]}


async def check_linkedin(username: str, client: httpx.AsyncClient) -> dict:
    """Проверяет LinkedIn профиль."""
    url = f"https://www.linkedin.com/in/{username}"
    try:
        r = await client.get(url)
        if r.status_code == 200:
            if "authwall" in str(r.url) or "login" in str(r.url):
                # LinkedIn редиректит на логин — профиль может существовать
                return {"found": "maybe", "url": url, "note": "Требует авторизацию"}
            return {"found": True, "url": url}
        if r.status_code in (404, 999):
            return {"found": False}
        return {"error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"error": str(e)[:80]}


async def check_facebook(username: str, client: httpx.AsyncClient) -> dict:
    """Facebook блокирует ботов — даём ссылку для ручной проверки."""
    url = f"https://www.facebook.com/{username}"
    return {"found": "manual", "url": url}


async def check_cryptorank(username: str, client: httpx.AsyncClient) -> dict:
    """Проверяет профиль на CryptoRank."""
    for path in [f"https://cryptorank.io/profile/{username}",
                 f"https://cryptorank.io/user/{username}"]:
        try:
            r = await client.get(path)
            if r.status_code == 200:
                if "not found" not in r.text.lower() and "404" not in r.text:
                    return {"found": True, "url": path}
            elif r.status_code == 404:
                continue
        except Exception:
            pass
    return {"found": False}


async def check_opensea(username: str, client: httpx.AsyncClient) -> dict:
    """Проверяет профиль на OpenSea."""
    url = f"https://opensea.io/{username}"
    try:
        r = await client.get(url)
        if r.status_code == 200:
            text = r.text.lower()
            if "page not found" in text or "not found" in text or "404" in text:
                return {"found": False}
            return {"found": True, "url": url}
        if r.status_code == 404:
            return {"found": False}
        return {"error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"error": str(e)[:80]}


async def scan_username_focused(username: str) -> tuple[dict, float]:
    """Запускает все 5 проверок параллельно."""
    start = time.time()
    async with httpx.AsyncClient(
        headers=BROWSER_HEADERS, timeout=15,
        follow_redirects=True
    ) as client:
        results = await asyncio.gather(
            check_twitter(username, client),
            check_linkedin(username, client),
            check_facebook(username, client),
            check_cryptorank(username, client),
            check_opensea(username, client),
        )
    elapsed = time.time() - start
    return dict(zip(
        ["Twitter/X", "LinkedIn", "Facebook", "CryptoRank", "OpenSea"],
        results
    )), elapsed


# ─── EMAIL CHECKERS ────────────────────────────────────────────────────────

def _email_scan_sync(email: str) -> tuple[dict, float]:
    """
    Проверяет email через forgot-password на Twitter, Facebook, LinkedIn.
    Использует holehe-модули где есть, иначе кастомные проверки.
    """
    import trio
    import httpx as _httpx

    async def _run():
        results = {}
        async with _httpx.AsyncClient(
            headers=BROWSER_HEADERS, timeout=15,
            follow_redirects=True
        ) as client:
            tasks = {
                "Twitter/X": _check_email_twitter(email, client),
                "Facebook":  _check_email_facebook(email, client),
                "LinkedIn":  _check_email_linkedin(email, client),
            }
            # Запускаем параллельно через trio
            out = {}
            async with trio.open_nursery() as nursery:
                for name, coro in tasks.items():
                    async def _run_one(n=name, c=coro):
                        out[n] = await c
                    nursery.start_soon(_run_one)
        return out

    start = time.time()
    results = trio.run(_run)
    elapsed = time.time() - start
    return results, elapsed


async def _check_email_twitter(email: str, client: httpx.AsyncClient) -> dict:
    """Проверяет зарегистрирован ли email на Twitter/X."""
    try:
        # Используем holehe-модуль если доступен
        from holehe.modules.social_media.twitter import twitter as holehe_twitter
        out = []
        await holehe_twitter(email, client, out)
        if out:
            r = out[0]
            if r.get("exists"):
                return {"found": True, "extra": r.get("emailrecovery") or ""}
            if r.get("rateLimit"):
                return {"found": "rate_limit"}
            return {"found": False}
    except Exception:
        pass
    # Фолбэк — прямая проверка
    try:
        r = await client.post(
            "https://api.twitter.com/i/users/email_available.json",
            data={"email": email},
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("valid") is False:
                return {"found": True}
            return {"found": False}
    except Exception as e:
        return {"error": str(e)[:60]}
    return {"error": "Не удалось проверить"}


async def _check_email_facebook(email: str, client: httpx.AsyncClient) -> dict:
    """Проверяет зарегистрирован ли email на Facebook."""
    try:
        from holehe.modules.social_media.facebook import facebook as holehe_fb
        out = []
        await holehe_fb(email, client, out)
        if out:
            r = out[0]
            if r.get("exists"):
                return {"found": True, "extra": r.get("emailrecovery") or ""}
            if r.get("rateLimit"):
                return {"found": "rate_limit"}
            return {"found": False}
    except Exception as e:
        return {"error": str(e)[:60]}


async def _check_email_linkedin(email: str, client: httpx.AsyncClient) -> dict:
    """Проверяет зарегистрирован ли email на LinkedIn."""
    try:
        r = await client.post(
            "https://www.linkedin.com/checkpoint/lg/login-submit",
            data={"session_key": email, "session_password": "wrong_password_probe"},
            headers={**BROWSER_HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
        )
        text = r.text.lower()
        # LinkedIn говорит "пароль неверный" только если email существует
        if "wrong" in text or "incorrect" in text or "password" in text:
            return {"found": True}
        if "email" in text and ("not" in text or "doesn" in text):
            return {"found": False}
        return {"found": "maybe", "note": "LinkedIn не дал чёткого ответа"}
    except Exception as e:
        return {"error": str(e)[:60]}


# ─── FORMATTERS ────────────────────────────────────────────────────────────

def fmt_username(username: str, results: dict, elapsed: float) -> str:
    lines = [f"👤 *Username:* `{username}`\n"]
    icons = {
        "Twitter/X":   "🐦",
        "LinkedIn":    "💼",
        "Facebook":    "📘",
        "CryptoRank":  "📊",
        "OpenSea":     "🌊",
    }
    for platform, res in results.items():
        icon = icons.get(platform, "🔎")
        # Всегда строим прямую ссылку на профиль
        profile_url = PROFILE_URLS.get(platform, "").format(u=username)
        link = f"[→ профиль]({profile_url})" if profile_url else ""

        if res.get("found") == "manual":
            # Facebook: авто-проверка невозможна, даём ссылку
            lines.append(f"{icon} *{platform}:* 🔗 Проверь вручную — {link}")
        elif res.get("found") is True:
            lines.append(f"{icon} *{platform}:* ✅ Найден — {link}")
        elif res.get("found") == "maybe":
            note = res.get("note", "")
            lines.append(f"{icon} *{platform}:* ⚠️ Возможно — {link}\n     _{note}_")
        elif res.get("found") is False:
            lines.append(f"{icon} *{platform}:* ❌ Не найден — {link}")
        elif res.get("error"):
            lines.append(f"{icon} *{platform}:* 🔴 Ошибка — {link}\n     `{res['error']}`")
        else:
            lines.append(f"{icon} *{platform}:* ❓ — {link}")
    lines.append(f"\n📊 Проверено за *{elapsed:.1f}с*")
    return "\n".join(lines)


def fmt_email(email: str, results: dict, elapsed: float) -> str:
    lines = [f"📧 *Email:* `{email}`\n"]
    icons = {"Twitter/X": "🐦", "LinkedIn": "💼", "Facebook": "📘"}
    # CryptoRank и OpenSea не проверяют email — не показываем их
    for platform in ["Twitter/X", "LinkedIn", "Facebook"]:
        res = results.get(platform, {})
        icon = icons[platform]
        if res.get("found") is True:
            extra = f" _{res['extra']}_" if res.get("extra") else ""
            lines.append(f"{icon} *{platform}:* ✅ Зарегистрирован{extra}")
        elif res.get("found") == "rate_limit":
            lines.append(f"{icon} *{platform}:* ⚠️ Rate limit — попробуй позже")
        elif res.get("found") == "maybe":
            note = res.get("note", "")
            lines.append(f"{icon} *{platform}:* ⚠️ Возможно _{note}_")
        elif res.get("found") is False:
            lines.append(f"{icon} *{platform}:* ❌ Не зарегистрирован")
        elif res.get("error"):
            lines.append(f"{icon} *{platform}:* 🔴 Ошибка: `{res['error']}`")
        else:
            lines.append(f"{icon} *{platform}:* ❓ Нет данных")
    lines.append(
        "\n📊 Проверено за *{:.1f}с*\n"
        "ℹ️ _CryptoRank и OpenSea — Web3 платформы, email не используют_".format(elapsed)
    )
    return "\n".join(lines)


# ─── SCAN EXECUTORS ────────────────────────────────────────────────────────

async def do_email_scan(update: Update, email: str):
    msg = await update.effective_message.reply_text(
        f"🔍 Проверяю email `{email}` на 5 платформах...\n⏳ ~10–15 сек.",
        parse_mode=ParseMode.MARKDOWN
    )
    try:
        loop = asyncio.get_event_loop()
        results, elapsed = await loop.run_in_executor(None, _email_scan_sync, email)
        text = fmt_email(email, results, elapsed)
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN,
                            disable_web_page_preview=True, reply_markup=kb_back())
    except Exception as e:
        import traceback; traceback.print_exc()
        try:
            await msg.edit_text(f"❌ `{type(e).__name__}: {e}`", parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass


async def do_username_scan(update: Update, username: str):
    msg = await update.effective_message.reply_text(
        f"🔍 Ищу `{username}` на 5 платформах...\n⏳ ~10–15 сек.",
        parse_mode=ParseMode.MARKDOWN
    )
    try:
        results, elapsed = await scan_username_focused(username)
        text = fmt_username(username, results, elapsed)
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN,
                            disable_web_page_preview=True, reply_markup=kb_back())
    except Exception as e:
        import traceback; traceback.print_exc()
        try:
            await msg.edit_text(f"❌ `{type(e).__name__}: {e}`", parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass


# ─── HANDLERS ──────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.effective_message.reply_text(
        "👋 *OSINT Bot*\n\n"
        "Проверяю присутствие на:\n"
        "🐦 Twitter/X  💼 LinkedIn  📘 Facebook\n"
        "📊 CryptoRank  🌊 OpenSea\n\n"
        "Отправь email или username — или выбери режим:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_main()
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data

    if d == "back:main":
        context.user_data.clear()
        await q.edit_message_text(
            "👋 *OSINT Bot*\n\n"
            "Проверяю присутствие на:\n"
            "🐦 Twitter/X  💼 LinkedIn  📘 Facebook\n"
            "📊 CryptoRank  🌊 OpenSea\n\n"
            "Отправь email или username — или выбери режим:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_main()
        )
    elif d == "help":
        await q.edit_message_text(
            "📖 *Как пользоваться*\n\n"
            "• *Проверить Email* — проверяет через восстановление пароля зарегистрирован ли этот email на Twitter, Facebook, LinkedIn\n\n"
            "• *Найти Username* — ищет профиль по нику на Twitter/X, LinkedIn, Facebook, CryptoRank, OpenSea\n\n"
            "• *Оба сразу* — email + username\\-часть до @\n\n"
            "• *Список* — загрузи .txt файл, по одному email или нику на строке\n\n"
            "Или просто напиши email/ник — бот определит сам.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_back()
        )
    elif d == "mode:email":
        context.user_data["mode"] = "email"
        await q.edit_message_text("📧 Отправь email-адрес:", reply_markup=kb_cancel())
    elif d == "mode:user":
        context.user_data["mode"] = "user"
        await q.edit_message_text("👤 Отправь username:", reply_markup=kb_cancel())
    elif d == "mode:both":
        context.user_data["mode"] = "both"
        await q.edit_message_text(
            "🔀 Отправь email — проверю email и username (часть до @):",
            reply_markup=kb_cancel()
        )
    elif d == "mode:file":
        context.user_data["mode"] = "file"
        await q.edit_message_text(
            "📄 Загрузи .txt файл (один email или username на строку, макс. 20):",
            reply_markup=kb_cancel()
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text:
        return
    mode = context.user_data.get("mode")

    if mode == "email":
        if not is_email(text):
            await update.message.reply_text("❌ Не похоже на email. Попробуй ещё:", reply_markup=kb_cancel())
            return
        context.user_data.clear()
        await do_email_scan(update, text)

    elif mode == "user":
        context.user_data.clear()
        await do_username_scan(update, text)

    elif mode == "both":
        if not is_email(text):
            await update.message.reply_text("❌ Нужен email для этого режима:", reply_markup=kb_cancel())
            return
        context.user_data.clear()
        await do_email_scan(update, text)
        await do_username_scan(update, text.split("@")[0])

    else:
        # Автодетект
        if is_email(text):
            await do_email_scan(update, text)
        else:
            await do_username_scan(update, text)


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc or not doc.file_name.endswith(".txt"):
        await update.message.reply_text("❌ Только .txt файлы.", reply_markup=kb_main())
        return
    if doc.file_size > 50 * 1024:
        await update.message.reply_text("❌ Файл слишком большой (макс. 50 КБ).")
        return

    msg = await update.message.reply_text("📄 Читаю файл...")
    f = await doc.get_file()
    raw = await f.download_as_bytearray()
    lines = [l.strip() for l in raw.decode("utf-8", errors="ignore").splitlines()
             if l.strip() and not l.startswith("#")]

    if not lines:
        await msg.edit_text("❌ Файл пустой.")
        return
    if len(lines) > 20:
        lines = lines[:20]
        await msg.edit_text(f"⚠️ Беру первые 20 строк. Начинаю...")
    else:
        await msg.edit_text(f"📋 *{len(lines)}* целей. Начинаю...", parse_mode=ParseMode.MARKDOWN)

    context.user_data.clear()
    for i, target in enumerate(lines, 1):
        await update.message.reply_text(
            f"⏳ `[{i}/{len(lines)}]` → `{target}`",
            parse_mode=ParseMode.MARKDOWN
        )
        if is_email(target):
            await do_email_scan(update, target)
        else:
            await do_username_scan(update, target)

    await update.message.reply_text("✅ *Готово!*", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_main())


# ─── MAIN ──────────────────────────────────────────────────────────────────

def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        print("❌ BOT_TOKEN не задан!\n   export BOT_TOKEN='токен_от_BotFather'")
        sys.exit(1)

    print("🤖 OSINT Focused Bot запускается...")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help",  cmd_start))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.Document.MimeType("text/plain"), handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("✅ Бот запущен. Ctrl+C для остановки.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
