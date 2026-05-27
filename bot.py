#!/usr/bin/env python3
"""
OSINT Combo Bot — holehe (email) + user-scanner (username) + Telegram
Токен: переменная окружения BOT_TOKEN
"""

import asyncio
import os
import re
import sys
import time

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def is_email(target: str) -> bool:
    return bool(EMAIL_RE.match(target.strip()))


# ─── KEYBOARDS ─────────────────────────────────────────────────────────────

def kb_main():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📧 Email скан", callback_data="mode:email"),
            InlineKeyboardButton("👤 Username скан", callback_data="mode:user"),
        ],
        [
            InlineKeyboardButton("🔀 Оба сразу", callback_data="mode:both"),
            InlineKeyboardButton("📄 Файл со списком", callback_data="mode:file"),
        ],
        [
            InlineKeyboardButton("❓ Помощь", callback_data="help"),
        ],
    ])


def kb_back():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Назад", callback_data="back:main")],
    ])


def kb_cancel():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Отмена", callback_data="back:main")],
    ])


# ─── HOLEHE (email) ────────────────────────────────────────────────────────

async def scan_email_holehe(email: str, timeout: int = 10) -> tuple[list, float]:
    import importlib
    import pkgutil
    import httpx
    import trio
    from holehe.instruments import TrioProgress

    def import_submodules(package, recursive=True):
        if isinstance(package, str):
            package = importlib.import_module(package)
        results = {}
        for loader, name, is_pkg in pkgutil.walk_packages(package.__path__):
            full_name = package.__name__ + '.' + name
            try:
                results[full_name] = importlib.import_module(full_name)
                if recursive and is_pkg:
                    results.update(import_submodules(full_name))
            except Exception:
                pass
        return results

    def get_functions(modules):
        websites = []
        for module in modules:
            if len(module.split(".")) > 3:
                modu = modules[module]
                site = module.split(".")[-1]
                if hasattr(modu, site) and callable(getattr(modu, site)):
                    websites.append(modu.__dict__[site])
        return websites

    async def launch(module, email, client, out):
        try:
            await module(email, client, out)
        except Exception:
            pass

    modules = import_submodules("holehe.modules")
    websites = get_functions(modules)
    client = httpx.AsyncClient(timeout=timeout)
    out = []
    instrument = TrioProgress(len(websites))
    trio.lowlevel.add_instrument(instrument)
    start = time.time()
    async with trio.open_nursery() as nursery:
        for website in websites:
            nursery.start_soon(launch, website, email, client, out)
    trio.lowlevel.remove_instrument(instrument)
    elapsed = time.time() - start
    out = sorted(out, key=lambda i: i.get('name', ''))
    await client.aclose()
    return out, elapsed


# ─── USER-SCANNER (username) ───────────────────────────────────────────────

def scan_username(username: str) -> tuple[list, float]:
    from user_scanner.core.helpers import ScanConfig
    from user_scanner.core.orchestrator import run_user_full

    config = ScanConfig(allow_loud=False, only_found=False, no_nsfw=False, verbose=False)
    start = time.time()
    results = run_user_full(username, config)
    elapsed = time.time() - start
    return results, elapsed


# ─── FORMATTERS ────────────────────────────────────────────────────────────

def format_holehe(email: str, data: list, elapsed: float) -> str:
    found = [r for r in data if r.get("exists")]
    rate   = [r for r in data if r.get("rateLimit")]
    errors = [r for r in data if r.get("error")]

    lines = [f"📧 *Email:* `{email}`\n"]
    if found:
        lines.append(f"✅ *Найден на {len(found)} сайтах:*")
        for r in found:
            extra = ""
            if r.get("emailrecovery"):
                extra += f" — 📮 `{r['emailrecovery']}`"
            if r.get("phoneNumber"):
                extra += f" / 📞 `{r['phoneNumber']}`"
            lines.append(f"  • `{r['domain']}`{extra}")
    else:
        lines.append("❌ *Нигде не найден*")

    lines.append(f"\n📊 *{len(data)}* сайтов за *{elapsed:.1f}с*")
    if rate:
        lines.append(f"⚠️ Rate\\-limit: {len(rate)}")
    if errors:
        lines.append(f"🔴 Ошибки: {len(errors)}")
    return "\n".join(lines)


def format_username(username: str, results: list, elapsed: float) -> str:
    from user_scanner.core.result import Status

    found = [r for r in results if r.status == Status.TAKEN]
    lines = [f"👤 *Username:* `{username}`\n"]

    if found:
        lines.append(f"✅ *Найден на {len(found)} платформах:*")
        by_cat: dict[str, list] = {}
        for r in found:
            cat = getattr(r, 'category', None) or 'Other'
            by_cat.setdefault(cat, []).append(r)
        for cat, items in sorted(by_cat.items()):
            lines.append(f"\n*{cat}*")
            for r in items:
                url = getattr(r, 'url', '') or ''
                name = getattr(r, 'site_name', '?')
                lines.append(f"  • [{name}]({url})" if url else f"  • `{name}`")
    else:
        lines.append("❌ *Нигде не найден*")

    lines.append(f"\n📊 *{len(results)}* платформ за *{elapsed:.1f}с*")
    return "\n".join(lines)


# ─── SEND HELPERS ──────────────────────────────────────────────────────────

async def send_long(update: Update, text: str, reply_markup=None):
    """Отправляет длинный текст, разбивая на части если надо."""
    if len(text) <= 4000:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True, reply_markup=reply_markup
        )
    else:
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for i, chunk in enumerate(chunks):
            await update.effective_message.reply_text(
                chunk, parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
                reply_markup=reply_markup if i == len(chunks) - 1 else None
            )


# ─── SCAN EXECUTORS ────────────────────────────────────────────────────────

async def do_email_scan(update: Update, email: str):
    msg = await update.effective_message.reply_text(
        f"🔍 Сканирую email `{email}`...\n⏳ ~20–30 сек.",
        parse_mode=ParseMode.MARKDOWN
    )
    try:
        data, elapsed = await scan_email_holehe(email)
        result = format_holehe(email, data, elapsed)
        await msg.delete()
        await send_long(update, result, reply_markup=kb_back())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: `{e}`", parse_mode=ParseMode.MARKDOWN)


async def do_username_scan(update: Update, username: str):
    msg = await update.effective_message.reply_text(
        f"🔍 Ищу username `{username}`...\n⏳ ~10–20 сек.",
        parse_mode=ParseMode.MARKDOWN
    )
    try:
        loop = asyncio.get_event_loop()
        results, elapsed = await loop.run_in_executor(None, scan_username, username)
        result = format_username(username, results, elapsed)
        await msg.delete()
        await send_long(update, result, reply_markup=kb_back())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: `{e}`", parse_mode=ParseMode.MARKDOWN)


# ─── HANDLERS ──────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.effective_message.reply_text(
        "👋 *OSINT Combo Bot*\n\nВыбери режим или просто отправь email / username:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_main()
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "back:main":
        context.user_data.clear()
        await query.edit_message_text(
            "👋 *OSINT Combo Bot*\n\nВыбери режим или просто отправь email / username:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_main()
        )

    elif data == "help":
        text = (
            "📖 *Справка*\n\n"
            "• *Email скан* — проверяет email на 120\\+ сайтах\n"
            "• *Username скан* — ищет ник на 100\\+ платформах\n"
            "• *Оба сразу* — email \\+ username\\-часть до @\n"
            "• *Файл* — загрузи \\.txt со списком \\(по одному на строку\\)\n\n"
            "Или просто напиши email / username без кнопок — автодетект\\."
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb_back())

    elif data == "mode:email":
        context.user_data['mode'] = 'email'
        await query.edit_message_text(
            "📧 Отправь email-адрес:",
            reply_markup=kb_cancel()
        )

    elif data == "mode:user":
        context.user_data['mode'] = 'user'
        await query.edit_message_text(
            "👤 Отправь username:",
            reply_markup=kb_cancel()
        )

    elif data == "mode:both":
        context.user_data['mode'] = 'both'
        await query.edit_message_text(
            "🔀 Отправь email-адрес — отсканирую email и username\\-часть до @:",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=kb_cancel()
        )

    elif data == "mode:file":
        context.user_data['mode'] = 'file'
        await query.edit_message_text(
            "📄 Отправь .txt файл со списком email-ов или username-ов (по одному на строку):",
            reply_markup=kb_cancel()
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text:
        return

    mode = context.user_data.get('mode')

    if mode == 'email':
        if not is_email(text):
            await update.message.reply_text("❌ Это не похоже на email. Попробуй ещё:", reply_markup=kb_cancel())
            return
        context.user_data.clear()
        await do_email_scan(update, text)

    elif mode == 'user':
        context.user_data.clear()
        await do_username_scan(update, text)

    elif mode == 'both':
        if not is_email(text):
            await update.message.reply_text("❌ Нужен валидный email. Попробуй ещё:", reply_markup=kb_cancel())
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
    if not doc:
        return

    if not doc.file_name.endswith(".txt"):
        await update.message.reply_text(
            "❌ Поддерживаются только `.txt` файлы\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=kb_main()
        )
        return

    if doc.file_size > 100 * 1024:
        await update.message.reply_text("❌ Файл слишком большой (макс. 100 КБ).", reply_markup=kb_main())
        return

    msg = await update.message.reply_text("📄 Читаю файл...")

    tg_file = await doc.get_file()
    content_bytes = await tg_file.download_as_bytearray()
    content = content_bytes.decode("utf-8", errors="ignore")

    lines = [l.strip() for l in content.splitlines() if l.strip() and not l.startswith("#")]
    if not lines:
        await msg.edit_text("❌ Файл пустой или все строки закомментированы.")
        return

    if len(lines) > 50:
        await msg.edit_text(f"⚠️ В файле {len(lines)} строк, обработаю первые 50.")
        lines = lines[:50]
    else:
        await msg.edit_text(f"📋 Найдено *{len(lines)}* целей. Начинаю...", parse_mode=ParseMode.MARKDOWN)

    context.user_data.clear()

    for i, target in enumerate(lines, 1):
        await update.message.reply_text(
            f"⏳ `[{i}/{len(lines)}]` Сканирую `{target}`...",
            parse_mode=ParseMode.MARKDOWN
        )
        if is_email(target):
            await do_email_scan(update, target)
        else:
            await do_username_scan(update, target)

    await update.message.reply_text(
        f"✅ *Готово!* Обработано {len(lines)} целей.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_main()
    )


# ─── MAIN ──────────────────────────────────────────────────────────────────

def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        print("❌ Переменная окружения BOT_TOKEN не задана!")
        print("   export BOT_TOKEN='токен_от_BotFather'")
        sys.exit(1)

    print("🤖 Запуск OSINT Combo Bot...")
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
