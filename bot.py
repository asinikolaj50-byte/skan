#!/usr/bin/env python3
"""
OSINT Telegram Bot — поиск аккаунтов по email и username.

Платформы (email):
  Twitter/X  — email_available.json API
  Facebook   — registration check + forgot-password (двухшаговый)
  Instagram  — registration check API
  LinkedIn   — forgot-password + login form fallback
  Discord    — registration API v10
  CryptoRank — reset-password API с XSRF-TOKEN

Платформы (username):
  Twitter/X  — страница профиля
  LinkedIn   — страница профиля
  Facebook   — ссылка для ручной проверки
  CryptoRank — API + страница профиля
  OpenSea    — страница профиля (без API-ключа)

Зависимости:
  pip install httpx[http2] python-telegram-bot

Переменные окружения:
  BOT_TOKEN        — токен Telegram-бота (обязательно)
  PROXY_URL        — прокси для всех исходящих запросов, формат:
                       http://user:pass@host:port
                       socks5://user:pass@host:port
                     Если не задан — запросы идут напрямую (возможны блокировки).
  CAPTCHA_API_KEY  — ключ 2captcha.com для автоматического решения капч
                     (используется при reCAPTCHA-блокировках Facebook/LinkedIn).
  REQUEST_DELAY    — задержка (секунд, float) между запросами в файловом режиме.
                     По умолчанию 2.0.
"""

import asyncio
import os
import random
import re
import string
import time

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

BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")
PROXY_URL   = os.environ.get("PROXY_URL", "").strip() or None   # None = без прокси
CAPTCHA_KEY = os.environ.get("CAPTCHA_API_KEY", "").strip() or None
FILE_DELAY  = float(os.environ.get("REQUEST_DELAY", "2.0"))

# ─── USER-AGENT ПУЛ ───────────────────────────────────────────────────────────
# Все UA — реальные строки актуальных Chrome/Safari (2024–2025).
# Случайный выбор при каждом запросе снижает вероятность блокировки по UA.

_UA_POOL = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Chrome macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Safari macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    # Firefox Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    # Chrome Android (мобильный UA — нужен для Facebook mobile)
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
    # Safari iPhone (нужен для Instagram)
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1",
]

# Базовый UA — desktop Chrome (по умолчанию для большинства запросов)
BROWSER_UA = _UA_POOL[0]

def rand_ua(mobile: bool = False, ios: bool = False) -> str:
    """Возвращает случайный User-Agent из пула.

    mobile=True  — Android Chrome
    ios=True     — Safari iPhone
    иначе        — desktop Chrome/Firefox/Safari
    """
    if ios:
        pool = [ua for ua in _UA_POOL if "iPhone" in ua]
    elif mobile:
        pool = [ua for ua in _UA_POOL if "Android" in ua or "iPhone" in ua]
    else:
        pool = [ua for ua in _UA_POOL if "Android" not in ua and "iPhone" not in ua]
    return random.choice(pool or _UA_POOL)


def browser_headers(mobile: bool = False, ios: bool = False) -> dict:
    """Полный набор заголовков с рандомным UA."""
    ua = rand_ua(mobile=mobile, ios=ios)
    hdrs = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
    }
    # Sec-CH-UA заголовки только для Chrome
    if "Chrome/" in ua and "Android" not in ua and "iPhone" not in ua:
        # Извлекаем версию Chrome
        m = re.search(r"Chrome/(\d+)", ua)
        ver = m.group(1) if m else "124"
        hdrs.update({
            "sec-ch-ua": f'"Google Chrome";v="{ver}", "Chromium";v="{ver}", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"' if "Windows" in ua else '"macOS"',
        })
    elif "Chrome/" in ua and "Android" in ua:
        m = re.search(r"Chrome/(\d+)", ua)
        ver = m.group(1) if m else "124"
        hdrs.update({
            "sec-ch-ua": f'"Google Chrome";v="{ver}", "Chromium";v="{ver}", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": '"Android"',
        })
    return hdrs


# Для обратной совместимости — статичный вариант без рандомизации
BROWSER_HEADERS = browser_headers()


# ─── ПРОКСИ-ХЕЛПЕР ────────────────────────────────────────────────────────────

def _normalize_proxy_url(raw: str) -> str:
    """Нормализует URL прокси.

    Проблема: если пароль содержит @, :, #, /, ? и т.д. — urlparse ломается.
    Решение: парсим вручную, кодируем user/pass через urllib.parse.quote.

    Поддерживаемые форматы:
      socks5://user:pass@host:port
      http://user:pass@host:port
      socks5://host:port              (без авторизации)
    """
    from urllib.parse import quote, urlparse, urlunparse

    raw = raw.strip()
    # Нормализуем схему в нижний регистр (SOCKS5:// → socks5://)
    if "://" in raw:
        scheme, rest = raw.split("://", 1)
        raw = scheme.lower() + "://" + rest

    try:
        p = urlparse(raw)
        # Если нет user/pass — возвращаем как есть
        if not p.username and not p.password:
            return raw
        # Кодируем спецсимволы в логине и пароле
        user = quote(p.username or "", safe="")
        pwd  = quote(p.password or "", safe="")
        host = p.hostname or ""
        port = f":{p.port}" if p.port else ""
        netloc = f"{user}:{pwd}@{host}{port}"
        return urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment))
    except Exception:
        return raw  # Не смогли распарсить — используем как есть


# Нормализованный URL прокси (кодирует спецсимволы в пароле)
_PROXY_NORMALIZED: str | None = _normalize_proxy_url(PROXY_URL) if PROXY_URL else None


def proxy_args() -> dict:
    """Возвращает kwargs для httpx.AsyncClient с прокси (если задан PROXY_URL).

    Если PROXY_URL не задан — возвращает пустой dict (прямое соединение).
    """
    if not _PROXY_NORMALIZED:
        return {}
    return {"proxy": _PROXY_NORMALIZED}


def make_client(
    *,
    mobile: bool = False,
    ios: bool = False,
    timeout: float = 20.0,
    http2: bool = False,
    extra_headers: dict | None = None,
) -> httpx.AsyncClient:
    """Фабрика изолированного httpx.AsyncClient.

    Каждый клиент получает:
      - случайный User-Agent из пула
      - прокси из PROXY_URL (если задан)
      - изолированный cookie jar
      - реалистичные browser-заголовки
    """
    hdrs = browser_headers(mobile=mobile, ios=ios)
    if extra_headers:
        hdrs.update(extra_headers)
    return httpx.AsyncClient(
        headers=hdrs,
        timeout=timeout,
        follow_redirects=True,
        http2=http2,
        **proxy_args(),
    )


# ─── JITTER-ЗАДЕРЖКИ ──────────────────────────────────────────────────────────

async def jitter(base: float = 0.5, spread: float = 1.0) -> None:
    """Асинхронная задержка base ± spread/2 секунд.

    Имитирует «человеческие» паузы между запросами, снижает вероятность
    rate-limit-блокировок.

    base=0.5, spread=1.0  →  задержка 0.0–1.5с
    base=1.5, spread=2.0  →  задержка 0.5–3.5с
    """
    delay = base + random.uniform(0, spread)
    await asyncio.sleep(delay)


# ─── CAPTCHA-ХЕЛПЕР (опционально, 2captcha.com) ───────────────────────────────

async def solve_recaptcha_v2(site_key: str, page_url: str) -> str | None:
    """Отправляет reCAPTCHA v2 на 2captcha.com и возвращает g-recaptcha-response.

    Требует: CAPTCHA_API_KEY в окружении.
    Если ключа нет — возвращает None (капча не решается, чекер вернёт ошибку).

    Алгоритм:
      1. POST /in.php  — отправить задачу → получить task_id
      2. Polling GET /res.php?action=get&id=<task_id> каждые 5с до 120с
         пока ответ не станет "OK|<token>"
    """
    if not CAPTCHA_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(
                "https://2captcha.com/in.php",
                data={
                    "key": CAPTCHA_KEY,
                    "method": "userrecaptcha",
                    "googlekey": site_key,
                    "pageurl": page_url,
                    "json": "1",
                },
            )
            d = r.json()
            if d.get("status") != 1:
                return None
            task_id = d["request"]

            for _ in range(24):       # 24 × 5с = 120с максимум
                await asyncio.sleep(5)
                r2 = await c.get(
                    "https://2captcha.com/res.php",
                    params={"key": CAPTCHA_KEY, "action": "get", "id": task_id, "json": "1"},
                )
                d2 = r2.json()
                if d2.get("status") == 1:
                    return d2["request"]
                if d2.get("request") not in ("CAPCHA_NOT_READY", "CAPTCHA_NOT_READY"):
                    break
    except Exception:
        pass
    return None

# Ссылки на профили
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


# ─── УТИЛИТЫ ──────────────────────────────────────────────────────────────────

def is_email(text: str) -> bool:
    return bool(EMAIL_RE.match(text.strip()))


def rand_str(n: int) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def h(text: str) -> str:
    """Экранирует спецсимволы HTML для Telegram."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def make_link(text: str, url: str) -> str:
    """Ссылка в формате HTML."""
    return f'<a href="{h(url)}">{h(text)}</a>'


def profile_url(platform: str, username: str) -> str:
    t = PROFILE_URLS.get(platform, "")
    return t.format(u=username) if t else ""


# ─── KEYBOARDS ────────────────────────────────────────────────────────────────

def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📧 Email → аккаунты", callback_data="mode:email"),
            InlineKeyboardButton("👤 Username → сети", callback_data="mode:user"),
        ],
        [InlineKeyboardButton("🔀 Email + Username сразу", callback_data="mode:both")],
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
# Каждый вернёт dict:
#   {"found": True}             — точно зарегистрирован
#   {"found": False}            — точно НЕ зарегистрирован
#   {"found": "rate_limit"}     — превышен лимит запросов
#   {"error": "..."}            — техническая ошибка

async def _check_twitter_email(email: str, _unused=None) -> dict:
    """
    Twitter/X email check.
    GET https://api.twitter.com/i/users/email_available.json?email=...
    {"taken": true} → зарегистрирован.
    Использует make_client() → случайный UA + прокси (если PROXY_URL задан).
    """
    try:
        await jitter(0.2, 0.6)
        async with make_client(
            extra_headers={
                "Accept": "application/json",
                "Referer": "https://x.com/",
                "x-twitter-active-user": "yes",
                "x-twitter-client-language": "en",
            },
            timeout=12.0,
        ) as c:
            r = await c.get(
                "https://api.twitter.com/i/users/email_available.json",
                params={"email": email},
            )
        if r.status_code == 200:
            d = r.json()
            if d.get("taken") is True or d.get("reason") == "taken":
                return {"found": True}
            return {"found": False}
        if r.status_code == 429:
            return {"found": "rate_limit"}
        return {"error": f"Twitter: HTTP {r.status_code}"}
    except Exception as e:
        return {"error": str(e)[:80]}


def _fb_parse_accounts(html: str) -> list[dict]:
    """
    Парсит HTML ответа Facebook identify-flow.
    Ищет имена аккаунтов, ссылки на профили и URL фото.
    Возвращает список: [{"name": "...", "url": "...", "photo": "..."}]
    """
    from bs4 import BeautifulSoup
    import json as _json

    accounts: list[dict] = []

    # Попытка 1: AJAX ответ (for (;;); + JSON с HTML внутри)
    raw = html
    if raw.startswith("for (;;);"):
        raw = raw[9:]
    try:
        data = _json.loads(raw)
        # Facebook кладёт HTML либо в domops, либо в payload/content
        inner_html = ""
        for key in ("payload", "jsmods", "domops"):
            val = data.get(key)
            if isinstance(val, str) and len(val) > 50:
                inner_html = val
                break
            if isinstance(val, dict):
                inner_html = str(val)
                break
        if inner_html:
            html = inner_html
    except Exception:
        pass

    soup = BeautifulSoup(html, "html.parser")

    # Попытка 2: найти ссылки содержащие /profile.php или facebook.com/
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        # Нормализуем относительные ссылки
        if href.startswith("/"):
            href = "https://www.facebook.com" + href

        # Пропускаем служебные страницы
        skip_patterns = [
            "/login", "/r.php", "/recover", "/identify",
            "help.facebook", "#", "javascript",
            "/policies", "/privacy", "/terms",
        ]
        if any(p in href for p in skip_patterns):
            continue

        is_profile = (
            "profile.php?id=" in href
            or re.search(r"facebook\.com/[A-Za-z0-9_.]{3,}/?$", href)
        )
        if not is_profile:
            continue

        # Имя — текст внутри ссылки или ближайший текстовый узел
        name = a.get_text(strip=True)
        if not name:
            # Ищем соседний текст
            parent = a.parent
            if parent:
                name = parent.get_text(strip=True)
        name = re.sub(r"\s+", " ", name).strip()[:60]
        if not name or len(name) < 2:
            name = "Facebook аккаунт"

        # Фото — img внутри ссылки или рядом
        photo = ""
        img = a.find("img")
        if not img and a.parent:
            img = a.parent.find("img")
        if img and img.get("src", "").startswith("http"):
            photo = img["src"]

        if href not in seen:
            seen.add(href)
            accounts.append({"name": name, "url": href, "photo": photo})

    # Попытка 3: вытащить имена и UID из JSON-данных в <script>
    if not accounts:
        # Ищем паттерны вида "name":"John Doe","id":"123456"
        name_ids = re.findall(r'"name"\s*:\s*"([^"]{2,60})"[^}]*?"id"\s*:\s*"(\d{6,})"', html)
        for name, uid in name_ids[:5]:
            url = f"https://www.facebook.com/profile.php?id={uid}"
            if url not in seen:
                seen.add(url)
                accounts.append({"name": name, "url": url, "photo": ""})

    return accounts[:5]  # Максимум 5 аккаунтов


async def _check_facebook_email(email: str, _unused=None) -> dict:
    """
    Facebook — identify flow (forgot-password).
    Извлекает имя аккаунта, ссылку на профиль и фото.

    Шаг 1: GET /login/identify/?ctx=recover → LSD + jazoest токены
    Шаг 2: POST email на identify → парсим аккаунты из HTML-ответа
    Шаг 3: Fallback — registration check через /r.php

    Возвращает:
      {"found": True, "accounts": [{"name": "...", "url": "...", "photo": "..."}]}
      {"found": False}
      {"error": "..."}
    """
    try:
        await jitter(0.3, 0.8)
        async with make_client(
            extra_headers={"Upgrade-Insecure-Requests": "1"},
            timeout=25.0,
            http2=False,
        ) as c:

            # ── Шаг 1: Identify page — основной путь ──────────────────
            r1 = await c.get(
                "https://www.facebook.com/login/identify/",
                params={"ctx": "recover"},
                headers={"Referer": "https://www.google.com/"},
            )
            html1 = r1.text

            def _extract_tokens(html: str) -> tuple[str, str]:
                lsd_m = (
                    re.search(r'\["LSD",\[\],\{"token":"([^"]+)"\}', html)
                    or re.search(r'name="lsd"\s+value="([^"]+)"', html)
                    or re.search(r'"lsd"\s*:\s*"([^"]+)"', html)
                    or re.search(r'"token"\s*:\s*"([A-Za-z0-9_\-]{6,})"', html)
                )
                j_m = (
                    re.search(r'name="jazoest"\s+value="(\d+)"', html)
                    or re.search(r'jazoest=(\d+)', html)
                )
                return (lsd_m.group(1) if lsd_m else ""), (j_m.group(1) if j_m else "2488")

            lsd, jazoest = _extract_tokens(html1)

            # Если не нашли токены — пробуем m.facebook.com
            if not lsd:
                r1m = await c.get(
                    "https://m.facebook.com/login/identify/",
                    params={"ctx": "recover"},
                    headers={"Referer": "https://www.google.com/"},
                )
                lsd, jazoest = _extract_tokens(r1m.text)
                if not lsd:
                    return {"error": "FB: нет LSD токена — IP сервера заблокирован Facebook"}

            # ── Шаг 2: Отправляем email в identify ────────────────────
            r2 = await c.post(
                "https://www.facebook.com/login/identify/",
                params={"ctx": "recover"},
                data={
                    "lsd": lsd,
                    "jazoest": jazoest,
                    "email": email,
                    "did_submit": "1",
                    "__a": "1",
                    "__req": "2",
                    "__user": "0",
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": "https://www.facebook.com",
                    "Referer": "https://www.facebook.com/login/identify/?ctx=recover",
                    "x-fb-lsd": lsd,
                    "x-requested-with": "XMLHttpRequest",
                    "sec-fetch-site": "same-origin",
                    "sec-fetch-mode": "cors",
                    "sec-fetch-dest": "empty",
                },
            )
            body2 = r2.text

            # Явный сигнал — аккаунт не найден
            not_found_signals = [
                "No search results", "no_results", "no accounts found",
                "couldn't find", "not found", "ACCOUNT_NOT_FOUND",
            ]
            if any(s.lower() in body2.lower() for s in not_found_signals):
                return {"found": False}

            # Явный сигнал — аккаунт найден
            found_signals = [
                "These accounts matched", "account matched",
                "redirectPageTo", "checkpointLoginHelp",
                "profile.php", "facebook.com/",
            ]
            if any(s in body2 for s in found_signals):
                accounts = _fb_parse_accounts(body2)
                return {"found": True, "accounts": accounts}

            # ── Шаг 3: Fallback — registration check ──────────────────
            r3 = await c.get(
                "https://www.facebook.com/r.php",
                headers={"Referer": "https://www.google.com/"},
            )
            lsd3, jazoest3 = _extract_tokens(r3.text)
            if lsd3:
                r4 = await c.post(
                    "https://www.facebook.com/api/v1/web/accounts/web_create_ajax/attempt/",
                    data={
                        "jazoest": jazoest3,
                        "lsd": lsd3,
                        "email": email,
                        "username": rand_str(14),
                        "first_name": "Test",
                        "opt_into_one_tap": "false",
                    },
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Origin": "https://www.facebook.com",
                        "Referer": "https://www.facebook.com/r.php",
                        "x-fb-lsd": lsd3,
                        "sec-fetch-site": "same-origin",
                        "sec-fetch-mode": "cors",
                        "sec-fetch-dest": "empty",
                    },
                )
                body4 = r4.text
                if "email_is_taken" in body4 or "EMAIL_IS_TAKEN" in body4:
                    return {"found": True, "accounts": []}
                if "email_sharing_limit" in body4:
                    return {"found": True, "accounts": []}
                if '"status":"ok"' in body4 or '"errors":{}' in body4:
                    return {"found": False}

            return {"error": "FB: неопределённый ответ (возможно IP блокируется)"}

    except Exception as e:
        return {"error": str(e)[:80]}


async def _check_instagram_email(email: str, _unused=None) -> dict:
    """
    Instagram — iOS Safari UA (требование IG API), прокси из PROXY_URL.
    POST /api/v1/users/check_email/ → error_type == "email_is_taken"
    """
    try:
        await jitter(0.3, 0.7)
        async with make_client(
            ios=True,
            http2=True,
            timeout=15.0,
        ) as c:
            r0 = await c.get("https://www.instagram.com/")
            csrf = c.cookies.get("csrftoken")
            if not csrf:
                m = re.search(r'csrf_token["\']?\s*:\s*["\']([^"\']+)', r0.text)
                if m:
                    csrf = m.group(1)
            if not csrf:
                return {"error": "Instagram: нет CSRF (IP заблокирован)"}

            r1 = await c.post(
                "https://www.instagram.com/api/v1/users/check_email/",
                data={"email": email, "sign_up_code": ""},
                headers={
                    "x-csrftoken": csrf,
                    "x-ig-app-id": "936619743392459",
                    "x-requested-with": "XMLHttpRequest",
                    "Origin": "https://www.instagram.com",
                    "Referer": "https://www.instagram.com/",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            if r1.status_code == 200:
                d = r1.json()
                if d.get("error_type") == "email_is_taken":
                    return {"found": True}
                if d.get("available") is True:
                    return {"found": False}
                return {"error": f"IG: неожиданный ответ: {list(d.keys())}"}
            if r1.status_code == 400:
                d = r1.json()
                if d.get("spam") is True:
                    return {"found": False}
                return {"error": f"IG: 400 {d}"}
            if r1.status_code == 429:
                return {"found": "rate_limit"}
            return {"error": f"IG: HTTP {r1.status_code}"}
    except Exception as e:
        return {"error": str(e)[:80]}


async def _check_linkedin_email(email: str, _unused=None) -> dict:
    """
    LinkedIn — два метода в одной сессии (куки метода A → метод B).

    Метод A (forgot-password):
      GET /checkpoint/lg/forgot-password → POST /reset-password-init
      "check your email" → найден; "member not found" → не найден.

    Метод B (login-form fallback):
      GET /login → POST /login-submit с неверным паролем
      "wrong password" / redirect → найден; "don't recognize" → не найден.

    Использует make_client() → случайный desktop Chrome UA + прокси.
    """
    try:
        await jitter(0.4, 0.8)
        async with make_client(
            timeout=20.0,
            http2=False,  # LinkedIn работает стабильнее на HTTP/1.1
        ) as c:

            # ── Метод A: forgot-password ──────────────────────────────
            r1 = await c.get("https://www.linkedin.com/checkpoint/lg/forgot-password")
            csrf_m = (
                re.search(r'name="loginCsrfParam"\s+value="([^"]+)"', r1.text)
                or re.search(r'"loginCsrfParam"\s*:\s*"([^"]+)"', r1.text)
                or re.search(r'name="csrfToken"\s+value="([^"]+)"', r1.text)
            )

            if csrf_m:
                r2 = await c.post(
                    "https://www.linkedin.com/checkpoint/lg/reset-password-init",
                    data={
                        "session_key": email,
                        "loginCsrfParam": csrf_m.group(1),
                        "isJsEnabled": "false",
                    },
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Origin": "https://www.linkedin.com",
                        "Referer": "https://www.linkedin.com/checkpoint/lg/forgot-password",
                    },
                )
                body2 = r2.text.lower()
                url2 = str(r2.url).lower()

                found_a = ["we sent", "check your email", "reset link",
                           "email has been sent", "password-reset-email-sent", "check-email"]
                for s in found_a:
                    if s in body2 or s in url2:
                        return {"found": True}

                not_found_a = ["member not found", "not on file", "no account",
                               "couldn't find", "don't have an account", "unknown_email"]
                for s in not_found_a:
                    if s in body2 or s in url2:
                        return {"found": False}

            # ── Метод B: login form (fallback) ────────────────────────
            r3 = await c.get("https://www.linkedin.com/login")
            csrf2_m = (
                re.search(r'name="loginCsrfParam"\s+value="([^"]+)"', r3.text)
                or re.search(r'"loginCsrfParam"\s*:\s*"([^"]+)"', r3.text)
            )
            if not csrf2_m:
                return {"error": "LinkedIn: не удалось получить CSRF (возможно блокировка IP)"}

            r4 = await c.post(
                "https://www.linkedin.com/checkpoint/lg/login-submit",
                data={
                    "session_key": email,
                    "session_password": "Wr0ng_Pa55w0rd_Pr0be_x9!2024",
                    "loginCsrfParam": csrf2_m.group(1),
                    "isJsEnabled": "false",
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": "https://www.linkedin.com",
                    "Referer": "https://www.linkedin.com/login",
                },
            )
            body4 = r4.text.lower()
            url4 = str(r4.url).lower()

            not_found_b = ["don't recognize", "doesn't recognize",
                           "unknown_email", "no account", "hmm, that"]
            for s in not_found_b:
                if s in body4:
                    return {"found": False}

            found_b = ["wrong password", "incorrect password", "too many incorrect",
                       "enter your password", "add-password", "challenge", "authwall"]
            for s in found_b:
                if s in body4 or s in url4:
                    return {"found": True}

            # Если LinkedIn вернул /checkpoint/ → email известен (нужна 2FA и т.п.)
            if "/checkpoint/" in url4 or "/uas/" in url4:
                return {"found": True}

            return {"error": "LinkedIn: неопределённый ответ"}

    except Exception as e:
        return {"error": str(e)[:80]}


async def _check_discord_email(email: str, _unused=None) -> dict:
    """
    Discord — registration API v10.
    EMAIL_ALREADY_REGISTERED → зарегистрирован.
    Использует make_client() → случайный UA + прокси.
    """
    try:
        await jitter(0.2, 0.5)
        async with make_client(
            extra_headers={
                "Content-Type": "application/json",
                "Accept": "*/*",
                "Origin": "https://discord.com",
                "Referer": "https://discord.com/register",
            },
            timeout=12.0,
        ) as c:
            r = await c.post(
                "https://discord.com/api/v10/auth/register",
                json={
                    "fingerprint": "",
                    "email": email,
                    "username": rand_str(16),
                    "password": rand_str(20),
                    "consent": True,
                    "date_of_birth": "1995-01-01",
                    "gift_code_sku_id": None,
                    "captcha_key": None,
                },
            )
        d = r.json()
        if r.status_code in (200, 400):
            errs = d.get("errors", {}).get("email", {}).get("_errors", [])
            if errs:
                if errs[0].get("code") == "EMAIL_ALREADY_REGISTERED":
                    return {"found": True}
                return {"found": False}
            if d.get("captcha_key"):
                return {"found": "rate_limit"}
            return {"found": False}
        if r.status_code == 429:
            return {"found": "rate_limit"}
        return {"error": f"Discord: HTTP {r.status_code}"}
    except Exception as e:
        return {"error": str(e)[:80]}


async def _check_cryptorank_email(email: str, _unused=None) -> dict:
    """
    CryptoRank — XSRF-TOKEN сессия + прокси.

    Шаг 1: GET cryptorank.io/ → получаем XSRF-TOKEN cookie
    Шаг 2: POST /api/v0/auth/reset-password с X-XSRF-TOKEN заголовком
      {"success": true}             → найден
      {"message": "User not found"} → не найден
      403 повторно                  → пробуем v1 / forgot-password endpoints
    """
    try:
        await jitter(0.3, 0.7)
        async with make_client(
            extra_headers={
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://cryptorank.io",
                "Referer": "https://cryptorank.io/",
            },
            timeout=15.0,
        ) as c:
            # Шаг 1: получаем XSRF-TOKEN из cookies
            await c.get("https://cryptorank.io/")
            xsrf = c.cookies.get("XSRF-TOKEN") or c.cookies.get("xsrf-token") or ""

            api_headers = {
                "Content-Type": "application/json",
                "X-XSRF-TOKEN": xsrf,
                "X-Requested-With": "XMLHttpRequest",
            }

            # Шаг 2: пробуем endpoints по очереди
            for endpoint in [
                "https://cryptorank.io/api/v0/auth/reset-password",
                "https://cryptorank.io/api/v1/auth/reset-password",
                "https://cryptorank.io/api/v0/auth/forgot-password",
            ]:
                try:
                    r = await c.post(endpoint, json={"email": email}, headers=api_headers)
                    if r.status_code == 403:
                        continue
                    if r.status_code in (200, 201):
                        d = r.json()
                        if d.get("success") is True or d.get("ok") is True:
                            return {"found": True}
                        msg = (d.get("message") or d.get("error") or "").lower()
                        if any(s in msg for s in ["not found", "no user", "not exist", "not registered"]):
                            return {"found": False}
                        if any(s in msg for s in ["too many", "rate limit"]):
                            return {"found": "rate_limit"}
                        # success=false без явного "not found" — двусмысленно
                        # Скорее всего нашёл и отправил письмо
                        return {"found": True}
                    if r.status_code == 404:
                        return {"found": False}
                    if r.status_code == 422:
                        # Unprocessable: email валидный, но не найден
                        return {"found": False}
                    if r.status_code == 429:
                        return {"found": "rate_limit"}
                except Exception:
                    continue

            return {"error": "CryptoRank: все endpoints вернули 403 (нет XSRF или блокировка)"}

    except Exception as e:
        return {"error": str(e)[:80]}


async def scan_email(email: str) -> tuple[dict, float]:
    """
    Параллельная проверка email на всех платформах.
    Каждый чекер использует СВОЮ изолированную сессию — куки не смешиваются.
    """
    start = time.time()
    results = await asyncio.gather(
        _check_twitter_email(email),
        _check_facebook_email(email),
        _check_instagram_email(email),
        _check_linkedin_email(email),
        _check_discord_email(email),
        _check_cryptorank_email(email),
        return_exceptions=False,
    )
    elapsed = time.time() - start
    return dict(zip(
        ["Twitter/X", "Facebook", "Instagram", "LinkedIn", "Discord", "CryptoRank"],
        results,
    )), elapsed


# ─── USERNAME CHECKERS ────────────────────────────────────────────────────────

async def _check_twitter_user(username: str) -> dict:
    """
    Twitter/X username — страница профиля.
    404 → не найден; "account doesn't exist" → не найден;
    200 без явных "не существует" → найден (X отдаёт 404 на несуществующие аккаунты).
    Использует make_client() → случайный UA + прокси.
    """
    url = f"https://x.com/{username}"
    try:
        await jitter(0.2, 0.6)
        async with make_client(
            extra_headers={"Accept": "text/html"},
            timeout=15.0,
        ) as c:
            r = await c.get(url)
        if r.status_code == 404:
            return {"found": False}
        if r.status_code == 200:
            text = r.text.lower()
            not_found = [
                "account doesn",
                "this account doesn",
                "caution: this account",
                "suspended",
                "@" + username.lower() + " doesn",
            ]
            if any(s in text for s in not_found):
                return {"found": False}
            # 200 без сигналов отсутствия = профиль существует
            return {"found": True, "url": url}
        if r.status_code == 429:
            return {"found": "rate_limit", "url": url}
        return {"error": f"Twitter: HTTP {r.status_code}"}
    except Exception as e:
        return {"error": str(e)[:80]}


async def _check_linkedin_user(username: str) -> dict:
    """
    LinkedIn username — страница профиля.
    404 → не найден; 999 → найден (LinkedIn блокирует ботов кодом 999,
    но это значит профиль есть); authwall redirect → найден.
    Использует make_client() → случайный UA + прокси.
    """
    url = f"https://www.linkedin.com/in/{username}"
    try:
        await jitter(0.3, 0.7)
        async with make_client(
            extra_headers={"Accept": "text/html,application/xhtml+xml"},
            timeout=15.0,
        ) as c:
            r = await c.get(url)
        if r.status_code == 404:
            return {"found": False}
        if r.status_code == 200:
            final = str(r.url).lower()
            if "authwall" in final or "/login" in final:
                return {"found": True, "url": url}
            if "profile not found" in r.text.lower() or "page not found" in r.text.lower():
                return {"found": False}
            return {"found": True, "url": url}
        if r.status_code == 999:
            # LinkedIn активно блокирует ботов 999 кодом — но страница ЕСТЬ
            return {"found": True, "url": url}
        if r.status_code == 429:
            return {"found": "rate_limit", "url": url}
        return {"error": f"LinkedIn: HTTP {r.status_code}"}
    except Exception as e:
        return {"error": str(e)[:80]}


async def _check_cryptorank_user(username: str) -> dict:
    """
    CryptoRank username.
    1. GET /api/v0/user/info?username=...  (JSON API, прямой ответ)
    2. Fallback: GET /profile/<username>   (страница профиля)
    Использует make_client() → случайный UA + прокси.
    """
    purl = f"https://cryptorank.io/profile/{username}"
    try:
        await jitter(0.2, 0.5)
        async with make_client(
            extra_headers={
                "Accept": "application/json, text/html, */*",
                "Referer": "https://cryptorank.io/",
            },
            timeout=15.0,
        ) as c:
            # Метод 1: JSON API
            try:
                r = await c.get(
                    "https://cryptorank.io/api/v0/user/info",
                    params={"username": username},
                    headers={"Accept": "application/json"},
                )
                if r.status_code == 200:
                    d = r.json()
                    if d.get("data") and str(d["data"].get("username", "")).lower() == username.lower():
                        return {"found": True, "url": purl}
                    return {"found": False}
                if r.status_code == 404:
                    return {"found": False}
            except Exception:
                pass

            # Метод 2: страница профиля
            r2 = await c.get(purl, headers={"Accept": "text/html"})
            if r2.status_code == 404:
                return {"found": False}
            if r2.status_code == 200:
                t = r2.text.lower()
                if "page not found" in t or '"statusCode":404' in t:
                    return {"found": False}
                if username.lower() in t[:8000]:
                    return {"found": True, "url": purl}
                return {"found": False}
            return {"error": f"CryptoRank: HTTP {r2.status_code}"}
    except Exception as e:
        return {"error": str(e)[:80]}


async def _check_opensea_user(username: str) -> dict:
    """
    OpenSea username — scraping страницы профиля (без API-ключа).
    Проверяем og:url, og:title и вхождение username в HTML.
    Использует make_client() → случайный UA + прокси.
    """
    purl = f"https://opensea.io/{username}"
    try:
        await jitter(0.2, 0.5)
        async with make_client(
            extra_headers={"Accept": "text/html,application/xhtml+xml,*/*"},
            timeout=15.0,
        ) as c:
            r = await c.get(purl)
        if r.status_code == 404:
            return {"found": False}
        if r.status_code == 200:
            text = r.text
            text_low = text.lower()

            not_found_signals = [
                "page not found",
                "this page could not be found",
                "account not found",
                '"statusCode":404',
            ]
            for s in not_found_signals:
                if s in text_low[:4000]:
                    return {"found": False}

            og_url = re.search(r'<meta[^>]+property="og:url"[^>]+content="([^"]+)"', text)
            og_title = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', text)

            if og_url and username.lower() in og_url.group(1).lower():
                return {"found": True, "url": purl}
            if og_title and username.lower() in og_title.group(1).lower():
                return {"found": True, "url": purl}
            if (
                f'"{username.lower()}"' in text_low[:10000]
                or f'/{username.lower()}"' in text_low[:10000]
            ):
                return {"found": True, "url": purl}

            # Страница загрузилась без "not found" → профиль существует
            return {"found": True, "url": purl}

        if r.status_code == 429:
            return {"found": "rate_limit", "url": purl}
        return {"error": f"OpenSea: HTTP {r.status_code}"}
    except Exception as e:
        return {"error": str(e)[:80]}


async def scan_username(username: str) -> tuple[dict, float]:
    """
    Параллельная проверка username на всех платформах.
    Каждый чекер использует СВОЮ изолированную сессию.
    """
    start = time.time()
    tw, li, cr, os_ = await asyncio.gather(
        _check_twitter_user(username),
        _check_linkedin_user(username),
        _check_cryptorank_user(username),
        _check_opensea_user(username),
    )
    elapsed = time.time() - start
    return {
        "Twitter/X":  tw,
        "LinkedIn":   li,
        "Facebook":   {"found": "manual", "url": f"https://www.facebook.com/{username}"},
        "CryptoRank": cr,
        "OpenSea":    os_,
    }, elapsed


# ─── ФОРМАТТЕРЫ (HTML) ────────────────────────────────────────────────────────

def _email_line(platform: str, res: dict) -> str:
    icon = ICONS.get(platform, "🔎")
    pname = h(platform)
    if res.get("found") is True:
        lines = [f"{icon} <b>{pname}:</b> ✅ Зарегистрирован"]
        # Показываем аккаунты если есть (Facebook identify-flow)
        for acc in res.get("accounts") or []:
            acc_name = h(acc.get("name") or "")
            acc_url  = acc.get("url") or ""
            acc_photo = acc.get("photo") or ""
            parts = []
            if acc_name:
                parts.append(f"<b>{acc_name}</b>")
            if acc_url:
                parts.append(make_link("→ профиль", acc_url))
            if acc_photo:
                parts.append(make_link("🖼 фото", acc_photo))
            if parts:
                lines.append("   " + "  ".join(parts))
        return "\n".join(lines)
    if res.get("found") == "rate_limit":
        return f"{icon} <b>{pname}:</b> ⏳ Rate limit — повтори позже"
    if res.get("found") is False:
        return f"{icon} <b>{pname}:</b> ❌ Не зарегистрирован"
    if res.get("error"):
        return f"{icon} <b>{pname}:</b> 🔴 <code>{h(res['error'])}</code>"
    return f"{icon} <b>{pname}:</b> ❓"


def fmt_email(email: str, results: dict, elapsed: float) -> str:
    lines = [f"📧 <b>Email:</b> <code>{h(email)}</code>\n"]
    for p in ["Twitter/X", "Facebook", "Instagram", "LinkedIn", "Discord", "CryptoRank"]:
        lines.append(_email_line(p, results.get(p, {})))
    lines.append(f"\n⏱ Проверено за <b>{elapsed:.1f}с</b>")
    return "\n".join(lines)


def _user_line(platform: str, res: dict, username: str) -> str:
    icon = ICONS.get(platform, "🔎")
    name = h(platform)
    url = res.get("url") or profile_url(platform, username)
    link = make_link("профиль", url) if url else ""

    if res.get("found") == "manual":
        return f"{icon} <b>{name}:</b> 🔗 {make_link('проверь вручную', url)}"
    if res.get("found") is True:
        return f"{icon} <b>{name}:</b> ✅ Найден — {link}"
    if res.get("found") == "maybe":
        note = h(res.get("note", ""))
        return f"{icon} <b>{name}:</b> ⚠️ Вероятно — {link}\n   <i>{note}</i>"
    if res.get("found") == "rate_limit":
        return f"{icon} <b>{name}:</b> ⏳ Rate limit — {link}"
    if res.get("found") is False:
        return f"{icon} <b>{name}:</b> ❌ Не найден"
    if res.get("error"):
        return f"{icon} <b>{name}:</b> 🔴 <code>{h(res['error'])}</code>"
    return f"{icon} <b>{name}:</b> ❓"


def fmt_username(username: str, results: dict, elapsed: float) -> str:
    lines = [f"👤 <b>Username:</b> <code>{h(username)}</code>\n"]
    for p in ["Twitter/X", "LinkedIn", "Facebook", "CryptoRank", "OpenSea"]:
        lines.append(_user_line(p, results.get(p, {}), username))
    lines.append(f"\n⏱ Проверено за <b>{elapsed:.1f}с</b>")
    return "\n".join(lines)


# ─── HANDLERS ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 <b>OSINT-бот</b> — поиск аккаунтов по email и username\n\n"
        "Платформы: Twitter/X, Facebook, Instagram, LinkedIn, Discord, CryptoRank, OpenSea",
        reply_markup=kb_main(),
        parse_mode=ParseMode.HTML,
    )


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    data = q.data

    async def safe_edit(text: str, **kwargs) -> None:
        """edit_message_text, игнорируя 'Message is not modified' (двойной клик)."""
        try:
            await q.edit_message_text(text, **kwargs)
        except Exception as exc:
            if "not modified" in str(exc).lower():
                return
            raise

    if data == "back:main":
        context.user_data.clear()
        await safe_edit(
            "👋 <b>OSINT-бот</b> — выбери режим:",
            reply_markup=kb_main(),
            parse_mode=ParseMode.HTML,
        )

    elif data == "help":
        help_text = (
            "<b>Методы проверки:</b>\n\n"
            "🐦 <b>Twitter/X</b> — email_available API (точный)\n"
            "📘 <b>Facebook</b> — forgot-password flow (точный)\n"
            "📸 <b>Instagram</b> — registration API (точный)\n"
            "💼 <b>LinkedIn</b> — forgot-password + login flow (точный)\n"
            "🎮 <b>Discord</b> — registration API v10 (точный)\n"
            "📊 <b>CryptoRank</b> — reset-password API (точный)\n"
            "🌊 <b>OpenSea</b> — страница профиля по username\n\n"
            "При email-проверке: только ✅ / ❌ — никакого «возможно»\n"
            "При username-проверке: кликабельная ссылка на профиль"
        )
        await safe_edit(help_text, reply_markup=kb_back(), parse_mode=ParseMode.HTML)

    elif data.startswith("mode:"):
        mode = data.split(":")[1]
        context.user_data["mode"] = mode
        context.user_data.pop("both_step", None)
        prompts = {
            "email": "Введи <b>email</b> для проверки:",
            "user":  "Введи <b>username</b> для поиска:",
            "both":  "Введи <b>email</b> для проверки (потом попрошу username):",
            "file":  "Отправь <code>.txt</code> файл (каждый email или username на новой строке):",
        }
        await safe_edit(
            prompts[mode],
            reply_markup=kb_cancel(),
            parse_mode=ParseMode.HTML,
        )


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    mode = context.user_data.get("mode")
    text = (update.message.text or "").strip()

    if not mode:
        await update.message.reply_text("Выбери режим:", reply_markup=kb_main())
        return

    if mode == "email":
        if not is_email(text):
            await update.message.reply_text("❌ Неверный формат email. Попробуй ещё раз.")
            return
        msg = await update.message.reply_text("⏳ Проверяю email...")
        results, elapsed = await scan_email(text)
        await msg.edit_text(
            fmt_email(text, results, elapsed),
            parse_mode=ParseMode.HTML,
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
            parse_mode=ParseMode.HTML,
            reply_markup=kb_back(),
        )

    elif mode == "both":
        step = context.user_data.get("both_step", "email")
        if step == "email":
            if not is_email(text):
                await update.message.reply_text("❌ Неверный формат email.")
                return
            context.user_data["both_step"] = "user"
            msg = await update.message.reply_text("⏳ Проверяю email...")
            results, elapsed = await scan_email(text)
            await msg.edit_text(fmt_email(text, results, elapsed), parse_mode=ParseMode.HTML)
            await update.message.reply_text(
                "Теперь введи <b>username</b> для проверки профилей:",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_cancel(),
            )
        else:
            msg = await update.message.reply_text("⏳ Ищу профили...")
            results, elapsed = await scan_username(text)
            await msg.edit_text(
                fmt_username(text, results, elapsed),
                parse_mode=ParseMode.HTML,
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
    if not (doc.file_name or "").endswith(".txt"):
        await update.message.reply_text("❌ Поддерживаются только .txt файлы.")
        return

    file = await doc.get_file()
    content = (await file.download_as_bytearray()).decode("utf-8", errors="ignore")
    lines = [l.strip() for l in content.splitlines() if l.strip()]

    if not lines:
        await update.message.reply_text("❌ Файл пуст.")
        return
    if len(lines) > 50:
        await update.message.reply_text("❌ Максимум 50 строк.")
        return

    await update.message.reply_text(f"⏳ Обрабатываю {len(lines)} строк…")

    for line in lines:
        if is_email(line):
            res, elapsed = await scan_email(line)
            out = fmt_email(line, res, elapsed)
        else:
            res, elapsed = await scan_username(line)
            out = fmt_username(line, res, elapsed)
        await update.message.reply_text(out, parse_mode=ParseMode.HTML)
        await asyncio.sleep(FILE_DELAY)

    context.user_data.clear()
    await update.message.reply_text("✅ Готово!", reply_markup=kb_main())


# ─── MAIN ─────────────────────────────────────────────────────────────────────

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает статус прокси и делает тест-запрос через него."""
    lines: list[str] = ["<b>🔧 Диагностика</b>\n"]

    # Прокси
    if PROXY_URL and _PROXY_NORMALIZED:
        # Маскируем пароль для показа (оставляем первые 2 символа пароля)
        masked = re.sub(r'(:)([^@:/]{1,2})[^@]*(@)', r'\1\2***\3', _PROXY_NORMALIZED)
        lines.append(f"🌐 <b>PROXY_URL:</b> <code>{h(masked)}</code>")
        if _PROXY_NORMALIZED != PROXY_URL:
            lines.append("   <i>✏️ URL нормализован (спецсимволы в пароле закодированы)</i>")
    else:
        lines.append("🌐 <b>PROXY_URL:</b> ❌ не задан")
        lines.append("   <i>Без прокси Facebook/Instagram/LinkedIn/CryptoRank блокируют датацентровые IP</i>")
        lines.append("")
        lines.append("<b>Как добавить:</b>")
        lines.append("GitHub → Settings → Secrets → Actions → New secret")
        lines.append("<code>Name:  PROXY_URL</code>")
        lines.append("<code>Value: socks5://ЛОГИН:ПАРОЛЬ@ХОСТ:ПОРТ</code>")

    # Тест подключения через прокси
    lines.append("")
    lines.append("<b>Тест соединения:</b>")
    msg = await update.message.reply_text(
        "\n".join(lines) + "\n⏳ проверяю...",
        parse_mode=ParseMode.HTML,
    )

    test_results: list[str] = []
    for test_url, label in [
        ("https://api.ipify.org?format=json", "IP через прокси"),
        ("https://www.facebook.com/favicon.ico", "Facebook"),
        ("https://www.instagram.com/favicon.ico", "Instagram"),
    ]:
        try:
            async with make_client(timeout=10.0) as c:
                r = await c.get(test_url)
            if "ipify" in test_url:
                ip = r.json().get("ip", "?")
                test_results.append(f"  ✅ {label}: <code>{h(ip)}</code>")
            else:
                test_results.append(f"  ✅ {label}: HTTP {r.status_code}")
        except Exception as e:
            test_results.append(f"  ❌ {label}: <code>{h(str(e)[:60])}</code>")

    lines += test_results
    lines.append("")
    if CAPTCHA_KEY:
        lines.append("🔑 <b>CAPTCHA_API_KEY:</b> ✅ задан")
    else:
        lines.append("🔑 <b>CAPTCHA_API_KEY:</b> не задан (опционально)")

    await msg.edit_text("\n".join(lines), parse_mode=ParseMode.HTML)


def main() -> None:
    if not BOT_TOKEN:
        raise ValueError(
            "Не задан BOT_TOKEN.\n"
            "Задай переменную окружения: export BOT_TOKEN='xxxxxxx:xxx...'"
        )

    proxy_status = f"ЗАДАН → {PROXY_URL[:30]}..." if PROXY_URL else "НЕ ЗАДАН (блокировки FB/LI/IG/CR)"
    print("─" * 60)
    print("  OSINT Telegram Bot")
    print(f"  PROXY_URL:  {proxy_status}")
    print(f"  CAPTCHA:    {'задан' if CAPTCHA_KEY else 'не задан'}")
    print(f"  DELAY:      {FILE_DELAY}с")
    print("─" * 60)

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    print("Бот запущен. Ожидаю сообщения...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
