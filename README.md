# OSINT Combo Bot

Единый OSINT-инструмент: **holehe** (email) + **user-scanner** (username) + **Telegram-бот**.

- 📧 **Email-режим** — проверяет email на 120+ сайтах через функцию восстановления пароля
- 👤 **Username-режим** — ищет никнейм на 100+ платформах
- 🤖 **Telegram-бот** — управление через чат

---

## Установка

```bash
git clone https://github.com/ВАШ_НИК/osint-combo-bot.git
cd osint-combo-bot
pip install -r requirements.txt
```

---

## Запуск Telegram-бота

### Локально

```bash
export BOT_TOKEN="токен_от_BotFather"
python bot.py
```

На Windows:
```cmd
set BOT_TOKEN=токен_от_BotFather
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
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable osint-bot
sudo systemctl start osint-bot
```

---

## GitHub Secrets (для GitHub Actions)

1. Открой репозиторий на GitHub
2. **Settings → Secrets and variables → Actions**
3. Нажми **New repository secret**
4. Имя: `BOT_TOKEN`, значение: токен от BotFather
5. Сохрани

Затем в GitHub Actions используй как:
```yaml
env:
  BOT_TOKEN: ${{ secrets.BOT_TOKEN }}
```

---

## Команды в Telegram

| Команда | Описание |
|---------|----------|
| `/start` | Начало работы |
| `/email адрес@mail.com` | Сканировать email |
| `/user johndoe` | Сканировать username |
| `/both адрес@mail.com` | Email + username сразу |
| Просто написать email | Автодетект → email-сканирование |
| Просто написать ник | Автодетект → username-сканирование |

---

## CLI (без Telegram)

```bash
python main.py target@example.com      # email
python main.py johndoe                 # username
python main.py target@example.com --both  # оба режима
python main.py johndoe --only-found    # только найденные
python main.py johndoe -c dev          # только категория dev
```

---

## Структура

```
osint-combo-bot/
├── bot.py           ← Telegram-бот
├── main.py          ← CLI
├── requirements.txt
├── holehe/          ← email OSINT движок
└── user_scanner/    ← username сканер
```

---

## Кредиты

- [holehe](https://github.com/megadose/holehe) — email OSINT
- [user-scanner](https://github.com/kaifcodec/user-scanner) — username scanner
