import re
import httpx
from user_scanner.core.result import Result

SHOW_URL = "https://linkedin.com"

_HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}


async def _check(email: str) -> Result:
    """
    Проверяет, зарегистрирован ли email на LinkedIn.

    Метод: Forgot-password flow.
      1. GET /login  — получить CSRF-токен (поле "loginCsrfParam").
      2. POST /checkpoint/lg/login-submit с email + неверным паролем.
         - LinkedIn отвечает "There were too many incorrect password attempts"
           или "Wrong email or password" ТОЛЬКО если email зарегистрирован.
         - Если email не найден, LinkedIn отвечает "Hmm, we don't recognize that email."
           (код ошибки UNKNOWN_EMAIL_ADDRESS или текст "don't recognize").

    Точность: высокая — LinkedIn чётко разделяет два случая.
    """
    async with httpx.AsyncClient(
        headers=_HEADERS_BASE,
        http2=True,
        timeout=15.0,
        follow_redirects=True,
    ) as client:
        try:
            # 1. Получаем страницу логина и csrf-токен
            r1 = await client.get("https://www.linkedin.com/login")
            if r1.status_code != 200:
                return Result.error(f"Login page HTTP {r1.status_code}")

            csrf_match = re.search(
                r'name="loginCsrfParam"\s+value="([^"]+)"', r1.text
            )
            if not csrf_match:
                # Альтернативный формат
                csrf_match = re.search(r'"loginCsrfParam"\s*:\s*"([^"]+)"', r1.text)
            if not csrf_match:
                return Result.error("Не удалось извлечь CSRF-токен LinkedIn")

            csrf = csrf_match.group(1)

            # 2. Отправляем форму логина с заведомо неверным паролем
            payload = {
                "session_key": email,
                "session_password": "Wr0ng_Pa55w0rd_Pr0be_!2024",
                "loginCsrfParam": csrf,
                "isJsEnabled": "false",
            }
            headers_post = {
                **_HEADERS_BASE,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://www.linkedin.com/login",
                "Origin": "https://www.linkedin.com",
            }
            r2 = await client.post(
                "https://www.linkedin.com/checkpoint/lg/login-submit",
                data=payload,
                headers=headers_post,
            )

            body = r2.text.lower()

            # Случай 1: email НЕ найден
            # LinkedIn говорит "hmm, we don't recognize that email"
            # или "UNKNOWN_EMAIL_ADDRESS" в JSON-ответе
            not_found_signals = [
                "don't recognize",
                "doesn't recognize",
                "unknown_email_address",
                "no account found",
                "that email address",
                "we couldn't find",
            ]
            for sig in not_found_signals:
                if sig in body:
                    return Result.available(url=SHOW_URL)

            # Случай 2: email НАЙДЕН — неверный пароль, но email известен
            found_signals = [
                "wrong password",
                "incorrect password",
                "too many incorrect",
                "password is wrong",
                "enter your password",
                "checkpoint/lg/login-submit",  # редирект на ввод пароля
                "check-page",
                "challenge",
            ]
            for sig in found_signals:
                if sig in body:
                    return Result.taken(url=SHOW_URL)

            # Случай 3: редирект на страницу ввода пароля = email найден
            final_url = str(r2.url).lower()
            if "add-password" in final_url or "checkpoint" in final_url:
                return Result.taken(url=SHOW_URL)

            # Не удалось однозначно определить
            return Result.error(
                "Неопределённый ответ LinkedIn — возможно блокировка"
            )

        except httpx.TimeoutException:
            return Result.error("Таймаут соединения")
        except Exception as e:
            return Result.error(f"Ошибка: {e}")


async def validate_linkedin(email: str) -> Result:
    return await _check(email)
