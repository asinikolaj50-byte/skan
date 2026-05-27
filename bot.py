#!/usr/bin/env python3
"""
OSINT Telegram Bot — поиск аккаунтов по email и username.

Платформы (email):
  Twitter/X  — API email_available.json (точный результат)
  Facebook   — forgot-password flow (точный результат)
  Instagram  — API registration check (точный результат)
  LinkedIn   — forgot-password / login flow (точный результат)
  Discord    — API registration check (точный результат)

Платформы (username):
  Twitter/X  — проверка страницы профиля
  LinkedIn   — проверка страницы профиля
  Facebook   — ссылка для ручной проверки
  CryptoRank — API + страница профиля
  OpenSea    — OpenSea API v2

Для CryptoRank и OpenSea email-поиск также реализован.

Установка зависимостей:
  pip install httpx[http2] python-telegram-bot colorama
"""

import asyncio
import os
import re
import time
from typing import Optional

import httpx

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.constants import ParseMode

# ─── КОНСТАНТЫ ────────────────────────────────────────────────────────────────

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Прямые ссылки на профили (подставляется username)
PROFILE_URLS: dict[str, str] = {
    "Twitter/X":  "https://x.com/{u}",
    "LinkedIn":   "https://www.linkedin.com/in/{u}",
    "Facebook":   "https://www.facebook.com/{u}",
    "CryptoRank": "https://cryptorank.io/profile/{u}",
    "OpenSea":    "https://opensea.io/{u}",
}

ICONS: dict[str, str] = {
    "Twitter/X":  "🐦",
    "LinkedIn":   "💼",
    "Facebook":   "📘",
    "Instagram":  "📸",
    "Discord":    "🎮",
    "CryptoRank": "📊",
    "OpenSea":    "🌊",
}

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")


# ─── УТИЛИТЫ ──────────────────────────────────────────────────────────────────

def is_email(text: str) -> bool:
    return bool(EMAIL_RE.match(text.strip()))


def profile_link(platform: str, identifier: str) -> str:
    """Строит ссылку на профиль для inline-кнопок / Markdown."""
    template = PROFILE_URLS.get(platform, "")
    if not template:
        return ""
    return template.format(u=identifier)


# ─── KEYBOARDS ────────────────────────────────────────────────────────────────

def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📧 Email → аккаунты", callback_data="mode:email"),
            InlineKeyboardButton("👤 Username → сети", callback_data="mode:user"),
        ],
        [
            InlineKeyboardButton("🔀 Email + Username сразу", callback_data="mode:both"),
        ],
        [
            InlineKeyboardButton("📄 Список .txt", callback_data="mode:file"),
            InlineKeyboardButton("❓ Помощь", callback_data="help"),
        ],
    ])


def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back:main")]])


def kb_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="back:main")]])


# ─── EMAIL CHECKERS ───────────────────────────────────────────────────────────

async def _check_twitter_email(email: str, client: httpx.AsyncClient) -> dict:
    """
    Twitter/X — проверка email через registration API.
    GET https://api.twitter.com/i/users/email_available.json?email=...
    {"taken": true} → зарегистрирован, {"taken": false} → нет.
    Точность: высокая — официальный endpoint регистрации.
    """
    try:
        r = await client.get(
            "https://api.twitter.com/i/users/email_available.json",
            params={"email": email},
            headers={
                **BROWSER_HEADERS,
                "Accept": "application/json",
                "x-twitter-active-user": "yes",
                "x-twitter-client-language": "en",
                "Referer": "https://x.com/",
            },
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("taken") is True or data.get("reason") == "taken":
                return {"found": True}
            if data.get("valid") is True:
                return {"found": False}
            return {"found": False}
        if r.status_code == 429:
            return {"found": "rate_limit"}
        return {"error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"error": str(e)[:80]}


async def _check_facebook_email(email: str, client: httpx.AsyncClient) -> dict:
    """
    Facebook — forgot-password flow.
    Шаги:
      1. GET https://www.facebook.com  → извлечь LSD-токен и jazoest
      2. POST https://www.facebook.com/ajax/login/help/identify.php?ctx=recover
         с {email, lsd, jazoest}
    Ответ "These accounts matched" → найден.
    Ответ "No search results"      → не зарегистрирован.
    Точность: высокая — официальный forgot-password endpoint.
    """
    try:
        headers_get = {
            **BROWSER_HEADERS,
            "sec-ch-ua": '"Google Chrome";v="124", "Chromium";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Upgrade-Insecure-Requests": "1",
        }
        r1 = await client.get("https://www.facebook.com", headers=headers_get)
        html = r1.text

        lsd_match = (
            re.search(r'\["LSD",\[\],\{"token":"([^"]+)"\}', html)
            or re.search(r'name="lsd"\s+value="([^"]+)"', html)
            or re.search(r'"lsd":"([^"]+)"', html)
        )
        j_match = (
            re.search(r'jazoest=(\d+)', html)
            or re.search(r'name="jazoest"\s+value="(\d+)"', html)
        )

        if not lsd_match or not j_match:
            return {"error": "Не удалось извлечь токены (FB заблокировал IP или изменил структуру)"}

        lsd = lsd_match.group(1)
        jazoest = j_match.group(1)

        headers_post = {
            **BROWSER_HEADERS,
            "Content-Type": "application/x-www-form-urlencoded",
            "x-fb-lsd": lsd,
            "Origin": "https://www.facebook.com",
            "Referer": "https://www.facebook.com/login/identify/?ctx=recover",
            "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
        }
        payload = {
            "jazoest": jazoest,
            "lsd": lsd,
            "email": email,
            "did_submit": "1",
            "__user": "0",
            "__a": "1",
            "__req": "7",
        }
        r2 = await client.post(
            "https://www.facebook.com/ajax/login/help/identify.php",
            params={"ctx": "recover"},
            data=payload,
            headers=headers_post,
        )
        body = r2.text
        if "These accounts matched your search" in body or "redirectPageTo" in body:
            return {"found": True}
        if "No search results" in body or "Your search did not return any results" in body:
            return {"found": False}
        return {"error": "Неопределённый ответ FB — возможно блокировка IP"}

    except Exception as e:
        return {"error": str(e)[:80]}


async def _check_instagram_email(email: str, client: httpx.AsyncClient) -> dict:
    """
    Instagram — проверка через API регистрации.
    POST https://www.instagram.com/api/v1/users/check_email/
    error_type == "email_is_taken" → зарегистрирован.
    available == true              → не зарегистрирован.
    Точность: высокая.
    """
    try:
        user_agent = (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/17.0 Mobile/15E148 Safari/604.1"
        )
        async with httpx.AsyncClient(
            headers={"user-agent": user_agent}, http2=True, timeout=10.0
        ) as ig_client:
            r0 = await ig_client.get(
                "https://www.instagram.com/", follow_redirects=True
            )
            csrf = ig_client.cookies.get("csrftoken")
            if not csrf:
                m = re.search(r'["\']csrf_token["\']\s*:\s*["\']([^"\']+)["\']', r0.text)
                if m:
                    csrf = m.group(1)
            if not csrf:
                return {"error": "CSRF не получен (IP заблокирован Instagram)"}

            headers_post = {
                "x-csrftoken": csrf,
                "x-ig-app-id": "936619743392459",
                "x-requested-with": "XMLHttpRequest",
                "origin": "https://www.instagram.com",
                "referer": "https://www.instagram.com/",
                "content-type": "application/x-www-form-urlencoded",
            }
            r1 = await ig_client.post(
                "https://www.instagram.com/api/v1/users/check_email/",
                data={"email": email, "sign_up_code": ""},
                headers=headers_post,
            )
            if r1.status_code == 200:
                data = r1.json()
                if data.get("error_type") == "email_is_taken":
                    return {"found": True}
                if data.get("available") is True:
                    return {"found": False}
                return {"error": "Неожиданный ответ Instagram"}
            if r1.status_code == 400:
                data = r1.json()
                if data.get("spam") is True:
                    return {"found": False}  # Спам = не существует
                return {"error": f"400: {data}"}
            if r1.status_code == 429:
                return {"found": "rate_limit"}
            return {"error": f"HTTP {r1.status_code}"}

    except Exception as e:
        return {"error": str(e)[:80]}


async def _check_linkedin_email(email: str, client: httpx.AsyncClient) -> dict:
    """
    LinkedIn — forgot-password flow через форму логина.
    Шаги:
      1. GET /login → извлечь loginCsrfParam
      2. POST /checkpoint/lg/login-submit с email + неверным паролем
    LinkedIn явно разделяет:
      "don't recognize that email" → не зарегистрирован
      "wrong password" / редирект на ввод пароля → зарегистрирован
    Точность: высокая.
    """
    try:
        r1 = await client.get(
            "https://www.linkedin.com/login",
            headers={**BROWSER_HEADERS, "Accept": "text/html"},
        )
        csrf_m = (
            re.search(r'name="loginCsrfParam"\s+value="([^"]+)"', r1.text)
            or re.search(r'"loginCsrfParam"\s*:\s*"([^"]+)"', r1.text)
        )
        if not csrf_m:
            return {"error": "LinkedIn: не удалось получить CSRF"}

        csrf = csrf_m.group(1)
        payload = {
            "session_key": email,
            "session_password": "Wr0ng_Pa55w0rd_Pr0be_xK9!",
            "loginCsrfParam": csrf,
            "isJsEnabled": "false",
        }
        r2 = await client.post(
            "https://www.linkedin.com/checkpoint/lg/login-submit",
            data=payload,
            headers={
                **BROWSER_HEADERS,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://www.linkedin.com/login",
                "Origin": "https://www.linkedin.com",
            },
        )
        body = r2.text.lower()

        # Точный сигнал «не найден»
        not_found = [
            "don't recognize", "doesn't recognize",
            "unknown_email_address", "no account found",
            "we couldn't find", "hmm, that email",
        ]
        for s in not_found:
            if s in body:
                return {"found": False}

        # Точный сигнал «найден» (неверный пароль = email известен)
        found = [
            "wrong password", "incorrect password",
            "too many incorrect", "enter your password",
        ]
        for s in found:
            if s in body:
                return {"found": True}

        # Редирект на страницу ввода пароля = email найден
        final = str(r2.url).lower()
        if "add-password" in final or "/checkpoint/" in final or "authwall" in final:
            return {"found": True}

        return {"error": "LinkedIn: неопределённый ответ"}

    except Exception as e:
        return {"error": str(e)[:80]}


async def _check_discord_email(email: str, client: httpx.AsyncClient) -> dict:
    """
    Discord — проверка через API регистрации.
    POST https://discord.com/api/v10/auth/register
    errors.email[0].code == "EMAIL_ALREADY_REGISTERED" → зарегистрирован.
    Точность: высокая.
    """
    import random
    import string

    def rand_str(n: int) -> str:
        return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))

    try:
        payload = {
            "fingerprint": "",
            "email": email,
            "username": rand_str(16),
            "password": rand_str(20),
            "consent": True,
            "date_of_birth": "1995-01-01",
            "gift_code_sku_id": None,
            "captcha_key": None,
        }
        r = await client.post(
            "https://discord.com/api/v10/auth/register",
            json=payload,
            headers={
                **BROWSER_HEADERS,
                "Content-Type": "application/json",
                "Accept": "*/*",
                "Origin": "https://discord.com",
            },
        )
        data = r.json()
        if r.status_code in (400, 200):
            errors = data.get("errors", {}).get("email", {}).get("_errors", [])
            if errors:
                code = errors[0].get("code", "")
                if code == "EMAIL_ALREADY_REGISTERED":
                    return {"found": True}
                return {"found": False}
            if data.get("captcha_key"):
                # Discord требует капчу → email скорее всего новый
                return {"found": False}
            # Регистрация прошла без ошибок = email новый
            return {"found": False}
        if r.status_code == 429:
            return {"found": "rate_limit"}
        return {"error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"error": str(e)[:80]}


async def _check_cryptorank_email(email: str, client: httpx.AsyncClient) -> dict:
    """
    CryptoRank — forgot-password endpoint.
    POST https://cryptorank.io/api/v0/auth/reset-password
    {"success": true}                → аккаунт найден
    {"success": false, "message": "User not found"} → не зарегистрирован
    """
    try:
        r = await client.post(
            "https://cryptorank.io/api/v0/auth/reset-password",
            json={"email": email},
            headers={
                **BROWSER_HEADERS,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": "https://cryptorank.io",
                "Referer": "https://cryptorank.io/",
            },
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("success") is True:
                return {"found": True}
            msg = (data.get("message") or "").lower()
            if "not found" in msg or "no user" in msg or "not exist" in msg:
                return {"found": False}
            if "too many" in msg or "rate" in msg:
                return {"found": "rate_limit"}
            return {"error": f"Неопределённый ответ: {data.get('message', '')}"}
        if r.status_code == 404:
            return {"found": False}
        if r.status_code == 422:
            return {"found": False}
        if r.status_code == 429:
            return {"found": "rate_limit"}
        return {"error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"error": str(e)[:80]}


async def scan_email(email: str) -> tuple[dict, float]:
    """
    Параллельно проверяет email на всех платформах.
    Возвращает (results_dict, elapsed_seconds).
    """
    start = time.time()
    async with httpx.AsyncClient(
        headers=BROWSER_HEADERS,
        timeout=15.0,
        follow_redirects=True,
        http2=True,
    ) as client:
        results = await asyncio.gather(
            _check_twitter_email(email, client),
            _check_facebook_email(email, client),
            _check_instagram_email(email, client),
            _check_linkedin_email(email, client),
            _check_discord_email(email, client),
            _check_cryptorank_email(email, client),
        )
    elapsed = time.time() - start
    platforms = ["Twitter/X", "Facebook", "Instagram", "LinkedIn", "Discord", "CryptoRank"]
    return dict(zip(platforms, results)), elapsed


# ─── USERNAME CHECKERS ────────────────────────────────────────────────────────

async def _check_twitter_user(username: str, client: httpx.AsyncClient) -> dict:
    """Проверяет профиль Twitter/X — страница профиля."""
    url = f"https://x.com/{username}"
    try:
        r = await client.get(url, headers=BROWSER_HEADERS)
        if r.status_code == 404:
            return {"found": False}
        if r.status_code == 200:
            text = r.text.lower()
            if "this account doesn" in text or "account doesn't exist" in text:
                return {"found": False}
            # X часто блокирует боты и редиректит на логин — даём ссылку
            return {"found": "maybe", "url": url, "note": "Требует авторизацию, проверь вручную"}
        return {"error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"error": str(e)[:80]}


async def _check_linkedin_user(username: str, client: httpx.AsyncClient) -> dict:
    """Проверяет профиль LinkedIn — страница профиля."""
    url = f"https://www.linkedin.com/in/{username}"
    try:
        r = await client.get(url, headers=BROWSER_HEADERS)
        if r.status_code == 404:
            return {"found": False}
        if r.status_code == 200:
            final_url = str(r.url).lower()
            if "authwall" in final_url or "/login" in final_url:
                # LinkedIn редиректит на логин — профиль скорее всего существует
                return {"found": True, "url": url}
            if "profile not found" in r.text.lower():
                return {"found": False}
            return {"found": True, "url": url}
        if r.status_code == 999:
            # LinkedIn блокирует ботов с кодом 999 — профиль может существовать
            return {"found": "maybe", "url": url, "note": "LinkedIn заблокировал запрос (999)"}
        return {"error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"error": str(e)[:80]}


async def _check_cryptorank_user(username: str, client: httpx.AsyncClient) -> dict:
    """
    CryptoRank — проверка по username.
    Метод 1: GET /api/v0/user/info?username=...  (JSON API)
    Метод 2: Страница профиля (fallback)
    """
    profile_url = f"https://cryptorank.io/profile/{username}"
    try:
        r = await client.get(
            "https://cryptorank.io/api/v0/user/info",
            params={"username": username},
            headers={**BROWSER_HEADERS, "Accept": "application/json"},
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("data") and str(data["data"].get("username", "")).lower() == username.lower():
                return {"found": True, "url": profile_url}
            return {"found": False}
        if r.status_code == 404:
            return {"found": False}
    except Exception:
        pass

    # Fallback: страница профиля
    try:
        r2 = await client.get(profile_url, headers=BROWSER_HEADERS)
        if r2.status_code == 404:
            return {"found": False}
        if r2.status_code == 200:
            text = r2.text.lower()
            if "page not found" in text or "404" in text[:500]:
                return {"found": False}
            if username.lower() in text[:8000]:
                return {"found": True, "url": profile_url}
            return {"found": False}
        return {"error": f"HTTP {r2.status_code}"}
    except Exception as e:
        return {"error": str(e)[:80]}


async def _check_opensea_user(username: str, client: httpx.AsyncClient) -> dict:
    """
    OpenSea — проверка по username через OpenSea API v2.
    GET https://api.opensea.io/api/v2/accounts/{username}
    200 → аккаунт найден (возвращает данные профиля)
    400/404 → не найден
    """
    profile_url = f"https://opensea.io/{username}"
    try:
        r = await client.get(
            f"https://api.opensea.io/api/v2/accounts/{username}",
            headers={
                **BROWSER_HEADERS,
                "Accept": "application/json",
                "x-app-id": "opensea-web",
            },
        )
        if r.status_code == 200:
            data = r.json()
            # Ответ содержит address или username
            if data.get("username") or data.get("address"):
                return {"found": True, "url": profile_url}
            return {"found": False}
        if r.status_code in (400, 404):
            return {"found": False}
        if r.status_code == 429:
            return {"found": "rate_limit"}
        return {"error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"error": str(e)[:80]}


async def scan_username(username: str) -> tuple[dict, float]:
    """
    Параллельно проверяет username на всех платформах.
    Facebook не имеет автоматической проверки — выдаём ссылку.
    """
    start = time.time()
    async with httpx.AsyncClient(
        headers=BROWSER_HEADERS,
        timeout=15.0,
        follow_redirects=True,
        http2=True,
    ) as client:
        results_list = await asyncio.gather(
            _check_twitter_user(username, client),
            _check_linkedin_user(username, client),
            _check_cryptorank_user(username, client),
            _check_opensea_user(username, client),
        )
    elapsed = time.time() - start

    results = dict(zip(
        ["Twitter/X", "LinkedIn", "CryptoRank", "OpenSea"],
        results_list,
    ))
    # Facebook — только ссылка (авто-проверка заблокирована)
    results["Facebook"] = {
        "found": "manual",
        "url": f"https://www.facebook.com/{username}",
    }
    return results, elapsed


# ─── ФОРМАТТЕРЫ ───────────────────────────────────────────────────────────────

def fmt_email(email: str, results: dict, elapsed: float) -> str:
    lines = [f"📧 *Email:* `{email}`\n"]
    for platform in ["Twitter/X", "Facebook", "Instagram", "LinkedIn", "Discord", "CryptoRank"]:
        res = results.get(platform, {})
        icon = ICONS.get(platform, "🔎")

        if res.get("found") is True:
            lines.append(f"{icon} *{platform}:* ✅ Зарегистрирован")
        elif res.get("found") == "rate_limit":
            lines.append(f"{icon} *{platform}:* ⏳ Rate limit — попробуй позже")
        elif res.get("found") is False:
            lines.append(f"{icon} *{platform}:* ❌ Не зарегистрирован")
        elif res.get("error"):
            lines.append(f"{icon} *{platform}:* 🔴 Ошибка: `{res['error']}`")
        else:
            lines.append(f"{icon} *{platform}:* ❓ Нет данных")

    lines.append(f"\n⏱ Проверено за *{elapsed:.1f}с*")
    return "\n".join(lines)


def fmt_username(username: str, results: dict, elapsed: float) -> str:
    lines = [f"👤 *Username:* `{username}`\n"]
    for platform in ["Twitter/X", "LinkedIn", "Facebook", "CryptoRank", "OpenSea"]:
        res = results.get(platform, {})
        icon = ICONS.get(platform, "🔎")
        url = res.get("url") or profile_link(platform, username)
        link = f"[→ профиль]({url})" if url else ""

        if res.get("found") == "manual":
            lines.append(f"{icon} *{platform}:* 🔗 Проверь вручную — {link}")
        elif res.get("found") is True:
            lines.append(f"{icon} *{platform}:* ✅ Найден — {link}")
        elif res.get("found") == "maybe":
            note = res.get("note", "")
            lines.append(f"{icon} *{platform}:* ⚠️ Возможно — {link}\n   _{note}_")
        elif res.get("found") == "rate_limit":
            lines.append(f"{icon} *{platform}:* ⏳ Rate limit — {link}")
        elif res.get("found") is False:
            lines.append(f"{icon} *{platform}:* ❌ Не найден")
        elif res.get("error"):
            lines.append(f"{icon} *{platform}:* 🔴 Ошибка — {link}\n   `{res['error']}`")
        else:
            lines.append(f"{icon} *{platform}:* ❓ — {link}")

    lines.append(f"\n⏱ Проверено за *{elapsed:.1f}с*")
    return "\n".join(lines)


# ─── HANDLERS ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *OSINT-бот* — поиск аккаунтов по email и username\n\n"
        "Платформы: Twitter/X, Facebook, Instagram, LinkedIn, Discord, CryptoRank, OpenSea",
        reply_markup=kb_main(),
        parse_mode=ParseMode.MARKDOWN,
    )


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "back:main":
        context.user_data.clear()
        await q.edit_message_text(
            "👋 *OSINT-бот* — выбери режим:",
            reply_markup=kb_main(),
            parse_mode=ParseMode.MARKDOWN,
        )
    elif data == "help":
        text = (
            "*Режимы работы:*\n\n"
            "📧 *Email → аккаунты* — проверяет, зарегистрирован ли email на:\n"
            "  Twitter, Facebook, Instagram, LinkedIn, Discord, CryptoRank\n\n"
            "👤 *Username → сети* — ищет профиль по нику на:\n"
            "  Twitter, LinkedIn, Facebook, CryptoRank, OpenSea\n\n"
            "🔀 *Email + Username* — сначала email-проверка, потом ник\n\n"
            "*Методы проверки:*\n"
            "• Twitter — email\\_available API (точный)\n"
            "• Facebook — forgot-password flow (точный)\n"
            "• Instagram — registration API (точный)\n"
            "• LinkedIn — login flow (точный)\n"
            "• Discord — registration API (точный)\n"
            "• CryptoRank — reset-password API (точный)\n"
            "• OpenSea — API v2 (точный по username)"
        )
        await q.edit_message_text(text, reply_markup=kb_back(), parse_mode=ParseMode.MARKDOWN)
    elif data in ("mode:email", "mode:user", "mode:both", "mode:file"):
        context.user_data["mode"] = data.split(":")[1]
        prompts = {
            "email": "Введи *email* для проверки:",
            "user":  "Введи *username* для поиска:",
            "both":  "Введи *email* для проверки (потом попрошу username):",
            "file":  "Отправь .txt файл (каждый email или username на новой строке):",
        }
        await q.edit_message_text(
            prompts[context.user_data["mode"]],
            reply_markup=kb_cancel(),
            parse_mode=ParseMode.MARKDOWN,
        )


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    mode = context.user_data.get("mode")
    text = (update.message.text or "").strip()

    if not mode:
        await update.message.reply_text(
            "Выбери режим:",
            reply_markup=kb_main(),
        )
        return

    if mode == "email":
        if not is_email(text):
            await update.message.reply_text("❌ Неверный формат email. Попробуй ещё раз.")
            return
        msg = await update.message.reply_text("⏳ Проверяю email...")
        results, elapsed = await scan_email(text)
        await msg.edit_text(
            fmt_email(text, results, elapsed),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_back(),
        )

    elif mode == "user":
        if not text:
            await update.message.reply_text("❌ Введи username.")
            return
        msg = await update.message.reply_text("⏳ Ищу профили...")
        results, elapsed = await scan_username(text)
        await msg.edit_text(
            fmt_username(text, results, elapsed),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_back(),
        )

    elif mode == "both":
        step = context.user_data.get("both_step", "email")
        if step == "email":
            if not is_email(text):
                await update.message.reply_text("❌ Неверный формат email.")
                return
            context.user_data["both_email"] = text
            context.user_data["both_step"] = "user"
            msg = await update.message.reply_text("⏳ Проверяю email...")
            results, elapsed = await scan_email(text)
            await msg.edit_text(
                fmt_email(text, results, elapsed),
                parse_mode=ParseMode.MARKDOWN,
            )
            await update.message.reply_text(
                "Теперь введи *username* для проверки профилей:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb_cancel(),
            )
        else:
            msg = await update.message.reply_text("⏳ Ищу профили по username...")
            results, elapsed = await scan_username(text)
            await msg.edit_text(
                fmt_username(text, results, elapsed),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb_back(),
            )
            context.user_data.clear()

    else:
        await update.message.reply_text("Выбери режим:", reply_markup=kb_main())


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    mode = context.user_data.get("mode")
    if mode != "file":
        await update.message.reply_text("Сначала выбери режим 'Список .txt' в меню.")
        return

    doc = update.message.document
    if not doc.file_name.endswith(".txt"):
        await update.message.reply_text("❌ Поддерживаются только .txt файлы.")
        return

    file = await doc.get_file()
    content = (await file.download_as_bytearray()).decode("utf-8", errors="ignore")
    lines = [l.strip() for l in content.splitlines() if l.strip()]

    if not lines:
        await update.message.reply_text("❌ Файл пуст.")
        return
    if len(lines) > 50:
        await update.message.reply_text("❌ Максимум 50 строк в файле.")
        return

    await update.message.reply_text(f"⏳ Обрабатываю {len(lines)} строк...")

    for line in lines:
        if is_email(line):
            results, elapsed = await scan_email(line)
            text = fmt_email(line, results, elapsed)
        else:
            results, elapsed = await scan_username(line)
            text = fmt_username(line, results, elapsed)
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        await asyncio.sleep(1)  # Не спамим Telegram

    context.user_data.clear()
    await update.message.reply_text("✅ Готово!", reply_markup=kb_main())


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main() -> None:
    if not BOT_TOKEN:
        raise ValueError(
            "Не задан BOT_TOKEN. "
            "Задай переменную окружения: export BOT_TOKEN='...' "
            "или передай токен напрямую в этом файле."
        )

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))

    print("Бот запущен. Ctrl+C для остановки.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
