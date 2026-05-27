"""
OpenSea — проверка email и username.

Username-метод: GET https://api.opensea.io/api/v2/accounts/{username}
  200 → аккаунт найден (возвращает данные профиля + ссылку)
  400/404 → не найден

Email-метод:
  OpenSea не предоставляет публичный forgot-password endpoint.
  Используем endpoint регистрации/логина через Magic.link:
  POST https://opensea.io/api/auth/email/check
  {"email": "<email>"}
  
  Если аккаунт существует — OpenSea предлагает "sign in" (magic link).
  Если нет — предлагает создать новый аккаунт.
"""
import httpx
from user_scanner.core.result import Result

SHOW_URL = "https://opensea.io"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://opensea.io",
    "Referer": "https://opensea.io/",
    "x-app-id": "opensea-web",
    "x-build-id": "2024",
}

_API_HEADERS = {
    **_HEADERS,
    "X-API-KEY": "",  # Публичный API OpenSea v2 работает без ключа для базовых запросов
}


async def check_username(username: str) -> Result:
    """
    Проверяет существование профиля OpenSea по username.

    Endpoint: GET https://api.opensea.io/api/v2/accounts/{username}
    Документация: https://docs.opensea.io/reference/get_account

    Ответ 200:
      {
        "address": "0x...",
        "username": "...",
        "profile_image_url": "...",
        ...
      }
    Ответ 400/404: аккаунт не найден или username не существует.
    """
    profile_url = f"https://opensea.io/{username}"

    async with httpx.AsyncClient(
        headers=_API_HEADERS,
        timeout=12.0,
        follow_redirects=True,
    ) as client:
        try:
            r = await client.get(
                f"https://api.opensea.io/api/v2/accounts/{username}",
            )

            if r.status_code == 200:
                data = r.json()
                # Убеждаемся что это именно нужный username, не адрес
                found_username = data.get("username", "")
                if found_username.lower() == username.lower() or data.get("address"):
                    return Result.taken(url=profile_url)
                return Result.available(url=SHOW_URL)

            if r.status_code in (400, 404):
                return Result.available(url=SHOW_URL)

            if r.status_code == 429:
                return Result.error("Rate limited (429) — попробуй позже")

            # Fallback: проверяем страницу профиля
            r2 = await client.get(profile_url)
            if r2.status_code == 404:
                return Result.available(url=SHOW_URL)
            if r2.status_code == 200:
                text = r2.text.lower()
                # OpenSea возвращает 200 даже для несуществующих профилей — проверяем контент
                if (
                    "page not found" in text
                    or "this account does not exist" in text
                    or "no items to display" in text[:3000]
                    and username.lower() not in text[:5000]
                ):
                    return Result.available(url=SHOW_URL)
                # Если username фигурирует в мета-тегах → найден
                if f'"username":"{username.lower()}"' in text or f'/{username.lower()}"' in text[:5000]:
                    return Result.taken(url=profile_url)
                return Result.error("Неопределённый ответ — проверь вручную")

            return Result.error(f"HTTP {r.status_code}")

        except httpx.TimeoutException:
            return Result.error("Таймаут соединения")
        except Exception as e:
            return Result.error(f"Ошибка: {e}")


async def check_email(email: str) -> Result:
    """
    Проверяет, зарегистрирован ли email на OpenSea.

    OpenSea использует Magic.link для email-аутентификации.
    Мы отправляем запрос на инициацию magic link и смотрим на ответ:
      - "new_user": false  → аккаунт существует
      - "new_user": true   → новый пользователь (не зарегистрирован)

    Endpoint: POST https://opensea.io/api/auth/magic_link/request
    Body (JSON): {"destination": "<email>", "type": "EMAIL"}

    Примечание: Если endpoint изменился — возвращает HTTP 404.
    В этом случае используем fallback через страницу логина.
    """
    async with httpx.AsyncClient(
        headers=_HEADERS,
        timeout=12.0,
        follow_redirects=True,
    ) as client:
        # --- Метод 1: Magic.link check ---
        try:
            r = await client.post(
                "https://opensea.io/api/auth/magic_link/request",
                json={"destination": email, "type": "EMAIL", "dry_run": True},
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("new_user") is False:
                    return Result.taken(url=SHOW_URL)
                if data.get("new_user") is True:
                    return Result.available(url=SHOW_URL)
        except Exception:
            pass

        # --- Метод 2: Проверка через login endpoint ---
        try:
            r2 = await client.post(
                "https://opensea.io/api/auth/login",
                json={"email": email, "method": "email"},
            )
            if r2.status_code == 200:
                data = r2.json()
                if data.get("exists") is True or data.get("account_exists") is True:
                    return Result.taken(url=SHOW_URL)
                if data.get("exists") is False:
                    return Result.available(url=SHOW_URL)

            if r2.status_code == 404:
                # Аккаунт не найден — endpoint существует, но email не знает
                return Result.available(url=SHOW_URL)
        except Exception:
            pass

        # --- Метод 3: OpenSea /account/email_exists ---
        try:
            r3 = await client.get(
                "https://opensea.io/api/account/email_exists",
                params={"email": email},
            )
            if r3.status_code == 200:
                data = r3.json()
                if data.get("exists") is True:
                    return Result.taken(url=SHOW_URL)
                if data.get("exists") is False:
                    return Result.available(url=SHOW_URL)
        except Exception:
            pass

        return Result.error(
            "OpenSea не раскрывает email-lookup публично — "
            "проверь username вручную на opensea.io"
        )


async def validate_opensea(username: str) -> Result:
    return await check_username(username)


async def validate_opensea_email(email: str) -> Result:
    return await check_email(email)
