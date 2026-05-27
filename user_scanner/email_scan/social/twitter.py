import httpx
from user_scanner.core.result import Result

SHOW_URL = "https://x.com"

async def _check(email: str) -> Result:
    """
    Проверяет, зарегистрирован ли email на Twitter/X.

    Метод: GET https://api.twitter.com/i/users/email_available.json?email=...
    Ответ: {"valid": false, "taken": true, "reason": "taken"}  -> аккаунт существует
           {"valid": true,  "taken": false}                    -> не зарегистрирован
    Точность: высокая (официальный endpoint регистрации).
    """
    try:
        async with httpx.AsyncClient(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
                "Referer": "https://x.com/",
                "x-twitter-active-user": "yes",
                "x-twitter-client-language": "en",
            },
            timeout=10.0,
            follow_redirects=True,
        ) as client:
            r = await client.get(
                "https://api.twitter.com/i/users/email_available.json",
                params={"email": email},
            )

        if r.status_code == 200:
            data = r.json()
            if data.get("taken") is True:
                return Result.taken(url=SHOW_URL)
            if data.get("valid") is True and data.get("taken") is False:
                return Result.available(url=SHOW_URL)
            if data.get("reason") == "taken":
                return Result.taken(url=SHOW_URL)
            return Result.available(url=SHOW_URL)

        if r.status_code == 429:
            return Result.error("Rate limited (429) — попробуй позже")

        return Result.error(f"HTTP {r.status_code}")

    except httpx.TimeoutException:
        return Result.error("Таймаут соединения")
    except Exception as e:
        return Result.error(f"Ошибка: {e}")


async def validate_twitter(email: str) -> Result:
    return await _check(email)
