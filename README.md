# wolt-reg-bot

Telegram-бот, который **покупает виртуальный номер, ловит SMS и (для Wolt) поднимает одноразовую почту**. Сам в Wolt/Gmail не логинится и заказ не оформляет — форму заполняешь ты.

Живой инстанс автора: [@RinneganWoltBot](https://t.me/RinneganWoltBot). Этот репозиторий — исходники без секретов.

[![CI](https://github.com/r1nnegann/wolt-reg-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/r1nnegann/wolt-reg-bot/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![aiogram 3](https://img.shields.io/badge/aiogram-3.x-2AABEE)](https://docs.aiogram.dev/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## Зачем это

Регистрация в Wolt и Gmail просит почту и/или SMS. Вручную кликать HeroSMS + Mail.tm неудобно: номер сгорает, письмо приходит кривой ссылкой, код нужно успеть ввести.

Бот делает инфраструктуру:

| Сервис | Что делает бот | Что делаешь ты |
|---|---|---|
| **Wolt** | Mail.tm-ящик → ссылка из письма → номер KZ `+7` → SMS-код | вставляешь почту/ссылку/номер/код на wolt.com |
| **Gmail** | номер UK `+44` → SMS-код | вставляешь номер и код в форме Google |

Не путать с [wolt-easy](https://github.com/r1nnegann/wolt-easy) — тот скаут скидок, без аккаунтов.

---

## Возможности

- Живая цена HeroSMS перед покупкой: баланс, наличие, `maxPrice` с запасом `$0.005`
- Wolt: случайный ящик Mail.tm, разбор письма (https и `wolt-app://`), кнопка «скопировать ссылку» (лимит Telegram 256 символов — длинные ссылки уходят markdown-ссылкой)
- SMS: webhook HeroSMS (мгновенно) + опрос раз в 7 с как страховка; 10 минут ожидания, resend на 2-й минуте, авто-`setStatus=6`
- «Сменить номер» на лету: старый activation отменяется, почта Wolt остаётся
- Один живой экран в чате: старые кнопки удаляются, не копятся
- Белый список пользователей, свои HeroSMS-ключи или общий админский
- Админка: юзеры, ключи, статистика, история, логи; смена админского ключа без утечки в лог
- SQLite WAL, состояние переживает рестарт процесса (FSM в памяти — незакрытую сессию продолжаешь кнопкой)

---

## Как устроен поток

```
Telegram  ──polling──►  bot.py
                           │
                           ├─ Mail.tm REST     (Wolt: ящик, письма, ссылка)
                           ├─ HeroSMS legacy   (баланс, цена, номер, SMS)
                           └─ aiohttp :18091   (опциональный webhook)
                                    ▲
HTTPS /hooks/herosms/<token>  ──nginx──┘   только этот path, остальное 404
```

### Wolt

```
/start → Wolt
  → проверка баланса
  → Mail.tm inbox (рандомный local-part)
  → ждём письмо ≤ 5 мин, достаём ссылку
  → confirm: цена / maxPrice / сток
  → getNumberV2  service=rr  country=2  (Казахстан)
  → вводишь номер на wolt.com
  → код: webhook или polling → авто-complete
```

### Gmail

Тот же confirm и покупка, без почты: `service=go`, `country=16` (UK). Код `gm` в HeroSMS — это не Gmail.

Номер в чат приходит **локальный, без маски**: `77001234567` → `7001234567`, `4477…` → без `44`. Так просят формы.

---

## Стек

Один файл `bot.py` — так и задумано.

- Python 3.12, [aiogram](https://docs.aiogram.dev/) 3, httpx, python-dotenv, aiohttp
- SQLite (`users`, `activations`, `accounts`, `logs`, `ui_messages`)
- HeroSMS `https://hero-sms.com/stubs/handler_api.php`
- Mail.tm `https://api.mail.tm` (домен не хардкодится — `GET /domains`)

---

## Быстрый старт

Нужны: токен у [@BotFather](https://t.me/BotFather), ключ [HeroSMS](https://hero-sms.com), свой Telegram id (напиши `/start` боту или [@userinfobot](https://t.me/userinfobot)).

```bash
git clone https://github.com/r1nnegann/wolt-reg-bot.git
cd wolt-reg-bot
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

`.env`:

```env
BOT_TOKEN=123456:ABC...
ADMIN_ID=123456789
HERO_KEY=your_herosms_api_key
```

```bash
python bot.py
```

Первый `/start` от `ADMIN_ID` становится админом с доступом к общему ключу. Остальных пускает админка.

### Docker

```bash
cp .env.example .env   # заполни BOT_TOKEN, ADMIN_ID, HERO_KEY
docker compose up -d --build
docker compose logs -f
```

База: `./data/bot.db`. Webhook слушает `127.0.0.1:18091` на хосте. Образ не содержит `.env`.

---

## Конфигурация

| Переменная | Обязательно | По умолчанию | Смысл |
|---|---|---|---|
| `BOT_TOKEN` | да | — | токен BotFather |
| `ADMIN_ID` | да | — | числовой Telegram id владельца |
| `HERO_KEY` | да | — | ключ HeroSMS админа |
| `ADMIN_NAME` | нет | `Admin` | имя сида в SQLite |
| `HERO_WEBHOOK_TOKEN` | нет | пусто | секрет в URL webhook; пусто = только polling |
| `HERO_WEBHOOK_HOST` | нет | `127.0.0.1` | в Docker compose ставится `0.0.0.0` |
| `HERO_WEBHOOK_PORT` | нет | `18091` | порт listener |
| `SMS_BOT_ENV` | нет | `./.env` | путь к dotenv |
| `SMS_BOT_DB` | нет | `./data/bot.db` | путь к SQLite |

Секреты только в `.env` (gitignored) или в окружении. В код и в git — никогда.

---

## Telegram

| Кнопка / команда | Кто | Что |
|---|---|---|
| `/start` | все | меню |
| Начать регистрацию | белый список | Wolt или Gmail |
| Мои почты | белый список | ящики Mail.tm, повтор ссылки, удаление |
| Статус | белый список | текущая активация |
| Сменить номер | во время SMS | новый номер, почта та же |
| Админ-панель | `ADMIN_ID` | юзеры, ключи, стата, логи |

Новых людей бот сам заносит в `users` (`is_active`, без ключа) и пишет админу. Регистрировать они смогут после ключа или флага «админский HeroSMS».

---

## Webhook HeroSMS (необязательно)

Polling и так работает. Webhook просто быстрее.

1. Сгенерируй длинный токен (`python -c 'import secrets; print(secrets.token_urlsafe(32))'`).
2. Положи его в `HERO_WEBHOOK_TOKEN`.
3. На шлюзе открой **только** `POST /hooks/herosms/<token>` → `127.0.0.1:18091`. Шаблон: [`deploy/nginx-herosms.conf.example`](deploy/nginx-herosms.conf.example).
4. В кабинете HeroSMS укажи `https://YOUR_DOMAIN/hooks/herosms/<token>`.

Чужой token → `404`. Повтор той же активации → no-op. Listener без токена не стартует.

---

## systemd

Шаблон юнита: [`deploy/sms-bot.service`](deploy/sms-bot.service).

```bash
sudo useradd --system --home /opt/wolt-reg-bot --shell /usr/sbin/nologin woltreg
sudo rsync -a --exclude .venv --exclude data ./ /opt/wolt-reg-bot/
sudo -u woltreg python3.12 -m venv /opt/wolt-reg-bot/.venv
sudo -u woltreg /opt/wolt-reg-bot/.venv/bin/pip install -r requirements.txt
sudo install -m 600 -o woltreg -g woltreg /dev/null /opt/wolt-reg-bot/.env
# впиши секреты в .env
sudo cp deploy/sms-bot.service /etc/systemd/system/wolt-reg-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now wolt-reg-bot
```

Два процесса с одним `BOT_TOKEN` дадут Telegram `409 Conflict`.

---

## Тесты

Внешние API мокаются. Секреты не нужны.

```bash
PYTHONPATH=. python -m unittest discover -s tests -v
```

Покрытие: разбор писем Wolt, Mail.tm, SQLite-аккаунты, webhook (идемпотентность), смена номера, живой UI, запись админского ключа без утечки в лог.

CI гоняет то же на каждый push/PR. Деплоя из Actions нет — секреты VPS в GitHub не кладём.

---

## Структура

```
wolt-reg-bot/
├── bot.py                 весь бот
├── tests/test_mail_flow.py
├── requirements.txt
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── deploy/
│   ├── sms-bot.service
│   └── nginx-herosms.conf.example
└── data/                  SQLite, в git не попадает
```

---

## Безопасность

Подробности — в [SECURITY.md](SECURITY.md). Коротко:

- `.env` и `*.db` в `.gitignore`
- `httpx` не логирует URL (ключ HeroSMS иначе уедет в journald)
- админский ключ в чате маскируется, исходное сообщение удаляется
- webhook слушает loopback, path = секрет

---

## Ограничения и ответственность

- Это **не** официальный продукт Wolt, Google, Telegram, HeroSMS или Mail.tm.
- Бот не обходит капчу, antifraud и 3-D Secure. С DC-IP Wolt часто молча глотает письмо — нужен обычный/мобильный канал на стороне браузера, не бота.
- Авторегистрация пачками, фарм аккаунтов и любые действия против ToS сервисов — на тебе. Код для личного хелпера, не для фермы.
- Цены, коды стран (`rr`/`go`, `2`/`16`) — снимок HeroSMS; если каталог сменится, поправь `SERVICES` в `bot.py`.

---

## Лицензия

[MIT](LICENSE). Товарные знаки — их владельцев.
