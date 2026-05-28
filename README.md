# OSINT Combo Bot

Единый OSINT-инструмент: **holehe** (email) + **user-scanner** (username) + **Telegram-бот**.

- 📧 **Email-режим** — проверяет email на 120+ сайтах через forgot-password flow
- 👤 **Username-режим** — ищет никнейм на 100+ платформах с кликабельными ссылками
- 🤖 **Telegram-бот** — управление через inline-кнопки
- 🔀 **Ротация User-Agent** — 13 реальных браузерных UA на каждый запрос
- 🌐 **Поддержка прокси** — HTTP/SOCKS5, одна переменная окружения
- ⏱ **Jitter-задержки** — случайные паузы между запросами для обхода rate-limit

---

## Установка

```bash
git clone https://github.com/ВАШ_НИК/osint-combo-bot.git
cd osint-combo-bot
pip install -r requirements.txt
```

---

## Запуск Telegram-бота

### Локально (домашний интернет — прокси не нужен)

```bash
export BOT_TOKEN="токен_от_BotFather"
python bot.py
```

На Windows:
```cmd
set BOT_TOKEN=токен_от_BotFather
python bot.py
```

### С прокси (если запускаешь на VPS/сервере в датацентре)

Facebook, LinkedIn и CryptoRank блокируют датацентровые IP-адреса.
Решение — прокси с домашними/мобильными IP:

```bash
export BOT_TOKEN="токен_от_BotFather"
export PROXY_URL="http://user:pass@proxy-host:8080"
# или SOCKS5:
export PROXY_URL="socks5://user:pass@proxy-host:1080"
python bot.py
```

### На сервере (systemd)

Создай файл `/etc/systemd/system/osint-bot.service`:

```ini
[Unit]
Description=OSINT Combo Bot
After=network.target

[Service]
WorkingDirectory=/path/to/osint-combo-bot
ExecStart=/usr/bin/python3 bot.py
Environment=BOT_TOKEN=ВАШ_ТОКЕН
Environment=PROXY_URL=http://user:pass@host:port
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable osint-bot
sudo systemctl start osint-bot
sudo journalctl -u osint-bot -f   # логи
```

---

## Переменные окружения

| Переменная | Обязательно | Описание |
|---|---|---|
| `BOT_TOKEN` | ✅ | Токен от @BotFather |
| `PROXY_URL` | Для VPS | `http://user:pass@host:port` или `socks5://...` |
| `CAPTCHA_API_KEY` | Нет | Ключ [2captcha.com](https://2captcha.com) для авторешения капч |
| `REQUEST_DELAY` | Нет | Задержка между строками в .txt-файле (по умолчанию `2.0` сек) |

---

## Настройка GitHub Actions (запуск прямо с GitHub)

1. Открой репозиторий → **Settings → Secrets and variables → Actions**
2. Добавь секреты:

| Secret | Значение |
|---|---|
| `BOT_TOKEN` | Токен от @BotFather |
| `PROXY_URL` | Прокси (если нужен) |
| `CAPTCHA_API_KEY` | Ключ 2captcha (если нужен) |

3. Запуск: **Actions → Run OSINT Telegram Bot → Run workflow**

> ⚠️ GitHub Actions использует IP датацентра Microsoft Azure.
> Facebook, LinkedIn, CryptoRank будут выдавать ошибки без `PROXY_URL`.

---

## Команды в Telegram

| Действие | Описание |
|---------|----------|
| `/start` | Открывает главное меню с кнопками |
| Кнопка «📧 Email → аккаунты» | Проверить email на платформах |
| Кнопка «👤 Username → сети» | Найти профили по никнейму |
| Кнопка «🔀 Email + Username» | Оба режима последовательно |
| Кнопка «📄 Список .txt» | Загрузить файл со списком |

Проверяемые платформы (email): Twitter/X, Facebook, Instagram, LinkedIn, Discord, CryptoRank

Проверяемые платформы (username): Twitter/X, LinkedIn, Facebook, CryptoRank, OpenSea

---

## CLI (без Telegram)

```bash
python main.py target@example.com         # email
python main.py johndoe                    # username
python main.py target@example.com --both  # оба режима
python main.py johndoe --only-found       # только найденные
python main.py johndoe -c dev             # только категория dev
```

---

## Структура

```
osint-combo-bot/
├── bot.py                 ← Telegram-бот (исправленный)
├── main.py                ← CLI
├── requirements.txt
├── .github/workflows/     ← GitHub Actions
├── holehe/                ← email OSINT движок (120+ платформ)
└── user_scanner/          ← username сканер (100+ платформ)
```

---

## Почему ошибки на VPS без прокси

Сайты определяют тип IP-адреса. Датацентровые IP (AWS, Azure, Hetzner, DigitalOcean и т.д.) находятся в чёрных списках у Facebook, LinkedIn и CryptoRank — они возвращают 403/401 даже при правильных запросах.

Решение: прокси с **домашними** или **мобильными** IP. Рекомендуемые сервисы:
- [WebShare](https://www.webshare.io/) — дешёвые ротируемые прокси
- [Bright Data](https://brightdata.com/) — мобильные прокси (самые надёжные)
- [ProxyEmpire](https://proxyempire.io/) — ISP-прокси

---

## Кредиты

- [holehe](https://github.com/megadose/holehe) — email OSINT
- [user-scanner](https://github.com/kaifcodec/user-scanner) — username scanner
