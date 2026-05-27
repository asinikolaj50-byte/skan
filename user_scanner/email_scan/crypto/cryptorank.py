"""
CryptoRank — проверка email и username.

Email-метод: POST /api/v0/auth/reset-password
  Если ответ содержит "success" или "email sent" → аккаунт найден.
  Если "user not found" / "email not found" → не зарегистрирован.

Username-метод: GET /api/v0/user/info?username={username}
  200 + данные пользователя → найден.
  404 / "not found" в теле → не найден.
"""
import httpx
from user_scanner.core.result import Result

SHOW_URL = "https://cryptorank.io"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://cryptorank.io",
    "Referer": "https://cryptorank.io/",
}


async def check_email(email: str) -> Result:
    """
    Ищет аккаунт CryptoRank по email через forgot-password endpoint.

    Endpoint: POST https://cryptorank.io/api/v0/auth/reset-password
    Body (JSON): {"email": "<email>"}

    Возможные ответы:
      {"success": true}                       → аккаунт найден, письмо отправлено
      {"success": false, "message": "..."}    → разбираем message
        "User not found"                      → не зарегистрирован
        "Too many requests"                   → rate limit
    """
    async with httpx.AsyncClient(
        headers=_HEADERS, timeout=12.0, follow_redirects=True
    ) as client:
        try:
            r = await client.post(
                "https://cryptorank.io/api/v0/auth/reset-password",
                json={"email": email},
            )

            if r.status_code == 200:
                data = r.json()
                if data.get("success") is True:
                    return Result.taken(url=SHOW_URL)
                msg = (data.get("message") or "").lower()
                if "not found" in msg or "no user" in msg or "not exist" in msg:
                    return Result.available(url=SHOW_URL)
                if "too many" in msg or "rate" in msg:
                    return Result.error("Rate limit — попробуй позже")
                # success=false без явного "не найден" — скорее всего найден
                # (например заблокирован временно)
                return Result.error(f"Неопределённый ответ: {data.get('message', '')}")

            if r.status_code == 404:
                return Result.available(url=SHOW_URL)

            if r.status_code == 429:
                return Result.error("Rate limited (429)")

            if r.status_code == 422:
                # Unprocessable entity — обычно email не существует как аккаунт
                try:
                    data = r.json()
                    msg = str(data).lower()
                    if "not found" in msg or "not exist" in msg:
                        return Result.available(url=SHOW_URL)
                except Exception:
                    pass
                return Result.available(url=SHOW_URL)

            return Result.error(f"HTTP {r.status_code}")

        except httpx.TimeoutException:
            return Result.error("Таймаут соединения")
        except Exception as e:
            return Result.error(f"Ошибка: {e}")


async def check_username(username: str) -> Result:
    """
    Проверяет существование профиля CryptoRank по username.

    Метод 1 (API): GET https://cryptorank.io/api/v0/user/info?username={username}
      200 + {"data": {"username": ...}} → найден
      404 / {"error": ...}             → не найден

    Метод 2 (fallback, страница профиля):
      GET https://cryptorank.io/profile/{username}
      Ищем признаки реального профиля в HTML.
    """
    profile_url = f"https://cryptorank.io/profile/{username}"

    async with httpx.AsyncClient(
        headers=_HEADERS, timeout=12.0, follow_redirects=True
    ) as client:

        # --- Метод 1: JSON API ---
        try:
            r = await client.get(
                "https://cryptorank.io/api/v0/user/info",
                params={"username": username},
            )
            if r.status_code == 200:
                data = r.json()
                if (
                    data.get("data")
                    and data["data"].get("username", "").lower() == username.lower()
                ):
                    return Result.taken(url=profile_url)
                # Данные есть, но username другой — не найден
                return Result.available(url=SHOW_URL)
            if r.status_code == 404:
                return Result.available(url=SHOW_URL)
        except Exception:
            pass  # Переходим к fallback

        # --- Метод 2: Страница профиля ---
        try:
            r2 = await client.get(profile_url)
            if r2.status_code == 404:
                return Result.available(url=SHOW_URL)
            if r2.status_code == 200:
                text = r2.text.lower()
                # Ищем явные признаки существующего профиля
                if (
                    f'"username":"{username.lower()}"' in text
                    or f"cryptorank.io/profile/{username.lower()}" in text
                    or username.lower() in text[:5000]
                    and "not found" not in text[:2000]
                ):
                    # Проверяем, что страница не 404-редизайн
                    if "page not found" not in text and "404" not in text[:500]:
                        return Result.taken(url=profile_url)
                return Result.available(url=SHOW_URL)
        except httpx.TimeoutException:
            return Result.error("Таймаут соединения")
        except Exception as e:
            return Result.error(f"Ошибка: {e}")

    return Result.available(url=SHOW_URL)


async def validate_cryptorank_email(email: str) -> Result:
    return await check_email(email)


async def validate_cryptorank(username: str) -> Result:
    return await check_username(username)
