#!/usr/bin/env python3
"""
Telegram-бот для OSINT Combo (holehe + user-scanner)
Токен задаётся через переменную окружения BOT_TOKEN
"""

import asyncio
import io
import os
import re
import sys
import time

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')

sys.path.insert(0, os.path.dirname(__file__))


def is_email(target: str) -> bool:
    return bool(EMAIL_RE.match(target.strip()))


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
        name = str(module).split('<function ')[1].split(' ')[0] if '<function ' in str(module) else module.__name__
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

def scan_username(username: str, only_found: bool = True) -> tuple[list, float]:
    from user_scanner.core.helpers import ScanConfig, load_categories, load_modules, get_site_name
    from user_scanner.core.orchestrator import run_user_full
    from user_scanner.core.result import Status

    config = ScanConfig(allow_loud=False, only_found=False, no_nsfw=False, verbose=False)

    start = time.time()
    results = run_user_full(username, config)
    elapsed = time.time() - start
    return results, elapsed


# ─── FORMATTERS ────────────────────────────────────────────────────────────

def format_holehe_results(email: str, data: list, elapsed: float) -> str:
    found = [r for r in data if r.get("exists")]
    errors = [r for r in data if r.get("error")]
    rate = [r for r in data if r.get("rateLimit")]

    lines = [f"📧 *Email:* `{email}`\n"]

    if found:
        lines.append(f"✅ *Найден на {len(found)} сайтах:*")
        for r in found:
            extra = ""
            if r.get("emailrecovery"):
                extra += f" — 📮 {r['emailrecovery']}"
            if r.get("phoneNumber"):
                extra += f" / 📞 {r['phoneNumber']}"
            lines.append(f"  • `{r['domain']}`{extra}")
    else:
        lines.append("❌ *Нигде не найден*")

    lines.append(f"\n📊 Проверено: *{len(data)}* сайтов за *{elapsed:.1f}с*")
    if rate:
        lines.append(f"⚠️ Rate limit: {len(rate)} сайтов")
    if errors:
        lines.append(f"🔴 Ошибки: {len(errors)} сайтов")

    return "\n".join(lines)


def format_username_results(username: str, results: list, elapsed: float) -> str:
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
                if url:
                    lines.append(f"  • [{r.site_name}]({url})")
                else:
                    lines.append(f"  • `{r.site_name}`")
    else:
        lines.append("❌ *Нигде не найден*")

    lines.append(f"\n📊 Проверено: *{len(results)}* платформ за *{elapsed:.1f}с*")
    return "\n".join(lines)


# ─── HANDLERS ──────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *OSINT Combo Bot*\n\n"
        "Просто отправь мне:\n"
        "• 📧 *Email* — проверю его на 120+ сайтах\n"
        "• 👤 *Username* — поищу на 100+ платформах\n\n"
        "Команды:\n"
        "/email `адрес@mail.com` — сканировать email\n"
        "/user `johndoe` — сканировать username\n"
        "/both `адрес@mail.com` — email + username по части до @\n"
        "/help — помощь"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def cmd_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: `/email адрес@mail.com`", parse_mode=ParseMode.MARKDOWN)
        return
    email = context.args[0].strip()
    if not is_email(email):
        await update.message.reply_text("❌ Это не похоже на email-адрес.")
        return
    await _do_email_scan(update, email)


async def cmd_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: `/user johndoe`", parse_mode=ParseMode.MARKDOWN)
        return
    username = context.args[0].strip()
    await _do_username_scan(update, username)


async def cmd_both(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: `/both адрес@mail.com`", parse_mode=ParseMode.MARKDOWN)
        return
    email = context.args[0].strip()
    if not is_email(email):
        await update.message.reply_text("❌ Нужен валидный email для команды /both.")
        return
    await _do_email_scan(update, email)
    username = email.split("@")[0]
    await update.message.reply_text(f"🔄 Также ищу username `{username}`...", parse_mode=ParseMode.MARKDOWN)
    await _do_username_scan(update, username)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text:
        return

    if is_email(text):
        await _do_email_scan(update, text)
    else:
        await _do_username_scan(update, text)


async def _do_email_scan(update: Update, email: str):
    msg = await update.message.reply_text(f"🔍 Сканирую email `{email}`...\n⏳ Подождите, это займёт ~20-30 сек.", parse_mode=ParseMode.MARKDOWN)
    try:
        data, elapsed = await scan_email_holehe(email)
        result_text = format_holehe_results(email, data, elapsed)

        if len(result_text) > 4000:
            chunks = [result_text[i:i+4000] for i in range(0, len(result_text), 4000)]
            await msg.delete()
            for chunk in chunks:
                await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        else:
            await msg.edit_text(result_text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка при сканировании: `{e}`", parse_mode=ParseMode.MARKDOWN)


async def _do_username_scan(update: Update, username: str):
    msg = await update.message.reply_text(f"🔍 Ищу username `{username}`...\n⏳ Подождите ~10-20 сек.", parse_mode=ParseMode.MARKDOWN)
    try:
        loop = asyncio.get_event_loop()
        results, elapsed = await loop.run_in_executor(None, scan_username, username)
        result_text = format_username_results(username, results, elapsed)

        if len(result_text) > 4000:
            chunks = [result_text[i:i+4000] for i in range(0, len(result_text), 4000)]
            await msg.delete()
            for chunk in chunks:
                await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        else:
            await msg.edit_text(result_text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка при сканировании: `{e}`", parse_mode=ParseMode.MARKDOWN)


# ─── MAIN ──────────────────────────────────────────────────────────────────

def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        print("❌ Переменная окружения BOT_TOKEN не задана!")
        print("   export BOT_TOKEN='ваш_токен_от_BotFather'")
        sys.exit(1)

    print("🤖 Запуск OSINT Combo Bot...")
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("email", cmd_email))
    app.add_handler(CommandHandler("user", cmd_user))
    app.add_handler(CommandHandler("both", cmd_both))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Бот запущен. Нажми Ctrl+C для остановки.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
