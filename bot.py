#!/usr/bin/env python3
"""SMS Bot v2.6 — Wolt Mail.tm + SMS webhook (KZ), Gmail SMS-only (UK), live price confirm."""
import asyncio, html as html_lib, json, os, re, secrets, string, tempfile, time, sqlite3, datetime, traceback, logging, httpx
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware
from aiogram.types import (Message, CallbackQuery, CopyTextButton,
                            InlineKeyboardButton, InlineKeyboardMarkup, BotCommand)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger("sms_bot")

# HeroSMS legacy authentication uses api_key in the query string.  Do not let
# httpx/httpcore log request URLs, otherwise the API key ends up in journald.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.environ.get("SMS_BOT_ENV", os.path.join(BASE_DIR, ".env"))
load_dotenv(ENV_PATH)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
HERO_KEY = os.environ.get("HERO_KEY", "").strip()
MIN_HERO_KEY_LEN = 20
HANDLER_URL = "https://hero-sms.com/stubs/handler_api.php"
MAIL_BASE_URL = "https://api.mail.tm"
HERO_WEBHOOK_TOKEN = os.environ.get("HERO_WEBHOOK_TOKEN", "").strip()
HERO_WEBHOOK_PORT = int(os.environ.get("HERO_WEBHOOK_PORT", "18091") or "18091")
HERO_WEBHOOK_HOST = os.environ.get("HERO_WEBHOOK_HOST", "127.0.0.1").strip() or "127.0.0.1"
try:
    ADMIN_ID = int(os.environ.get("ADMIN_ID", "0") or "0")
except ValueError:
    ADMIN_ID = 0
ADMIN_NAME = os.environ.get("ADMIN_NAME", "Admin").strip() or "Admin"
DB_PATH = os.environ.get("SMS_BOT_DB", os.path.join(BASE_DIR, "data", "bot.db"))

COUNTRY_KZ = 2
COUNTRY_UK = 16
# Default = Wolt. Gmail keeps its own country in SERVICES.
COUNTRY = COUNTRY_KZ
PRICE_MARGIN = 0.005
MAX_BUY_ATTEMPTS = 5

COUNTRY_INFO = {
    COUNTRY_KZ: {"code": "KZ", "name": "Казахстан", "dial": "+7", "prefix": "7"},
    COUNTRY_UK: {"code": "UK", "name": "Великобритания", "dial": "+44", "prefix": "44"},
}

# Service codes from HeroSMS getServicesList:
# rr=Wolt, go=Google/youtube/Gmail (НЕ gm — gm это Mocospace)
SERVICES = {
    "wolt": {"code": "rr", "label": "Wolt",   "country": COUNTRY_KZ, "emoji": "📱"},
    "gmail": {"code": "go", "label": "Gmail",  "country": COUNTRY_UK, "emoji": "📧"},
}


def country_info(country_id):
    try:
        cid = int(country_id)
    except (TypeError, ValueError):
        return {"code": "??", "name": "неизвестно", "dial": "", "prefix": ""}
    return COUNTRY_INFO.get(cid, {
        "code": str(cid),
        "name": f"страна {cid}",
        "dial": "",
        "prefix": "",
    })


def country_label(country_id) -> str:
    info = country_info(country_id)
    if info["dial"]:
        return f"{info['name']} ({info['dial']})"
    return info["name"]


def phone_local(phone, country=None) -> str:
    """Local number without country prefix. NEVER mask."""
    raw = str(phone or "").strip()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return raw
    prefixes = []
    prefix = country_info(country).get("prefix") if country is not None else ""
    if prefix:
        prefixes.append(prefix)
    for extra in ("44", "7"):
        if extra not in prefixes:
            prefixes.append(extra)
    for prefix in prefixes:
        if not digits.startswith(prefix) or len(digits) <= len(prefix):
            continue
        rest = digits[len(prefix):]
        if prefix == "7" and len(digits) != 11:
            continue
        if prefix == "44" and len(rest) < 9:
            continue
        return rest
    return digits


def service_country(service_key: str, default=None) -> int:
    svc = SERVICES.get(service_key) or {}
    if "country" in svc:
        return int(svc["country"])
    return COUNTRY if default is None else default

HERO_READY = 1; HERO_RESEND = 3; HERO_COMPLETE = 6; HERO_CANCEL = 8
SMS_WAIT_SECONDS = 600        # total wait for SMS
SMS_RESEND_AFTER = 120        # trigger resend after 2 min
SMS_CANCEL_AFTER = 600        # cancel if no SMS by end (was incorrectly 300)
MAIL_WAIT_SECONDS = 300       # wait for Wolt email before offering retry
MAIL_POLL_SECONDS = 5         # stays well below Mail.tm's documented quota
MAIL_HTTP_TIMEOUT = 30

def _row_get(row, key, default=None):
    """Safe get for sqlite3.Row / dict / None. Row has no .get() in Py3.11."""
    if row is None:
        return default
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _is_admin(row) -> bool:
    return bool(_row_get(row, "is_admin", 0) == 1)


def _uses_admin_hero(row) -> bool:
    return bool(_row_get(row, "use_admin_hero", 0))


def _mail_html_to_text(value) -> str:
    """Normalize Mail.tm html/text fields, which can be str or list[str]."""
    if isinstance(value, list):
        return " ".join(str(item) for item in value if item is not None)
    return value if isinstance(value, str) else ""


def _mail_sender(message: dict) -> str:
    """Mail.tm may return `from` as a dict, string, or null."""
    raw = message.get("from", "") if isinstance(message, dict) else ""
    if isinstance(raw, dict):
        return str(raw.get("address") or raw.get("name") or "")
    return raw if isinstance(raw, str) else ""


def mail_extract_link(message: dict) -> str | None:
    """Extract the newest Wolt http(s) or wolt-app link from a Mail.tm message."""
    if not isinstance(message, dict):
        return None

    html_text = _mail_html_to_text(message.get("html"))
    text = " ".join(
        part for part in (
            _mail_sender(message),
            str(message.get("subject") or ""),
            str(message.get("intro") or ""),
            _mail_html_to_text(message.get("text")),
            html_text,
        ) if part
    )
    text = html_lib.unescape(text)

    # Prefer href values: HTML often wraps a long tracking URL across markup.
    candidates = re.findall(r"href\s*=\s*[\"']([^\"']+)", html_text, flags=re.IGNORECASE)
    candidates.extend(re.findall(r"(?:https?://|wolt-app://)[^\s<>\"']+", text, flags=re.IGNORECASE))
    for candidate in candidates:
        link = html_lib.unescape(candidate).strip().rstrip(".,;:!?)]}>\"'")
        if "wolt" in link.lower() and link.lower().startswith(("http://", "https://", "wolt-app://")):
            return link
    return None


# ═══════════════════════════════════════════════════════════
#  DB
# ═══════════════════════════════════════════════════════════

def db_init():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            first_name TEXT,
            added_by INTEGER,
            is_admin INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            hero_key TEXT,
            use_admin_hero INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS activations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            activation_id INTEGER,
            phone TEXT,
            service TEXT,
            country INTEGER,
            cost REAL,
            sms_code TEXT,
            status TEXT DEFAULT 'started',
            created_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            action TEXT,
            details TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            email TEXT NOT NULL,
            password TEXT NOT NULL,
            mail_tm_id TEXT,
            mail_token TEXT,
            email_link TEXT,
            mail_message_id TEXT,
            phone TEXT,
            activation_id INTEGER,
            sms_code TEXT,
            service TEXT NOT NULL DEFAULT 'rr',
            country INTEGER NOT NULL DEFAULT 16,
            cost REAL,
            status TEXT NOT NULL DEFAULT 'email_waiting',
            created_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_user_email
            ON accounts(user_id, email);
        CREATE TABLE IF NOT EXISTS ui_messages (
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            PRIMARY KEY (chat_id, message_id)
        );
        """)
        c = conn.cursor()
        if ADMIN_ID:
            c.execute("SELECT id FROM users WHERE telegram_id = ?", (ADMIN_ID,))
            if not c.fetchone():
                c.execute(
                    "INSERT INTO users (telegram_id, first_name, added_by, is_admin, use_admin_hero) VALUES (?, ?, ?, 1, 1)",
                    (ADMIN_ID, ADMIN_NAME, ADMIN_ID),
                )
            c.execute(
                "UPDATE users SET use_admin_hero = 1 WHERE telegram_id = ? AND use_admin_hero = 0",
                (ADMIN_ID,),
            )
        conn.commit()
    finally:
        conn.close()


def db_log(tid, action, details=""):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT INTO logs (telegram_id, action, details) VALUES (?, ?, ?)",
            (tid, action, details),
        ); conn.commit()
    except Exception as e:
        log.error(f"db_log failed: {e}")
    finally:
        conn.close()


def db_error_log(tid, action, error, tb=""):
    details = f"ERROR: {error}"
    if tb: details += f" | TRACEBACK: {tb[:500]}"
    db_log(tid, action, details)


def db_get_user(tid):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT * FROM users WHERE telegram_id = ? AND is_active = 1",
            (tid,),
        ).fetchone()
    finally:
        conn.close()


def db_get_all_users():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT telegram_id, username, first_name, is_admin, created_at FROM users WHERE is_active = 1 ORDER BY created_at"
        ).fetchall()
    finally:
        conn.close()


def db_add_activation(user_id, activation_id, phone, cost, service, country):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        c = conn.execute(
            "INSERT INTO activations (user_id, activation_id, phone, service, country, cost, status) VALUES (?, ?, ?, ?, ?, ?, 'started')",
            (user_id, activation_id, phone, service, country, cost),
        )
        row_id = c.lastrowid; conn.commit()
        return row_id
    finally:
        conn.close()


_ALLOWED_COLUMNS = {"sms_code", "status", "completed_at", "cost", "phone"}

def db_update_activation(activation_id, **kwargs):
    for k in kwargs:
        if k not in _ALLOWED_COLUMNS:
            raise ValueError(f"Column '{k}' not in allowlist")
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [activation_id]
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(f"UPDATE activations SET {sets} WHERE activation_id = ?", vals)
        conn.commit()
    finally:
        conn.close()


def db_get_activation_for_webhook(activation_id):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT a.*, u.telegram_id, u.is_admin "
            "FROM activations a JOIN users u ON u.id = a.user_id "
            "WHERE a.activation_id = ? ORDER BY a.id DESC LIMIT 1",
            (activation_id,),
        ).fetchone()
    finally:
        conn.close()


def db_claim_activation_sms(activation_id, code):
    """Atomically claim a webhook code; duplicate deliveries become no-ops."""
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "UPDATE activations SET sms_code = ?, status = 'webhook_processing' "
            "WHERE activation_id = ? "
            "AND (sms_code IS NULL OR sms_code = '') "
            "AND status NOT IN ('completed', 'cancelled', 'webhook_processing')",
            (str(code), activation_id),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def db_release_webhook_sms(activation_id):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "UPDATE activations SET sms_code = '', status = 'started' "
            "WHERE activation_id = ? AND status = 'webhook_processing'",
            (activation_id,),
        )
        conn.commit()
    finally:
        conn.close()


def db_finish_webhook_sms(activation_id):
    completed_at = datetime.datetime.now().isoformat()
    db_update_activation(
        activation_id,
        status="completed",
        completed_at=completed_at,
    )
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "UPDATE accounts SET sms_code = (SELECT sms_code FROM activations WHERE activation_id = ?), "
            "status = 'completed', completed_at = ? WHERE activation_id = ?",
            (activation_id, completed_at, activation_id),
        )
        conn.commit()
    finally:
        conn.close()


def db_get_recent_activations(limit=10):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT phone, service, country, cost, sms_code, status, created_at, completed_at FROM activations ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()


def db_get_all_users_enriched():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT u.id, u.telegram_id, u.username, u.first_name, u.is_admin, u.created_at, "
            "COUNT(a.id) as activation_count, MAX(a.completed_at) as last_activity "
            "FROM users u LEFT JOIN activations a ON a.user_id = u.id "
            "WHERE u.is_active = 1 GROUP BY u.id "
            "ORDER BY u.is_admin DESC, u.created_at ASC"
        ).fetchall()
    finally:
        conn.close()


def db_get_user_by_id(user_db_id):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT * FROM users WHERE id = ? AND is_active = 1", (user_db_id,)
        ).fetchone()
    finally:
        conn.close()


def db_get_user_activations(user_db_id, limit=10, offset=0):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT * FROM activations WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
            (user_db_id, limit, offset),
        ).fetchall()
    finally:
        conn.close()


def db_get_activations_enriched(limit=5, offset=0):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT a.*, u.telegram_id, u.first_name, u.username "
            "FROM activations a JOIN users u ON u.id = a.user_id "
            "ORDER BY a.id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    finally:
        conn.close()


def db_get_activations_count():
    conn = sqlite3.connect(DB_PATH)
    try:
        return conn.execute("SELECT COUNT(*) FROM activations").fetchone()[0]
    finally:
        conn.close()


def db_get_stats():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        return {
            "total_users": conn.execute(
                "SELECT COUNT(*) FROM users WHERE is_active = 1"
            ).fetchone()[0],
            "total_activations": conn.execute(
                "SELECT COUNT(*) FROM activations"
            ).fetchone()[0],
            "successful": conn.execute(
                "SELECT COUNT(*) FROM activations WHERE status = 'completed'"
            ).fetchone()[0],
        }
    finally:
        conn.close()


_ACCOUNT_ALLOWED_COLUMNS = {
    "email", "password", "mail_tm_id", "mail_token", "email_link",
    "mail_message_id", "phone", "activation_id", "sms_code", "service",
    "country", "cost", "status", "completed_at",
}


def db_add_account(user_id, email, password, mail_tm_id, mail_token,
                   service="rr", country=COUNTRY, cost=None, status="email_waiting"):
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "INSERT INTO accounts "
            "(user_id, email, password, mail_tm_id, mail_token, service, country, cost, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, email, password, mail_tm_id, mail_token, service, country, cost, status),
        )
        account_id = cur.lastrowid
        conn.commit()
        return account_id
    finally:
        conn.close()


def db_get_account(account_id, user_id=None):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        if user_id is None:
            query = "SELECT * FROM accounts WHERE id = ?"
            args = (account_id,)
        else:
            query = "SELECT * FROM accounts WHERE id = ? AND user_id = ?"
            args = (account_id, user_id)
        return conn.execute(query, args).fetchone()
    finally:
        conn.close()


def db_get_latest_account(user_id, statuses=None):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            return conn.execute(
                f"SELECT * FROM accounts WHERE user_id = ? AND status IN ({placeholders}) "
                "ORDER BY id DESC LIMIT 1",
                (user_id, *statuses),
            ).fetchone()
        return conn.execute(
            "SELECT * FROM accounts WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)
        ).fetchone()
    finally:
        conn.close()


def db_update_account(account_id, **kwargs):
    if not kwargs:
        return
    for key in kwargs:
        if key not in _ACCOUNT_ALLOWED_COLUMNS:
            raise ValueError(f"Column '{key}' not in accounts allowlist")
    sets = ", ".join(f"{key} = ?" for key in kwargs)
    values = list(kwargs.values()) + [account_id]
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(f"UPDATE accounts SET {sets} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()


def db_get_user_accounts(user_id, limit=50):
    """All Mail.tm inboxes of a user, newest first."""
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT * FROM accounts WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    finally:
        conn.close()


def db_delete_account(account_id, user_id=None):
    conn = sqlite3.connect(DB_PATH)
    try:
        if user_id is None:
            cur = conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        else:
            cur = conn.execute(
                "DELETE FROM accounts WHERE id = ? AND user_id = ?",
                (account_id, user_id),
            )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def db_ui_list(chat_id) -> list[int]:
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT message_id FROM ui_messages WHERE chat_id = ?",
            (int(chat_id),),
        ).fetchall()
        return [int(row[0]) for row in rows]
    except sqlite3.Error as exc:
        log.warning("db_ui_list failed: %s", exc)
        return []
    finally:
        conn.close()


def db_ui_add(chat_id, message_id):
    if chat_id is None or message_id is None:
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO ui_messages (chat_id, message_id) VALUES (?, ?)",
            (int(chat_id), int(message_id)),
        )
        conn.commit()
    except sqlite3.Error as exc:
        log.warning("db_ui_add failed: %s", exc)
    finally:
        conn.close()


def db_ui_set(chat_id, message_ids):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("DELETE FROM ui_messages WHERE chat_id = ?", (int(chat_id),))
        conn.executemany(
            "INSERT OR IGNORE INTO ui_messages (chat_id, message_id) VALUES (?, ?)",
            [(int(chat_id), int(mid)) for mid in message_ids if mid is not None],
        )
        conn.commit()
    except sqlite3.Error as exc:
        log.warning("db_ui_set failed: %s", exc)
    finally:
        conn.close()


class MailTMError(ValueError):
    pass


class MailTMAuthError(MailTMError):
    pass


def _mail_json(response, operation):
    try:
        data = response.json()
    except ValueError as exc:
        raise MailTMError(f"Mail.tm {operation}: non-JSON response") from exc
    if response.status_code >= 400:
        if isinstance(data, dict):
            detail = data.get("detail") or data.get("message") or data.get("title") or str(data)
        else:
            detail = str(data)
        error_type = MailTMAuthError if response.status_code == 401 else MailTMError
        raise error_type(f"Mail.tm {operation}: HTTP {response.status_code}: {detail}")
    return data


def mail_get_domains():
    response = httpx.get(
        f"{MAIL_BASE_URL}/domains",
        params={"page": 1},
        timeout=MAIL_HTTP_TIMEOUT,
    )
    data = _mail_json(response, "domains")
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        raise MailTMError("Mail.tm domains: unexpected response shape")
    return data.get("hydra:member") or data.get("member") or []


def mail_login(email, password):
    response = httpx.post(
        f"{MAIL_BASE_URL}/token",
        json={"address": email, "password": password},
        timeout=MAIL_HTTP_TIMEOUT,
    )
    data = _mail_json(response, "token")
    token = data.get("token")
    if not token:
        raise MailTMError("Mail.tm token: response has no token")
    return token


def mail_create():
    """Create a fully random Mail.tm inbox and authenticate it."""
    domains = [
        item for item in mail_get_domains()
        if item.get("domain") and item.get("isActive", True) and not item.get("isPrivate", False)
    ]
    if not domains:
        raise MailTMError("Mail.tm: no active public domains")
    domain = secrets.choice(domains)["domain"]
    alphabet = string.ascii_lowercase + string.digits
    local_part = "".join(secrets.choice(alphabet) for _ in range(14))
    password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(24))
    address = f"{local_part}@{domain}"

    response = httpx.post(
        f"{MAIL_BASE_URL}/accounts",
        json={"address": address, "password": password},
        timeout=MAIL_HTTP_TIMEOUT,
    )
    account = _mail_json(response, "accounts")
    account_id = account.get("id")
    if not account_id:
        raise MailTMError("Mail.tm accounts: response has no id")
    token = mail_login(address, password)
    return {
        "id": account_id,
        "address": address,
        "password": password,
        "token": token,
    }


def mail_fetch(token):
    response = httpx.get(
        f"{MAIL_BASE_URL}/messages",
        headers={"Authorization": f"Bearer {token}"},
        params={"page": 1},
        timeout=MAIL_HTTP_TIMEOUT,
    )
    data = _mail_json(response, "messages")
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        raise MailTMError("Mail.tm messages: unexpected response shape")
    return data.get("hydra:member") or data.get("member") or []


def mail_get_message(token, message_id):
    response = httpx.get(
        f"{MAIL_BASE_URL}/messages/{message_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=MAIL_HTTP_TIMEOUT,
    )
    return _mail_json(response, "message")


def mail_delete(account_id, token):
    response = httpx.delete(
        f"{MAIL_BASE_URL}/accounts/{account_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=MAIL_HTTP_TIMEOUT,
    )
    if response.status_code >= 400:
        _mail_json(response, "delete account")


# ═══════════════════════════════════════════════════════════
#  HeroSMS
# ═══════════════════════════════════════════════════════════

def _hero(action, api_key=None, **params):
    p = {"api_key": api_key or HERO_KEY, "action": action, **params}
    response = httpx.get(HANDLER_URL, params=p, timeout=30)
    raw = response.text.strip()

    # The endpoint returns JSON errors such as
    # {"title":"BAD_KEY","details":"Unauthorized"}.  Previously that
    # payload was passed to hero_get_price(), which reported the misleading
    # "Service ... not found" message for both services.
    payload = None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        pass

    title = ""
    details = ""
    if isinstance(payload, dict):
        title = str(payload.get("title") or payload.get("error") or "").strip()
        details = payload.get("details") or payload.get("message") or ""
        if not isinstance(details, str):
            details = json.dumps(details, ensure_ascii=False)

    auth_titles = {"BAD_KEY", "BAD_API_KEY", "UNAUTHORIZED", "UNAUTHENTICATED"}
    if response.status_code >= 400 or title.upper() in auth_titles:
        label = title or f"HTTP_{response.status_code}"
        reason = details or raw[:200] or response.reason_phrase
        raise ValueError(f"HeroSMS API error {label}: {reason}")

    return raw


def _hero_json(action, api_key=None, **params):
    raw = _hero(action, api_key, **params)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"HeroSMS non-JSON for {action}: {raw[:200]}") from e


def _hero_balance(api_key=None):
    raw = _hero("getBalance", api_key=api_key)
    if raw.startswith("ACCESS_BALANCE:"):
        return float(raw.split(":", 1)[1])
    raise ValueError(f"Unexpected balance: {raw}")


def mask_hero_key(key: str) -> str:
    raw = (key or "").strip()
    if len(raw) < 4:
        return "••••"
    return f"••••{raw[-4:]}"


def _env_quote(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:+-]+", value or ""):
        return value
    return json.dumps(value, ensure_ascii=False)


def write_env_value(path: str, name: str, value: str) -> None:
    """Replace or append NAME=value in a dotenv file. Preserves other lines."""
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", name):
        raise ValueError("invalid env name")
    try:
        with open(path, encoding="utf-8") as handle:
            original = handle.read()
    except FileNotFoundError:
        original = ""
    lines = original.splitlines(True)
    pattern = re.compile(rf"^(export\s+)?{re.escape(name)}=")
    replaced = False
    out = []
    quoted = _env_quote(value)
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#") or not pattern.match(stripped):
            out.append(line)
            continue
        leading = line[: len(line) - len(stripped)]
        prefix = "export " if stripped.startswith("export ") else ""
        out.append(f"{leading}{prefix}{name}={quoted}\n")
        replaced = True
    if not replaced:
        if out and not str(out[-1]).endswith("\n"):
            out.append("\n")
        out.append(f"{name}={quoted}\n")
    new_text = "".join(out)
    if not new_text.endswith("\n"):
        new_text += "\n"
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(new_text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def persist_admin_hero_key(new_key: str) -> str:
    """Save the shared admin HeroSMS key to .env and process memory. Returns mask."""
    global HERO_KEY
    key = (new_key or "").strip()
    if len(key) < MIN_HERO_KEY_LEN:
        raise ValueError("ключ слишком короткий")
    write_env_value(ENV_PATH, "HERO_KEY", key)
    HERO_KEY = key
    os.environ["HERO_KEY"] = key
    return mask_hero_key(key)


def hero_get_price(service_code, country=COUNTRY, api_key=None):
    """Get price for a service from HeroSMS. Returns (cost, count) or raises."""
    data = _hero_json("getPrices", api_key=api_key, country=country)
    if not isinstance(data, dict):
        raise ValueError(f"HeroSMS getPrices returned unexpected payload: {data!r}")
    sc = str(country)
    if sc not in data or not isinstance(data[sc], dict) or service_code not in data[sc]:
        raise ValueError(f"Service '{service_code}' not found for country {country}")
    info = data[sc][service_code]
    return float(info["cost"]), int(info.get("count", 0) or 0)


def hero_buy_number(service_code, country=COUNTRY, max_price=None, api_key=None):
    """Buy number via getNumberV2. Returns activation dict or raises with clear message."""
    params = {"service": service_code, "country": country}
    if max_price is not None:
        params["maxPrice"] = f"{float(max_price):.4f}"
    data = _hero_json("getNumberV2", api_key=api_key, **params)
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected getNumberV2 response: {data!r}")
    # Error payloads: {"title":"NO_NUMBERS","details":"..."} or plain-ish errors
    if "activationId" not in data or "phoneNumber" not in data:
        title = data.get("title") or data.get("error") or "BUY_FAILED"
        details = data.get("details") or data.get("message") or json.dumps(data, ensure_ascii=False)[:200]
        raise ValueError(f"{title}: {details}")
    return data


def parse_hero_webhook(payload):
    """Return (activation_id, code) for current and legacy HeroSMS payloads."""
    if not isinstance(payload, dict):
        return None, None
    nested = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    raw_id = (
        payload.get("activationId")
        or payload.get("activation_id")
        or payload.get("activationID")
        or nested.get("activationId")
        or nested.get("activation_id")
    )
    try:
        activation_id = int(raw_id)
    except (TypeError, ValueError):
        activation_id = None

    raw_code = (
        payload.get("code")
        or payload.get("smsCode")
        or payload.get("sms_code")
        or nested.get("code")
        or nested.get("smsCode")
        or nested.get("sms_code")
    )
    if isinstance(raw_code, list):
        raw_code = raw_code[0] if raw_code else None
    if isinstance(raw_code, dict):
        raw_code = raw_code.get("code") or raw_code.get("text")
    code = str(raw_code).strip() if raw_code is not None else ""
    if not code:
        text = str(payload.get("text") or payload.get("smsText") or nested.get("text") or "")
        match = re.search(r"(?<!\d)\d{4,8}(?!\d)", text)
        code = match.group(0) if match else ""
    if not activation_id or not code or len(code) > 64:
        return None, None
    return activation_id, code


async def _cancel_hero_activation(activation_id, api_key=None):
    """Cancel a HeroSMS number so the user is not charged. Best-effort."""
    if not activation_id:
        return
    try:
        await asyncio.to_thread(
            _hero, "setStatus", api_key=api_key, id=activation_id, status=HERO_CANCEL,
        )
    except Exception as exc:
        log.warning("HeroSMS cancel failed for %s: %s", activation_id, exc)
    try:
        db_update_activation(
            activation_id,
            status="cancelled",
            completed_at=datetime.datetime.now().isoformat(),
        )
    except Exception:
        pass


async def _purchase_number(service_code, country, api_key=None) -> dict:
    """Live price + balance + getNumberV2 with retries + setStatus READY.

    Returns the HeroSMS activation dict. Raises ValueError on failure.
    Cancels the number if READY fails after a successful buy.
    """
    current_balance = await asyncio.to_thread(_hero_balance, api_key)
    live_cost, live_avail = await asyncio.to_thread(hero_get_price, service_code, country, api_key)
    max_price = round(live_cost + PRICE_MARGIN, 4)
    if current_balance < max_price:
        raise ValueError(
            f"Недостаточно средств: баланс ${current_balance:.2f}, нужно ${max_price:.4f}"
        )
    if live_avail <= 0:
        raise ValueError("Сейчас нет доступных номеров")

    activation = None
    last_error = None
    for attempt in range(MAX_BUY_ATTEMPTS):
        try:
            activation = await asyncio.to_thread(
                hero_buy_number, service_code, country, max_price, api_key,
            )
            break
        except Exception as exc:
            last_error = exc
            log.warning("getNumberV2 attempt %s maxPrice=%s: %s", attempt + 1, max_price, exc)
            try:
                live_cost, live_avail = await asyncio.to_thread(
                    hero_get_price, service_code, country, api_key,
                )
                max_price = round(live_cost + PRICE_MARGIN * (attempt + 2), 4)
            except Exception as price_error:
                log.warning("price refresh on retry failed: %s", price_error)
            if attempt < MAX_BUY_ATTEMPTS - 1:
                await asyncio.sleep(2 + attempt)

    if not activation:
        raise ValueError(str(last_error or "NO_NUMBERS"))

    aid = activation["activationId"]
    try:
        ready_raw = await asyncio.to_thread(
            _hero, "setStatus", api_key=api_key, id=aid, status=HERO_READY,
        )
        if ready_raw and not str(ready_raw).startswith("ACCESS_") and "OK" not in str(ready_raw).upper() and str(ready_raw) != "1":
            log.info("setStatus READY response: %s", ready_raw)
    except Exception as exc:
        log.warning("setStatus READY failed: %s", exc)
        try:
            await asyncio.to_thread(
                _hero, "setStatus", api_key=api_key, id=aid, status=HERO_CANCEL,
            )
        except Exception:
            pass
        raise ValueError("HeroSMS не активировал номер") from exc

    if "activationCost" not in activation:
        activation["activationCost"] = live_cost
    return activation


def get_user_hero_key(user_row):
    if _uses_admin_hero(user_row):
        return None  # None → use HERO_KEY (admin)
    return _row_get(user_row, "hero_key")


def sms_check(activation_id, api_key=None):
    """Returns (received: bool, code_or_status: str)."""
    data = _hero_json("getStatusV2", api_key=api_key, id=activation_id)
    if not isinstance(data, dict):
        return False, str(data)
    sms = data.get("sms") or {}
    if isinstance(sms, dict) and sms.get("code"):
        return True, str(sms["code"])
    codes = data.get("smsCode") or []
    if isinstance(codes, dict):
        codes = [codes.get("code", "")]
    if isinstance(codes, list) and codes:
        first = codes[0]
        if isinstance(first, dict):
            first = first.get("code") or first.get("text") or ""
        if first:
            return True, str(first)
    raw = _hero("getStatus", api_key=api_key, id=activation_id)
    if raw.startswith("STATUS_OK:"):
        return True, raw.split(":", 1)[1]
    return False, raw


# ═══════════════════════════════════════════════════════════
#  FSM
# ═══════════════════════════════════════════════════════════

class RegState(StatesGroup):
    waiting_hero_key = State()
    confirming = State()
    waiting_wolt_email = State()
    waiting_sms = State()
    completed = State()


class AdminState(StatesGroup):
    add_user_wait = State()
    rm_user_wait = State()
    waiting_admin_hero_key = State()


# ═══════════════════════════════════════════════════════════
#  Keyboards
# ═══════════════════════════════════════════════════════════

router = Router()
_bot: Bot | None = None
_active_states: dict[int, FSMContext] = {}
_chat_locks: dict[int, asyncio.Lock] = {}


def _chat_lock(chat_id: int) -> asyncio.Lock:
    lock = _chat_locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        _chat_locks[chat_id] = lock
    return lock


def _forget_active_state(chat_id, state=None):
    if state is None or _active_states.get(chat_id) is state:
        _active_states.pop(chat_id, None)


class UiCleanupMiddleware(BaseMiddleware):
    """On every button click: delete other tracked bot messages, keep the clicked one live."""

    async def __call__(self, handler, event, data):
        message = getattr(event, "message", None)
        if message is not None and getattr(message, "chat", None) is not None:
            try:
                chat_id = message.chat.id
                mid = message.message_id
                await _purge_ui(chat_id, keep=mid)
                db_ui_add(chat_id, mid)
            except Exception as exc:
                log.warning("ui purge on callback failed: %s", exc)
        return await handler(event, data)


router.callback_query.middleware(UiCleanupMiddleware())


def _link_button_rows(link: str | None):
    """Telegram URL buttons only accept http(s). Copy works for any short link."""
    if not link:
        return []
    rows = []
    if link.lower().startswith(("http://", "https://")):
        rows.append([InlineKeyboardButton(text="🔗 Открыть ссылку Wolt", url=link)])
    if 0 < len(link) <= 256:
        rows.append([InlineKeyboardButton(
            text="📋 Скопировать ссылку",
            copy_text=CopyTextButton(text=link),
        )])
    return rows


def menu_kb(current_state=None, is_admin=False, pending_account_id=None, extra_rows=None):
    kb = []
    if extra_rows:
        kb.extend(extra_rows)
    if is_admin:
        kb.append([InlineKeyboardButton(text="👑 Админ-панель", callback_data="admin_panel")])
    # normalize State object / "Group:name" string
    st = current_state.state if hasattr(current_state, "state") else current_state
    if st is None:
        kb.append([InlineKeyboardButton(text="📍 Начать регистрацию", callback_data="choose_service")])
    elif st in (RegState.waiting_hero_key, RegState.waiting_hero_key.state, "RegState:waiting_hero_key"):
        kb.append([InlineKeyboardButton(text="🔄 Проверить доступ", callback_data="check_key")])
    elif st in (RegState.confirming, RegState.confirming.state, "RegState:confirming"):
        kb.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    elif st in (RegState.waiting_wolt_email, RegState.waiting_wolt_email.state, "RegState:waiting_wolt_email"):
        kb.append([InlineKeyboardButton(text="❌ Отменить", callback_data="cancel")])
    elif st in (RegState.waiting_sms, RegState.waiting_sms.state, "RegState:waiting_sms"):
        kb.append([InlineKeyboardButton(text="🔄 Сменить номер", callback_data="change_number")])
        kb.append([InlineKeyboardButton(text="❌ Отменить", callback_data="cancel")])
    elif st in (RegState.completed, RegState.completed.state, "RegState:completed"):
        kb.append([InlineKeyboardButton(text="✅ Завершить", callback_data="complete")])
        kb.append([InlineKeyboardButton(text="❌ Отменить", callback_data="cancel")])
    else:
        # unknown/stale state → allow start
        kb.append([InlineKeyboardButton(text="📍 Начать регистрацию", callback_data="choose_service")])
    if pending_account_id and st is None:
        kb.append([InlineKeyboardButton(
            text="▶️ Продолжить Wolt-регистрацию",
            callback_data=f"resume_wolt:{pending_account_id}",
        )])
    kb.append([InlineKeyboardButton(text="📬 Мои почты", callback_data="mail_list")])
    kb.append([InlineKeyboardButton(text="📊 Статус", callback_data="status")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def _number_ready_text(*, swapped: bool, phone_short: str, cost: float,
                       email: str | None = None, link: str | None = None,
                       service_emoji: str = "", service_label: str = "") -> str:
    title = "🔄 <b>Новый номер готов</b>" if swapped else f"{service_emoji} <b>Номер готов</b>"
    lines = [title, ""]
    if service_label:
        lines.append(f"Сервис: {service_emoji} {service_label}")
    if email:
        lines.append(f"📧 <code>{html_lib.escape(email)}</code>")
    lines.append(f"📱 <code>{html_lib.escape(phone_short)}</code>")
    lines.append(f"Цена: ${float(cost):.4f}")
    if link:
        safe_link = html_lib.escape(link, quote=True)
        lines.append("")
        lines.append(f'<a href="{safe_link}">🔗 Открыть ссылку Wolt</a>')
    lines.append("")
    if swapped:
        lines.append("Старый номер отменён. Введи <b>новый</b> в сервисе — жду SMS.")
    else:
        lines.append("Введи номер в сервисе. Если не подходит — «Сменить номер».")
    return "\n".join(lines)


def _session_markup(current_state, is_admin=False, link=None, pending_account_id=None):
    return menu_kb(
        current_state,
        is_admin=is_admin,
        pending_account_id=pending_account_id,
        extra_rows=_link_button_rows(link),
    )


def service_choice_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Wolt (почта + SMS, KZ +7)", callback_data="start_wolt")],
        [InlineKeyboardButton(text="📧 Gmail (+44)", callback_data="start_gmail")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_menu")],
    ])


def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users:0")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="➕ Добавить пользователя", callback_data="admin_add_user")],
        [InlineKeyboardButton(text="➖ Удалить пользователя", callback_data="admin_rm_user")],
        [InlineKeyboardButton(text="📜 История активаций", callback_data="admin_history:0")],
        [InlineKeyboardButton(text="📋 Логи", callback_data="admin_logs:0")],
        [InlineKeyboardButton(text="🔑 Управление ключами", callback_data="admin_keys")],
        [InlineKeyboardButton(text="✏️ Сменить админский ключ", callback_data="admin_change_hero_key")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")],
    ])


# ═══════════════════════════════════════════════════════════
#  /start
# ═══════════════════════════════════════════════════════════

@router.message(Command("start"))
async def cmd_start(msg: Message, state: FSMContext):
    user = db_get_user(msg.from_user.id)
    if user is None:
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO users (telegram_id, username, first_name, added_by) VALUES (?, ?, ?, ?)",
                (msg.from_user.id, msg.from_user.username or "", msg.from_user.first_name or "", ADMIN_ID),
            )
            conn.commit()
        finally:
            conn.close()
        await _bot.send_message(
            ADMIN_ID,
            f"🆕 Новый пользователь:\nID: {msg.from_user.id}\nName: {msg.from_user.first_name}\nUsername: @{msg.from_user.username or 'без username'}",
        )
        user = db_get_user(msg.from_user.id)

    is_admin = _is_admin(user)

    current = await state.get_state()
    if current and str(current).startswith("AdminState:"):
        await state.clear()
        current = None

    hk = get_user_hero_key(user)
    # None hero_key is OK only when use_admin_hero=1 (shared admin key)
    if hk is None and not _uses_admin_hero(user):
        await state.set_state(RegState.waiting_hero_key)
        await msg.answer(
            "<b>🔑 Нужен HeroSMS API ключ</b>\n\n"
            "Пришли свой ключ от hero-sms.com.\n"
            "Или попроси админа включить использование его ключа.\n\n"
            "<i>Отправь ключ текстом:</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Проверить снова", callback_data="check_key")],
            ]),
        )
        return

    current = await state.get_state()
    pending = db_get_latest_account(
        user["id"],
        statuses=("email_waiting", "email_timeout", "email_error", "number_error", "sms_waiting"),
    ) if current is None else None
    # pass raw state string — State.__eq__ accepts it; RegState(str) is invalid
    await _replace_ui(
        msg.chat.id,
        "<b>🤖 SMS Bot</b>\n\n"
        "Wolt: Казахстан (+7). Gmail: UK (+44).\n"
        "Wolt или Gmail — на выбор.\n\n"
        "Перед покупкой покажу <b>актуальную цену</b> и баланс.",
        reply_markup=menu_kb(
            current,
            is_admin=is_admin,
            pending_account_id=pending["id"] if pending else None,
        ),
    )


# ═══════════════════════════════════════════════════════════
#  HeroSMS key input
# ═══════════════════════════════════════════════════════════

@router.message(RegState.waiting_hero_key)
async def hero_key_input(msg: Message, state: FSMContext):
    raw = msg.text.strip()
    if len(raw) < 20:
        await msg.answer("❌ Слишком короткий ключ. HeroSMS ключ обычно 32+ символов.")
        return
    await msg.answer("⏳ Проверяю ключ...")
    try:
        bal = _hero_balance(api_key=raw)
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute(
                "UPDATE users SET hero_key = ? WHERE telegram_id = ?",
                (raw, msg.from_user.id),
            )
            conn.commit()
        finally:
            conn.close()
        await state.clear()
        user = db_get_user(msg.from_user.id)
        is_admin = user["is_admin"] == 1 if user else False
        await msg.answer(
            f"✅ Ключ работает! Баланс: ${bal:.2f}\n\nТеперь можно получать номера.",
            reply_markup=menu_kb(None, is_admin=is_admin),
        )
        db_log(msg.from_user.id, "set_hero_key", f"balance=${bal:.2f}")
    except Exception as e:
        await msg.answer(
            f"❌ Ключ не работает: {e}\n\nПопробуй другой ключ или попроси админа.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Проверить снова", callback_data="check_key")],
            ]),
        )


@router.callback_query(F.data == "check_key")
async def cb_check_key(cb: CallbackQuery, state: FSMContext):
    try:
        user = db_get_user(cb.from_user.id)
        if not user:
            await cb.answer("Пользователь не найден", show_alert=True); return
        if get_user_hero_key(user) or _uses_admin_hero(user):
            await state.clear()
            await cb.message.edit_text(
                "✅ Доступ открыт!",
                reply_markup=menu_kb(None, is_admin=_is_admin(user)),
            )
        else:
            await cb.answer("Ключ всё ещё нужен. Отправь его текстом.", show_alert=True)
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


# ═══════════════════════════════════════════════════════════
#  Registration flow — выбор сервиса
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "choose_service")
async def cb_choose_service(cb: CallbackQuery, state: FSMContext):
    try:
        user_row = db_get_user(cb.from_user.id)
        if not user_row:
            await cb.answer("Доступ запрещён", show_alert=True); return
        current = await state.get_state()
        if current is not None and current != RegState.confirming.state:
            await cb.answer("Уже есть активная сессия", show_alert=True); return

        hk = get_user_hero_key(user_row)
        if hk is None and not _uses_admin_hero(user_row):
            await cb.message.edit_text(
                "🔑 Нужен HeroSMS API ключ.",
                reply_markup=menu_kb(None, is_admin=user_row["is_admin"] == 1),
            )
            await state.set_state(RegState.waiting_hero_key)
            await cb.answer(); return

        await cb.message.edit_text(
            "📍 <b>Выбери сервис</b>\n\n"
            "Wolt — номер KZ (+7), Gmail — UK (+44):",
            reply_markup=service_choice_kb(),
        )
        await state.set_state(RegState.confirming)
        await cb.answer()
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


# ═══════════════════════════════════════════════════════════
#  Выбор конкретного сервиса → подтверждение
# ═══════════════════════════════════════════════════════════

def _wolt_retry_kb(account_id, action="email"):
    if action == "email":
        text = "🔄 Проверить почту ещё раз"
        callback = f"retry_wolt_email:{account_id}"
    else:
        text = "📱 Повторить покупку номера"
        callback = f"retry_wolt_number:{account_id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=callback)],
        [InlineKeyboardButton(text="❌ Закрыть Wolt-сессию", callback_data=f"cancel_wolt:{account_id}")],
        [InlineKeyboardButton(text="🔙 В меню", callback_data="back_menu")],
    ])


async def _load_wolt_state(state, account, user_row):
    """Restore persisted Wolt state after a retry or process restart."""
    await state.update_data(
        service_key="wolt",
        service_code="rr",
        service_label="Wolt",
        service_emoji="📱",
        hero_key=get_user_hero_key(user_row),
        db_user_id=user_row["id"],
        account_id=account["id"],
        email=account["email"],
        password=account["password"],
        mail_tm_id=account["mail_tm_id"],
        mail_token=account["mail_token"],
        email_link=account["email_link"],
        cost=float(account["cost"] or 0),
        country=int(account["country"] or SERVICES["wolt"]["country"]),
        phone=account["phone"],
        activation_id=account["activation_id"],
        actual_cost=float(account["cost"] or 0),
    )


async def _send_wolt_link(chat_id, email, link, back_cb=None):
    """Send a link without exposing it as ugly raw text where Telegram permits.
    back_cb: if set, the trailing button returns to that view (e.g. mail_list)
    instead of cancelling the FSM session. Replaces the live UI message."""
    safe_email = html_lib.escape(email or "")
    safe_link = html_lib.escape(link, quote=True)
    text = (
        "📨 <b>Письмо Wolt получено</b>\n\n"
        f"📧 <code>{safe_email}</code>\n\n"
        f"<a href=\"{safe_link}\">🔗 Открыть ссылку Wolt</a>"
    )
    back_label = "🔙 К списку почт" if back_cb else "❌ Отменить"
    back_data = back_cb or "cancel"
    rows = _link_button_rows(link)
    rows.append([InlineKeyboardButton(text=back_label, callback_data=back_data)])
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    await _replace_ui(chat_id, text, reply_markup=markup)


async def _start_sms_watch(chat_id, state):
    watch_id = secrets.token_hex(8)
    await state.update_data(sms_watch_id=watch_id)
    asyncio.create_task(_wait_sms(chat_id, state))


async def _wolt_buy_number(chat_id, state):
    """Buy the Wolt number only after the email link has arrived."""
    data = await state.get_data()
    account_id = data.get("account_id")
    if not account_id or not data.get("db_user_id"):
        await state.clear()
        await _safe_send(chat_id, "⚠️ Данные Wolt-сессии потеряны. Начни заново.", reply_markup=menu_kb(None))
        return

    user_row = db_get_user(chat_id)
    is_admin = _is_admin(user_row)
    hk = data.get("hero_key")
    country = int(SERVICES["wolt"]["country"])
    link = data.get("email_link")
    email = data.get("email")
    buying_text = "⏳ Ссылка получена. Проверяю цену и покупаю номер Wolt KZ..."
    if email:
        buying_text += f"\n\n📧 <code>{html_lib.escape(email)}</code>"
    if link:
        buying_text += f'\n<a href="{html_lib.escape(link, quote=True)}">🔗 Открыть ссылку Wolt</a>'
    await _safe_send(
        chat_id,
        buying_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=(
            _link_button_rows(link)
            + [[InlineKeyboardButton(text="❌ Отменить", callback_data="cancel")]]
        )),
    )
    try:
        activation = await _purchase_number("rr", country, hk)
    except Exception as exc:
        db_update_account(account_id, status="number_error", cost=data.get("cost"))
        await state.clear()
        await _safe_send(
            chat_id,
            f"❌ Не удалось купить номер Wolt: {html_lib.escape(str(exc))}",
            reply_markup=_wolt_retry_kb(account_id, "number"),
        )
        return

    aid = activation["activationId"]
    phone = str(activation["phoneNumber"])
    phone_short = phone_local(phone, country)
    actual_cost = float(activation.get("activationCost") or data.get("cost") or 0)
    db_add_activation(data["db_user_id"], aid, phone, actual_cost, "rr", country)
    db_update_account(
        account_id,
        phone=phone,
        activation_id=aid,
        cost=actual_cost,
        status="sms_waiting",
        country=country,
    )
    await state.update_data(
        phone=phone, activation_id=aid, actual_cost=actual_cost, cost=actual_cost, country=country,
    )
    await state.set_state(RegState.waiting_sms)
    await _safe_send(
        chat_id,
        _number_ready_text(
            swapped=False,
            phone_short=phone_short,
            cost=actual_cost,
            email=email,
            link=link,
            service_emoji="📱",
            service_label="Wolt",
        ),
        reply_markup=_session_markup(RegState.waiting_sms, is_admin=is_admin, link=link),
    )
    await _start_sms_watch(chat_id, state)


async def _show_confirm(cb: CallbackQuery, state: FSMContext, service_key: str):
    """Show price + balance and ask for confirmation."""
    try:
        user_row = db_get_user(cb.from_user.id)
        if not user_row: return
        svc = SERVICES[service_key]
        hk = get_user_hero_key(user_row)

        await cb.message.edit_text("⏳ Проверяю цены...")
        await cb.answer()

        country = int(svc["country"])
        cost, avail = hero_get_price(svc["code"], country, api_key=hk)
        balance = _hero_balance(api_key=hk)
        max_price = cost + PRICE_MARGIN

        if balance < max_price:
            await cb.message.edit_text(
                f"❌ <b>Недостаточно средств</b>\n\n"
                f"Сервис: {svc['emoji']} {svc['label']}\n"
                f"Цена: ${cost:.4f}\n"
                f"Баланс: ${balance:.2f}\n"
                f"Нужно: ${max_price:.4f}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад к выбору", callback_data="choose_service")],
                ]),
            ); return

        await state.update_data(
            service_key=service_key,
            service_code=svc["code"],
            service_label=svc["label"],
            service_emoji=svc["emoji"],
            country=country,
            hero_key=hk,
            cost=cost,
            max_price=max_price,
            balance=balance,
            avail=avail,
            db_user_id=user_row["id"],
        )

        await cb.message.edit_text(
            f"<b>📋 Подтверждение</b>\n\n"
            f"Сервис: {svc['emoji']} {svc['label']}\n"
            f"Страна: {country_label(country)}\n"
            f"Цена сейчас: <b>${cost:.4f}</b>\n"
            f"Лимит покупки: <b>${max_price:.4f}</b> (+margin)\n"
            f"Баланс: <b>${balance:.2f}</b>\n"
            f"Доступно номеров: {avail:,}\n\n"
            f"<i>Перед покупкой цена обновится ещё раз. После ✅ — номер и ожидание SMS.</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"✅ Купить за ~${cost:.4f}", callback_data="confirm_buy")],
                [InlineKeyboardButton(text="🔄 Обновить цену", callback_data=f"start_{service_key}")],
                [InlineKeyboardButton(text="🔙 Назад к выбору", callback_data="choose_service")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
            ]),
        )
    except Exception as e:
        db_error_log(cb.from_user.id, "confirm_price", str(e), traceback.format_exc())
        await cb.message.edit_text(
            f"❌ Ошибка проверки цен: {e}",
            reply_markup=menu_kb(None, is_admin=_is_admin(user_row)),
        )


async def _wait_wolt_email(chat_id, state):
    """Wait for the Wolt email, then start the HeroSMS part of the flow."""
    started = time.time()
    try:
        while time.time() - started < MAIL_WAIT_SECONDS:
            current = await state.get_state()
            if current != RegState.waiting_wolt_email.state:
                return
            data = await state.get_data()
            token = data.get("mail_token")
            email = data.get("email")
            password = data.get("password")
            account_id = data.get("account_id")
            if not token or not email or not password or not account_id:
                return

            try:
                messages = await asyncio.to_thread(mail_fetch, token)
            except MailTMAuthError:
                try:
                    token = await asyncio.to_thread(mail_login, email, password)
                    await state.update_data(mail_token=token)
                    db_update_account(account_id, mail_token=token)
                except Exception as exc:
                    log.warning("Mail.tm relogin failed for account %s: %s", account_id, exc)
                await asyncio.sleep(MAIL_POLL_SECONDS)
                continue
            except Exception as exc:
                log.warning("Mail.tm fetch failed for account %s: %s", account_id, exc)
                await asyncio.sleep(MAIL_POLL_SECONDS)
                continue

            messages = sorted(
                messages,
                key=lambda item: item.get("createdAt", ""),
                reverse=True,
            )
            for summary in messages:
                meta = " ".join([
                    _mail_sender(summary),
                    str(summary.get("subject") or ""),
                    str(summary.get("intro") or ""),
                ]).lower()
                if "wolt" not in meta or not summary.get("id"):
                    continue
                try:
                    full = await asyncio.to_thread(mail_get_message, token, summary["id"])
                    link = mail_extract_link(full)
                except Exception as exc:
                    log.warning("Mail.tm message parse failed for account %s: %s", account_id, exc)
                    continue
                if not link:
                    continue

                db_update_account(
                    account_id,
                    email_link=link,
                    mail_message_id=str(summary["id"]),
                    status="email_received",
                )
                await state.update_data(email_link=link)
                await _send_wolt_link(chat_id, email, link)
                await _wolt_buy_number(chat_id, state)
                return

            await asyncio.sleep(MAIL_POLL_SECONDS)

        data = await state.get_data()
        account_id = data.get("account_id")
        if account_id:
            db_update_account(account_id, status="email_timeout")
        await state.clear()
        await _safe_send(
            chat_id,
            "⚠️ Письмо Wolt не пришло за 5 минут. Почтовый ящик сохранён — можно проверить ещё раз.",
            reply_markup=_wolt_retry_kb(account_id, "email") if account_id else menu_kb(None),
        )
    except Exception as exc:
        log.error("_wait_wolt_email crashed: %s", exc, exc_info=True)
        try:
            data = await state.get_data()
            account_id = data.get("account_id")
            if account_id:
                db_update_account(account_id, status="email_error")
        except Exception:
            account_id = None
        await state.clear()
        await _safe_send(
            chat_id,
            "⚠️ Ошибка при проверке Mail.tm. Почтовый ящик сохранён.",
            reply_markup=_wolt_retry_kb(account_id, "email") if account_id else menu_kb(None),
        )


async def _begin_wolt_registration(cb: CallbackQuery, state: FSMContext):
    current = await state.get_state()
    if current != RegState.confirming.state:
        await cb.answer("Сначала выбери Wolt в меню регистрации", show_alert=True)
        return
    user_row = db_get_user(cb.from_user.id)
    if not user_row:
        await cb.answer("Доступ запрещён", show_alert=True)
        return
    hk = get_user_hero_key(user_row)
    is_admin = _is_admin(user_row)
    inbox = None
    try:
        await cb.message.edit_text("⏳ Проверяю цену Wolt и создаю почтовый ящик...")
        await cb.answer()
        cost, avail = await asyncio.to_thread(hero_get_price, "rr", SERVICES["wolt"]["country"], hk)
        balance = await asyncio.to_thread(_hero_balance, hk)
        max_price = round(cost + PRICE_MARGIN, 4)
        if balance < max_price:
            await cb.message.edit_text(
                f"❌ Недостаточно средств для Wolt.\nБаланс: ${balance:.2f}\nНужно: ${max_price:.4f}",
                reply_markup=menu_kb(None, is_admin=is_admin),
            )
            await state.clear()
            return
        if avail <= 0:
            await cb.message.edit_text(
                "❌ Для Wolt KZ сейчас нет доступных номеров.",
                reply_markup=menu_kb(None, is_admin=is_admin),
            )
            await state.clear()
            return

        inbox = await asyncio.to_thread(mail_create)
        account_id = db_add_account(
            user_id=user_row["id"],
            email=inbox["address"],
            password=inbox["password"],
            mail_tm_id=inbox["id"],
            mail_token=inbox["token"],
            service="rr",
            country=SERVICES["wolt"]["country"],
            cost=cost,
        )
        await state.update_data(
            service_key="wolt", service_code="rr", service_label="Wolt", service_emoji="📱",
            country=SERVICES["wolt"]["country"],
            hero_key=hk, cost=cost, max_price=max_price, balance=balance, avail=avail,
            db_user_id=user_row["id"], account_id=account_id,
            email=inbox["address"], password=inbox["password"], mail_tm_id=inbox["id"],
            mail_token=inbox["token"],
        )
        await state.set_state(RegState.waiting_wolt_email)
        safe_email = html_lib.escape(inbox["address"])
        await cb.message.edit_text(
            "📧 <b>Почта Wolt готова</b>\n\n"
            f"Адрес: <code>{safe_email}</code>\n\n"
            "Введи этот email в Wolt. Я сам дождусь письма, пришлю ссылку, "
            "а затем куплю номер KZ (+7) и буду ждать SMS-код.\n\n"
            "⏳ Ожидаю письмо до 5 минут.",
            reply_markup=menu_kb(RegState.waiting_wolt_email, is_admin=is_admin),
        )
        asyncio.create_task(_wait_wolt_email(cb.message.chat.id, state))
    except Exception as exc:
        if inbox:
            try:
                await asyncio.to_thread(mail_delete, inbox["id"], inbox["token"])
            except Exception:
                pass
        await state.clear()
        log.error("Wolt email setup failed: %s", exc, exc_info=True)
        try:
            await cb.message.edit_text(
                f"❌ Не удалось создать почту Wolt: {html_lib.escape(str(exc))}",
                reply_markup=menu_kb(None, is_admin=is_admin),
            )
        except Exception:
            await cb.answer(f"Ошибка: {exc}", show_alert=True)


@router.callback_query(F.data == "start_wolt")
async def cb_start_wolt(cb: CallbackQuery, state: FSMContext):
    await _begin_wolt_registration(cb, state)


async def _restart_wolt_email_session(cb, state, account, user_row):
    await state.clear()
    await _load_wolt_state(state, account, user_row)
    await state.set_state(RegState.waiting_wolt_email)
    safe_email = html_lib.escape(account["email"])
    await cb.message.edit_text(
        "📧 <b>Снова проверяю почту Wolt</b>\n\n"
        f"Адрес: <code>{safe_email}</code>\n\n"
        "Если письмо уже пришло, ссылку отправлю автоматически.",
        reply_markup=menu_kb(RegState.waiting_wolt_email, is_admin=_is_admin(user_row)),
    )
    await cb.answer()
    asyncio.create_task(_wait_wolt_email(cb.message.chat.id, state))


async def _restart_wolt_number_session(cb, state, account, user_row):
    await state.clear()
    await _load_wolt_state(state, account, user_row)
    await state.set_state(RegState.waiting_wolt_email)
    await cb.message.edit_text("⏳ Повторяю покупку номера Wolt KZ...")
    await cb.answer()
    await _wolt_buy_number(cb.message.chat.id, state)


async def _resume_wolt_sms_session(cb, state, account, user_row):
    await state.clear()
    await _load_wolt_state(state, account, user_row)
    await state.set_state(RegState.waiting_sms)
    phone = str(account["phone"] or "")
    country = account["country"] if account["country"] is not None else SERVICES["wolt"]["country"]
    phone_short = phone_local(phone, country)
    link = account["email_link"]
    await cb.message.edit_text(
        _number_ready_text(
            swapped=False,
            phone_short=phone_short,
            cost=float(account["cost"] or 0),
            email=account["email"],
            link=link,
            service_emoji="📱",
            service_label="Wolt",
        ),
        reply_markup=_session_markup(RegState.waiting_sms, is_admin=_is_admin(user_row), link=link),
    )
    await cb.answer()
    await _start_sms_watch(cb.message.chat.id, state)


@router.callback_query(F.data.startswith("retry_wolt_email:"))
async def cb_retry_wolt_email(cb: CallbackQuery, state: FSMContext):
    try:
        account_id = int(cb.data.split(":", 1)[1])
        user_row = db_get_user(cb.from_user.id)
        account = db_get_account(account_id, user_row["id"] if user_row else None)
        if not user_row or not account:
            await cb.answer("Wolt-сессия не найдена", show_alert=True)
            return
        await _restart_wolt_email_session(cb, state, account, user_row)
    except Exception as exc:
        log.error("retry Wolt email failed: %s", exc, exc_info=True)
        await cb.answer(f"Ошибка: {exc}", show_alert=True)


@router.callback_query(F.data.startswith("retry_wolt_number:"))
async def cb_retry_wolt_number(cb: CallbackQuery, state: FSMContext):
    try:
        account_id = int(cb.data.split(":", 1)[1])
        user_row = db_get_user(cb.from_user.id)
        account = db_get_account(account_id, user_row["id"] if user_row else None)
        if not user_row or not account:
            await cb.answer("Wolt-сессия не найдена", show_alert=True)
            return
        await _restart_wolt_number_session(cb, state, account, user_row)
    except Exception as exc:
        log.error("retry Wolt number failed: %s", exc, exc_info=True)
        await cb.answer(f"Ошибка: {exc}", show_alert=True)


@router.callback_query(F.data.startswith("resume_wolt:"))
async def cb_resume_wolt(cb: CallbackQuery, state: FSMContext):
    try:
        account_id = int(cb.data.split(":", 1)[1])
        user_row = db_get_user(cb.from_user.id)
        account = db_get_account(account_id, user_row["id"] if user_row else None)
        if not user_row or not account:
            await cb.answer("Wolt-сессия не найдена", show_alert=True)
            return
        if account["status"] in ("email_waiting", "email_timeout", "email_error"):
            await _restart_wolt_email_session(cb, state, account, user_row)
        elif account["status"] == "number_error":
            await _restart_wolt_number_session(cb, state, account, user_row)
        elif account["status"] == "sms_waiting" and account["activation_id"]:
            await _resume_wolt_sms_session(cb, state, account, user_row)
        else:
            await cb.answer("Для этой Wolt-сессии продолжение уже не требуется", show_alert=True)
    except Exception as exc:
        log.error("resume Wolt failed: %s", exc, exc_info=True)
        await cb.answer(f"Ошибка: {exc}", show_alert=True)


@router.callback_query(F.data.startswith("cancel_wolt:"))
async def cb_cancel_wolt(cb: CallbackQuery, state: FSMContext):
    try:
        account_id = int(cb.data.split(":", 1)[1])
        user_row = db_get_user(cb.from_user.id)
        account = db_get_account(account_id, user_row["id"] if user_row else None)
        if not user_row or not account:
            await cb.answer("Wolt-сессия не найдена", show_alert=True)
            return
        if account["activation_id"] and account["status"] not in ("completed", "cancelled"):
            try:
                await asyncio.to_thread(
                    _hero,
                    "setStatus",
                    api_key=get_user_hero_key(user_row),
                    id=account["activation_id"],
                    status=HERO_CANCEL,
                )
            except Exception as exc:
                log.warning("Wolt cancel failed for activation %s: %s", account["activation_id"], exc)
            db_update_activation(account["activation_id"], status="cancelled")
        if account["status"] != "completed":
            db_update_account(account_id, status="cancelled")
        await state.clear()
        await cb.message.edit_text(
            "❌ Wolt-сессия закрыта. Почтовый ящик сохранён в базе.",
            reply_markup=menu_kb(None, is_admin=_is_admin(user_row)),
        )
        await cb.answer()
    except Exception as exc:
        await cb.answer(f"Ошибка: {exc}", show_alert=True)


@router.callback_query(F.data == "start_gmail")
async def cb_start_gmail(cb: CallbackQuery, state: FSMContext):
    await _show_confirm(cb, state, "gmail")


# ═══════════════════════════════════════════════════════════
#  Подтверждение покупки → buy → ждём SMS
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "confirm_buy")
async def cb_confirm_buy(cb: CallbackQuery, state: FSMContext):
    try:
        data = await state.get_data()
        if not data or "service_code" not in data:
            await cb.answer("Данные сессии устарели. Начни заново.", show_alert=True)
            await state.clear(); return

        user_row = db_get_user(cb.from_user.id)
        if not user_row: return
        is_admin = user_row["is_admin"] == 1

        svc_code = data["service_code"]
        svc_emoji = data.get("service_emoji", "")
        svc_label = data.get("service_label", "")
        country = int(data.get("country") or service_country(data.get("service_key", ""), COUNTRY_UK))
        hk = data.get("hero_key")
        db_user_id = data["db_user_id"]

        await cb.message.edit_text("⏳ Покупаю номер...")
        await cb.answer()

        try:
            activation = await _purchase_number(svc_code, country, hk)
        except Exception as e:
            db_error_log(cb.from_user.id, "buy_fail", str(e))
            await cb.message.edit_text(
                f"❌ Не удалось купить номер.\n<code>{html_lib.escape(str(e))}</code>",
                reply_markup=menu_kb(None, is_admin=is_admin),
            )
            return

        phone = activation["phoneNumber"]
        phone_short = phone_local(phone, country)
        actual_cost = float(activation.get("activationCost") or data.get("cost") or 0)

        db_add_activation(db_user_id, activation["activationId"], phone, actual_cost, svc_code, country)

        await state.update_data(
            phone=phone,
            activation_id=activation["activationId"],
            actual_cost=actual_cost,
            country=country,
        )
        await state.set_state(RegState.waiting_sms)

        await cb.message.edit_text(
            _number_ready_text(
                swapped=False,
                phone_short=phone_short,
                cost=actual_cost,
                service_emoji=svc_emoji,
                service_label=svc_label,
            ),
            reply_markup=menu_kb(RegState.waiting_sms, is_admin=is_admin),
        )

        await _start_sms_watch(cb.message.chat.id, state)

    except Exception as e:
        db_error_log(cb.from_user.id, "buy_crash", str(e), traceback.format_exc())
        await cb.answer(f"Ошибка: {e}", show_alert=True)


async def _wait_sms(chat_id, state):
    """Poll HeroSMS for SMS code. Runs as background task."""
    try:
        data = await state.get_data()
        if not data or not data.get("activation_id"):
            return
        _active_states[chat_id] = state
        aid = data["activation_id"]
        watch_id = data.get("sms_watch_id")
        hk = data.get("hero_key")
        svc_emoji = data.get("service_emoji", "")
        svc_label = data.get("service_label", "")
        account_id = data.get("account_id")

        started = time.time()
        deadline = started + SMS_WAIT_SECONDS
        resend_done = False

        async def _still_mine() -> bool:
            current = await state.get_state()
            if current != RegState.waiting_sms.state:
                fresh = await state.get_data()
                # Another watcher owns this chat if watch_id changed while still waiting.
                if fresh.get("sms_watch_id") and fresh.get("sms_watch_id") != watch_id:
                    return False
                _forget_active_state(chat_id, state)
                return False
            fresh = await state.get_data()
            if fresh.get("sms_watch_id") != watch_id or fresh.get("activation_id") != aid:
                return False
            return True

        while time.time() < deadline:
            if not await _still_mine():
                return
            persisted = db_get_activation_for_webhook(aid)
            if persisted and (
                persisted["sms_code"]
                or persisted["status"] in ("completed", "webhook_processing")
            ):
                _forget_active_state(chat_id, state)
                return

            try:
                got, value = await asyncio.to_thread(sms_check, aid, hk)
            except Exception as e:
                log.warning(f"sms_check error aid={aid}: {e}")
                await asyncio.sleep(7); continue

            if not await _still_mine():
                return

            if got:
                await state.update_data(sms_code=value)
                await state.set_state(RegState.completed)
                db_update_activation(aid, sms_code=value, status="completed",
                                     completed_at=datetime.datetime.now().isoformat())
                if account_id:
                    db_update_account(
                        account_id,
                        sms_code=value,
                        status="completed",
                        completed_at=datetime.datetime.now().isoformat(),
                    )
                db_log(chat_id, "got_sms", f"code={value} service={svc_label}")

                user_row = db_get_user(chat_id)
                is_admin = _is_admin(user_row)
                fresh = await state.get_data()
                phone_short = phone_local(fresh.get("phone"), fresh.get("country"))
                code_text = (
                    "✅ <b>SMS получен!</b>\n\n"
                    f"{svc_emoji} {svc_label}\n"
                    f"📱 Код: <code>{html_lib.escape(str(value))}</code>\n"
                )
                if phone_short:
                    code_text += f"Номер: <code>{html_lib.escape(phone_short)}</code>\n"
                if fresh.get("email"):
                    code_text += f"📧 <code>{html_lib.escape(fresh['email'])}</code>\n"
                code_text += "\nНажми «Завершить» когда закончишь."
                await _safe_send(
                    chat_id,
                    code_text,
                    reply_markup=_session_markup(
                        RegState.completed,
                        is_admin=is_admin,
                        link=fresh.get("email_link"),
                    ),
                )
                _forget_active_state(chat_id, state)
                return

            elapsed = time.time() - started
            if elapsed >= SMS_RESEND_AFTER and not resend_done:
                try:
                    await asyncio.to_thread(_hero, "setStatus", hk, id=aid, status=HERO_RESEND)
                    log.info(f"Resend triggered for activation {aid}")
                except Exception as e:
                    log.warning(f"Resend failed aid={aid}: {e}")
                resend_done = True

            await asyncio.sleep(7)

        if not await _still_mine():
            return

        # Timeout — cancel on HeroSMS + DB
        await _cancel_hero_activation(aid, hk)
        if account_id:
            try:
                db_update_account(
                    account_id,
                    status="cancelled",
                    completed_at=datetime.datetime.now().isoformat(),
                )
            except Exception:
                pass
        await state.clear()
        _forget_active_state(chat_id, state)
        await _safe_send(
            chat_id,
            f"⚠️ SMS не пришло за {SMS_WAIT_SECONDS // 60} мин. Номер отменён (деньги вернутся).",
            reply_markup=menu_kb(None),
        )

    except Exception as e:
        log.error(f"_wait_sms CRASHED: {e}", exc_info=True)
        try:
            data = await state.get_data()
            aid = data.get("activation_id")
            hk = data.get("hero_key")
            account_id = data.get("account_id")
            if aid:
                _hero("setStatus", api_key=hk, id=aid, status=HERO_CANCEL)
            if account_id:
                db_update_account(account_id, status="error")
        except Exception:
            pass
        await state.clear()
        _forget_active_state(chat_id, state)
        await _safe_send(chat_id, "⚠️ Произошла ошибка. Сессия сброшена.", reply_markup=menu_kb(None))


async def _swap_number(chat_id, state) -> bool:
    """Cancel the current HeroSMS number and buy a replacement. Returns True on success."""
    current = await state.get_state()
    if current != RegState.waiting_sms.state:
        return False
    data = await state.get_data()
    old_aid = data.get("activation_id")
    if not old_aid:
        return False
    hk = data.get("hero_key")
    svc_code = data.get("service_code") or "rr"
    country = int(data.get("country") or service_country(data.get("service_key", "wolt"), COUNTRY_KZ))
    account_id = data.get("account_id")
    user_row = db_get_user(chat_id)
    is_admin = _is_admin(user_row)

    # Invalidate the old SMS watcher before cancelling, so it exits cleanly.
    await state.update_data(changing_number=True, sms_watch_id=secrets.token_hex(8), activation_id=None)
    await _cancel_hero_activation(old_aid, hk)
    db_log(chat_id, "change_number", f"cancelled={old_aid}")

    try:
        activation = await _purchase_number(svc_code, country, hk)
    except Exception as exc:
        log.warning("change_number buy failed: %s", exc)
        if account_id:
            db_update_account(account_id, status="number_error", phone="", activation_id=None)
        await state.clear()
        _forget_active_state(chat_id, state)
        if account_id:
            await _safe_send(
                chat_id,
                f"❌ Старый номер отменён, новый купить не удалось: {html_lib.escape(str(exc))}",
                reply_markup=_wolt_retry_kb(account_id, "number"),
            )
        else:
            await _safe_send(
                chat_id,
                f"❌ Старый номер отменён, новый купить не удалось: {html_lib.escape(str(exc))}",
                reply_markup=menu_kb(None, is_admin=is_admin),
            )
        return False

    aid = activation["activationId"]
    phone = str(activation["phoneNumber"])
    phone_short = phone_local(phone, country)
    actual_cost = float(activation.get("activationCost") or data.get("cost") or 0)
    db_user_id = data.get("db_user_id") or (user_row["id"] if user_row else None)
    if db_user_id:
        db_add_activation(db_user_id, aid, phone, actual_cost, svc_code, country)
    if account_id:
        db_update_account(
            account_id,
            phone=phone,
            activation_id=aid,
            cost=actual_cost,
            status="sms_waiting",
            country=country,
        )
    await state.update_data(
        phone=phone,
        activation_id=aid,
        actual_cost=actual_cost,
        cost=actual_cost,
        country=country,
        changing_number=False,
    )
    await state.set_state(RegState.waiting_sms)
    link = data.get("email_link")
    await _safe_send(
        chat_id,
        _number_ready_text(
            swapped=True,
            phone_short=phone_short,
            cost=actual_cost,
            email=data.get("email"),
            link=link,
            service_emoji=data.get("service_emoji", ""),
            service_label=data.get("service_label", ""),
        ),
        reply_markup=_session_markup(RegState.waiting_sms, is_admin=is_admin, link=link),
    )
    await _start_sms_watch(chat_id, state)
    return True


@router.callback_query(F.data == "change_number")
async def cb_change_number(cb: CallbackQuery, state: FSMContext):
    try:
        current = await state.get_state()
        if current != RegState.waiting_sms.state:
            await cb.answer("Сейчас нет активного номера", show_alert=True)
            return
        data = await state.get_data()
        if not data.get("activation_id"):
            await cb.answer("Сейчас нет активного номера", show_alert=True)
            return
        lock = _chat_lock(cb.from_user.id)
        if lock.locked() or data.get("changing_number"):
            await cb.answer("Уже меняю номер…")
            return
        await cb.answer("Отменяю номер и покупаю новый…")
        async with lock:
            try:
                await cb.message.edit_text(
                    "⏳ Отменяю текущий номер и покупаю новый…",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="❌ Отменить сессию", callback_data="cancel")],
                    ]),
                )
            except TelegramBadRequest:
                pass
            ok = await _swap_number(cb.message.chat.id, state)
            if not ok:
                return
    except Exception as exc:
        log.error("change_number failed: %s", exc, exc_info=True)
        try:
            await state.update_data(changing_number=False)
        except Exception:
            pass
        await cb.answer(f"Ошибка: {exc}", show_alert=True)


# ═══════════════════════════════════════════════════════════
#  Back / Cancel / Complete
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "back_menu")
async def cb_back_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    _forget_active_state(cb.from_user.id, state)
    user = db_get_user(cb.from_user.id)
    is_admin = user["is_admin"] == 1 if user else False
    await cb.message.edit_text(
        "🤖 <b>SMS Bot</b>\n\nWolt: Казахстан (+7). Gmail: UK (+44).",
        reply_markup=menu_kb(None, is_admin=is_admin),
    )
    await cb.answer()


@router.callback_query(F.data == "cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    try:
        current = await state.get_state()
        user = db_get_user(cb.from_user.id)
        is_admin = user["is_admin"] == 1 if user else False

        data = await state.get_data()
        aid = data.get("activation_id")
        hk = data.get("hero_key")
        account_id = data.get("account_id")
        if aid:
            try:
                _hero("setStatus", api_key=hk, id=aid, status=HERO_CANCEL)
            except Exception as e:
                log.warning(f"Cancel setStatus failed: {e}")

        if current != RegState.completed.state and aid:
            db_update_activation(aid, status="cancelled")
            db_log(cb.from_user.id, "cancelled", f"activation={aid}")
        if current != RegState.completed.state and account_id:
            db_update_account(account_id, status="cancelled")

        await state.clear()
        _forget_active_state(cb.from_user.id, state)
        await cb.message.edit_text(
            "❌ Сессия завершена.",
            reply_markup=menu_kb(None, is_admin=is_admin),
        )
        await cb.answer()
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data == "complete")
async def cb_complete(cb: CallbackQuery, state: FSMContext):
    try:
        user = db_get_user(cb.from_user.id)
        is_admin = user["is_admin"] == 1 if user else False

        data = await state.get_data()
        aid = data.get("activation_id")
        hk = data.get("hero_key")
        account_id = data.get("account_id")
        if aid:
            try:
                _hero("setStatus", api_key=hk, id=aid, status=HERO_COMPLETE)
            except Exception as e:
                log.warning(f"Complete setStatus failed: {e}")
        if account_id:
            db_update_account(
                account_id,
                status="completed",
                completed_at=datetime.datetime.now().isoformat(),
            )

        await state.clear()
        _forget_active_state(cb.from_user.id, state)
        await cb.message.edit_text(
            "✅ Готово! Номер использован.\nМожешь начать новую регистрацию.",
            reply_markup=menu_kb(None, is_admin=is_admin),
        )
        await cb.answer()
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


# ═══════════════════════════════════════════════════════════
#  Status
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "status")
async def cb_status(cb: CallbackQuery, state: FSMContext):
    try:
        current = await state.get_state()
        data = await state.get_data() if current else {}
        user = db_get_user(cb.from_user.id)
        is_admin = user["is_admin"] == 1 if user else False

        state_names = {
            RegState.waiting_hero_key.state: "⏳ Ожидание HeroSMS ключа",
            RegState.confirming.state: "📍 Выбор сервиса",
            RegState.waiting_wolt_email.state: "📧 Ожидание письма Wolt",
            RegState.waiting_sms.state: "📩 Ожидание SMS",
            RegState.completed.state: "✅ SMS получен",
        }
        state_text = state_names.get(current, "—")

        lines = [f"<b>📊 Статус</b>\n\nСостояние: {state_text}"]
        if data.get("phone"):
            ps = phone_local(data["phone"], data.get("country"))
            lines.append(f"Номер: <code>{ps}</code>")
        if data.get("actual_cost"):
            lines.append(f"Цена: ${data['actual_cost']:.4f}")
        if data.get("service_label"):
            lines.append(f"Сервис: {data.get('service_emoji', '')} {data['service_label']}")

        try:
            bal = _hero_balance(api_key=data.get("hero_key") if data else None)
            lines.append(f"Баланс: ${bal:.2f}")
        except Exception:
            pass

        await cb.message.edit_text(
            "\n".join(lines),
            reply_markup=menu_kb(current, is_admin=is_admin),
        )
        await cb.answer()
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await cb.answer()
        else:
            log.warning(f"cb_status TelegramBadRequest: {e}")
            await cb.answer(f"Ошибка Telegram: {e}", show_alert=True)
    except Exception as e:
        log.error(f"cb_status failed: {e}", exc_info=True)
        await cb.answer(f"Ошибка: {e}", show_alert=True)


# ═══════════════════════════════════════════════════════════
#  Мои почты (Mail.tm inboxes)
# ═══════════════════════════════════════════════════════════

ACCOUNT_STATUS_LABELS = {
    "email_waiting": "⏳ ждёт письма Wolt",
    "email_received": "📨 ссылка получена",
    "sms_waiting": "📱 ждёт SMS",
    "completed": "✅ завершён",
    "cancelled": "❌ отменён",
    "email_timeout": "⚠️ письмо не пришло",
    "email_error": "⚠️ ошибка почты",
    "number_error": "⚠️ ошибка номера",
    "timeout": "⚠️ таймаут",
}


def _account_status_label(account) -> str:
    return ACCOUNT_STATUS_LABELS.get(
        _row_get(account, "status"), _row_get(account, "status") or "—"
    )


def mail_accounts_kb(user_id: int) -> InlineKeyboardMarkup:
    rows = []
    for acc in db_get_user_accounts(user_id):
        rows.append([InlineKeyboardButton(
            text=f"📧 {acc['email']}",
            callback_data=f"mail_view:{acc['id']}",
        )])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mail_view_kb(account_id: int, link: str | None = None) -> InlineKeyboardMarkup:
    rows = _link_button_rows(link)
    rows.extend([
        [InlineKeyboardButton(text="📨 Получить последнее письмо", callback_data=f"mail_link:{account_id}")],
        [InlineKeyboardButton(text="🗑 Удалить почту", callback_data=f"mail_del:{account_id}")],
        [InlineKeyboardButton(text="🔙 К списку почт", callback_data="mail_list")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "mail_list")
async def cb_mail_list(cb: CallbackQuery):
    try:
        user = db_get_user(cb.from_user.id)
        if not user:
            await cb.answer("Доступ запрещён", show_alert=True)
            return
        accounts = db_get_user_accounts(user["id"])
        if not accounts:
            await cb.message.edit_text(
                "📬 <b>Мои почты</b>\n\nПока нет ни одной почты.\n"
                "Они создаются при регистрации Wolt.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="back_menu")],
                ]),
            )
        else:
            lines = [f"📬 <b>Мои почты</b> — {len(accounts)}:"]
            for acc in accounts:
                lines.append(f"• <code>{html_lib.escape(acc['email'])}</code> — {_account_status_label(acc)}")
            await cb.message.edit_text(
                "\n".join(lines),
                reply_markup=mail_accounts_kb(user["id"]),
            )
        await cb.answer()
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await cb.answer()
        else:
            await cb.answer(f"Ошибка Telegram: {e}", show_alert=True)
    except Exception as e:
        log.error(f"cb_mail_list failed: {e}", exc_info=True)
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("mail_view:"))
async def cb_mail_view(cb: CallbackQuery):
    try:
        account_id = int(cb.data.split(":", 1)[1])
        user = db_get_user(cb.from_user.id)
        account = db_get_account(account_id, user["id"] if user else None)
        if not user or not account:
            await cb.answer("Почта не найдена", show_alert=True)
            return
        lines = [
            f"📧 <b>{html_lib.escape(account['email'])}</b>",
            "",
            f"Статус: {_account_status_label(account)}",
            f"Создана: {account['created_at'] or '—'}",
        ]
        if account["phone"]:
            ps = phone_local(account["phone"], account["country"])
            lines.append(f"Номер: <code>{html_lib.escape(ps)}</code>")
        if account["email_link"]:
            lines.append("Ссылка: уже получена ✅")
        lines.append("")
        lines.append("«Последнее письмо» — заново проверю Mail.tm и пришлю свежую ссылку Wolt.")
        await cb.message.edit_text("\n".join(lines), reply_markup=mail_view_kb(account_id))
        await cb.answer()
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await cb.answer()
        else:
            await cb.answer(f"Ошибка Telegram: {e}", show_alert=True)
    except Exception as e:
        log.error(f"cb_mail_view failed: {e}", exc_info=True)
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("mail_link:"))
async def cb_mail_link(cb: CallbackQuery):
    try:
        account_id = int(cb.data.split(":", 1)[1])
        user = db_get_user(cb.from_user.id)
        account = db_get_account(account_id, user["id"] if user else None)
        if not user or not account:
            await cb.answer("Почта не найдена", show_alert=True)
            return

        await cb.message.edit_text(
            f"⏳ Проверяю почту <code>{html_lib.escape(account['email'])}</code>...",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 К списку почт", callback_data="mail_list")],
            ]),
        )
        await cb.answer()

        token = account["mail_token"]
        try:
            messages = await asyncio.to_thread(mail_fetch, token)
        except MailTMAuthError:
            token = await asyncio.to_thread(mail_login, account["email"], account["password"])
            db_update_account(account_id, mail_token=token)
            messages = await asyncio.to_thread(mail_fetch, token)

        messages = sorted(
            messages,
            key=lambda item: item.get("createdAt", ""),
            reverse=True,
        )
        link = None
        found_wolt = False
        for summary in messages:
            meta = " ".join([
                _mail_sender(summary),
                str(summary.get("subject") or ""),
                str(summary.get("intro") or ""),
            ]).lower()
            if "wolt" not in meta or not summary.get("id"):
                continue
            found_wolt = True
            try:
                full = await asyncio.to_thread(mail_get_message, token, summary["id"])
                link = mail_extract_link(full)
            except Exception as exc:
                log.warning("mail_link parse failed for account %s: %s", account_id, exc)
            if link:
                db_update_account(
                    account_id,
                    email_link=link,
                    mail_message_id=str(summary["id"]),
                )
                break

        if link:
            refreshed = db_get_account(account_id, user["id"])
            lines = [
                f"📧 <b>{html_lib.escape(account['email'])}</b>",
                "",
                f"Статус: {_account_status_label(refreshed)}",
                f"Создана: {refreshed['created_at'] or '—'}",
                "Ссылка ниже — открой или скопируй.",
                f'<a href="{html_lib.escape(link, quote=True)}">🔗 Открыть ссылку Wolt</a>',
            ]
            if refreshed["phone"]:
                ps = phone_local(refreshed["phone"], refreshed["country"])
                lines.append(f"Номер: <code>{html_lib.escape(ps)}</code>")
            await cb.message.edit_text("\n".join(lines), reply_markup=mail_view_kb(account_id, link=link))
        elif found_wolt:
            await cb.message.edit_text(
                f"📧 <b>{html_lib.escape(account['email'])}</b>\n\n"
                "Письма от Wolt есть, но ссылку извлечь не удалось.\n"
                "Попробуй ещё раз через пару минут.",
                reply_markup=mail_view_kb(account_id),
            )
        else:
            await cb.message.edit_text(
                f"📧 <b>{html_lib.escape(account['email'])}</b>\n\n"
                "📭 Писем от Wolt пока нет.\n"
                "Введи эту почту на wolt.com — как только письмо придёт, жми кнопку снова.",
                reply_markup=mail_view_kb(account_id),
            )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await cb.answer()
        else:
            await cb.answer(f"Ошибка Telegram: {e}", show_alert=True)
    except Exception as e:
        log.error(f"cb_mail_link failed: {e}", exc_info=True)
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("mail_del:"))
async def cb_mail_del(cb: CallbackQuery):
    try:
        account_id = int(cb.data.split(":", 1)[1])
        user = db_get_user(cb.from_user.id)
        account = db_get_account(account_id, user["id"] if user else None)
        if not user or not account:
            await cb.answer("Почта не найдена", show_alert=True)
            return
        await cb.message.edit_text(
            f"🗑 <b>Удалить почту?</b>\n\n"
            f"<code>{html_lib.escape(account['email'])}</code>\n\n"
            "Ящик будет удалён с Mail.tm <b>безвозвратно</b>, вместе с письмами.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"mail_del_do:{account_id}")],
                [InlineKeyboardButton(text="❌ Нет, отмена", callback_data=f"mail_view:{account_id}")],
            ]),
        )
        await cb.answer()
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await cb.answer()
        else:
            await cb.answer(f"Ошибка Telegram: {e}", show_alert=True)
    except Exception as e:
        log.error(f"cb_mail_del failed: {e}", exc_info=True)
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("mail_del_do:"))
async def cb_mail_del_do(cb: CallbackQuery):
    try:
        account_id = int(cb.data.split(":", 1)[1])
        user = db_get_user(cb.from_user.id)
        account = db_get_account(account_id, user["id"] if user else None)
        if not user or not account:
            await cb.answer("Почта не найдена", show_alert=True)
            return

        # cancel any active HeroSMS activation first, if still pending
        if account["activation_id"] and account["status"] not in ("completed", "cancelled", "timeout"):
            try:
                await asyncio.to_thread(
                    _hero, "setStatus",
                    api_key=get_user_hero_key(user),
                    id=account["activation_id"],
                    status=HERO_CANCEL,
                )
            except Exception as exc:
                log.warning("mail delete: cancel activation %s failed: %s", account["activation_id"], exc)

        mail_tm_deleted = False
        try:
            token = account["mail_token"]
            try:
                await asyncio.to_thread(mail_delete, account["mail_tm_id"], token)
            except MailTMAuthError:
                token = await asyncio.to_thread(mail_login, account["email"], account["password"])
                await asyncio.to_thread(mail_delete, account["mail_tm_id"], token)
            mail_tm_deleted = True
        except Exception as exc:
            log.warning("mail delete: Mail.tm delete failed for %s: %s", account["email"], exc)

        db_delete_account(account_id, user["id"])
        db_log(cb.from_user.id, "mail_delete", account["email"])

        remaining = db_get_user_accounts(user["id"])
        if remaining:
            lines = [f"📬 <b>Мои почты</b> — {len(remaining)}:"]
            for acc in remaining:
                lines.append(f"• <code>{html_lib.escape(acc['email'])}</code> — {_account_status_label(acc)}")
            await cb.message.edit_text(
                "\n".join(lines),
                reply_markup=mail_accounts_kb(user["id"]),
            )
        else:
            await cb.message.edit_text(
                "🗑 Почта удалена.\n\n📬 Список почт пуст.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="back_menu")],
                ]),
            )
        await cb.answer(
            "Почта удалена ✅" + ("" if mail_tm_deleted else " (Mail.tm: ящик удалить не удалось, удалил из базы)"),
            show_alert=not mail_tm_deleted,
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await cb.answer()
        else:
            await cb.answer(f"Ошибка Telegram: {e}", show_alert=True)
    except Exception as e:
        log.error(f"cb_mail_del_do failed: {e}", exc_info=True)
        await cb.answer(f"Ошибка: {e}", show_alert=True)


# ═══════════════════════════════════════════════════════════
#  Admin Panel (unchanged from original, with small fixes)
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "admin_panel")
async def cb_admin_panel(cb: CallbackQuery):
    try:
        user = db_get_user(cb.from_user.id)
        if not user or user["is_admin"] != 1:
            await cb.answer("Доступ запрещён", show_alert=True); return
        await cb.message.edit_text(
            "👑 Админ-панель\nВыбери действие:", reply_markup=admin_kb()
        ); await cb.answer()
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("admin_users"))
async def cb_admin_users(cb: CallbackQuery):
    try:
        user = db_get_user(cb.from_user.id)
        if not user or user["is_admin"] != 1:
            await cb.answer("Доступ запрещён", show_alert=True); return
        parts = cb.data.split(":"); page = int(parts[1]) if len(parts) > 1 else 0; per_page = 5
        rows = db_get_all_users_enriched()
        if not rows:
            await cb.message.edit_text("👥 Пользователи\n\nНет пользователей.", reply_markup=admin_kb()); await cb.answer(); return
        total = len(rows); total_pages = (total + per_page - 1) // per_page; page = max(0, min(page, total_pages - 1))
        chunk = rows[page * per_page : (page + 1) * per_page]
        text = f"👥 Пользователи ({total})\n\n"
        for u in chunk:
            badge = " 👑" if u["is_admin"] else ""
            name = u["first_name"] or "—"
            uname = f" @{u['username']}" if u["username"] else ""
            ac = u["activation_count"] or 0
            last = u["last_activity"]; ls = f" | {last[:10]}" if last else ""
            full_user = db_get_user(u["telegram_id"])
            ki = "🔵" if full_user["use_admin_hero"] else ("🟢" if full_user["hero_key"] else "🔴")
            text += f"{name}{badge}{uname} {ki}\n  {u['telegram_id']} | 🔄 {ac}{ls}\n\n"
        nav = []
        for u in chunk:
            nav.append([InlineKeyboardButton(
                text=f"👤 {u['first_name'] or 'User'} {u['telegram_id']}",
                callback_data=f"admin_user_detail:{u['id']}",
            )])
        pb = []
        if page > 0:
            pb.append(InlineKeyboardButton(text="◀ Назад", callback_data=f"admin_users:{page - 1}"))
        if page < total_pages - 1:
            pb.append(InlineKeyboardButton(text="Далее ▶", callback_data=f"admin_users:{page + 1}"))
        if pb: nav.append(pb)
        nav.append([InlineKeyboardButton(text="🔙 Админ-панель", callback_data="admin_panel")])
        await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=nav))
        await cb.answer()
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(cb: CallbackQuery):
    try:
        admin = db_get_user(cb.from_user.id)
        if not admin or admin["is_admin"] != 1:
            await cb.answer("Доступ запрещён", show_alert=True); return
        s = db_get_stats()
        try: bal = _hero_balance()
        except Exception: bal = None
        sr = f"{s['successful'] / s['total_activations'] * 100:.0f}%" if s["total_activations"] > 0 else "—"
        text = (
            f"📊 Статистика\n\n"
            f"👥 Пользователей: {s['total_users']}\n"
            f"🔄 Активаций всего: {s['total_activations']}\n"
            f"✅ Успешных (SMS получен): {s['successful']} ({sr})\n"
        )
        if bal is not None:
            text += f"💰 Баланс HeroSMS: ${bal:.2f}\n"
        await cb.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 В админ-панель", callback_data="admin_panel")],
            ]),
        ); await cb.answer()
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data == "admin_add_user")
async def cb_admin_add_user(cb: CallbackQuery, state: FSMContext):
    try:
        user = db_get_user(cb.from_user.id)
        if not user or user["is_admin"] != 1:
            await cb.answer("Доступ запрещён", show_alert=True); return
        await cb.message.edit_text(
            "📝 Добавить пользователя\n\nПришли Telegram ID или @username.\n❌ /cancel чтобы отменить.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_admin")],
            ]),
        )
        await state.set_state(AdminState.add_user_wait); await cb.answer()
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.message(AdminState.add_user_wait)
async def admin_add_user_input(msg: Message, state: FSMContext):
    admin = db_get_user(msg.from_user.id)
    if not admin or admin["is_admin"] != 1:
        await state.clear(); return
    raw = msg.text.strip()
    if raw.lower() in ("/cancel", "отмена"):
        await state.clear()
        await msg.answer("❌ Добавление отменено.", reply_markup=admin_kb()); return
    tid = None; username = ""; first_name = raw
    if raw.startswith("@"):
        username = raw[1:]
        try:
            chat = await _bot.get_chat(raw)
            tid = chat.id; first_name = chat.first_name or username; username = chat.username or username
        except Exception:
            await msg.answer(
                "❌ Не удалось найти по username.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_admin")],
                ]),
            ); return
    else:
        try: tid = int(raw)
        except ValueError:
            await msg.answer(
                "❌ Неверный формат.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_admin")],
                ]),
            ); return
        try:
            chat = await _bot.get_chat(tid)
            first_name = chat.first_name or str(tid); username = chat.username or ""
        except Exception:
            first_name = str(tid)

    conn = sqlite3.connect(DB_PATH)
    try:
        existing = conn.execute(
            "SELECT id, is_active FROM users WHERE telegram_id = ?", (tid,)
        ).fetchone()
        if existing:
            if existing[1]:
                await msg.answer("⚠️ Пользователь уже в базе.", reply_markup=admin_kb())
            else:
                conn.execute("UPDATE users SET is_active = 1 WHERE telegram_id = ?", (tid,))
                conn.commit()
                await msg.answer(f"✅ Пользователь {first_name} восстановлен!", reply_markup=admin_kb())
                db_log(admin["telegram_id"], "admin_restore_user", f"tg={tid}")
            await state.clear(); return
        conn.execute(
            "INSERT INTO users (telegram_id, username, first_name, added_by) VALUES (?, ?, ?, ?)",
            (tid, username, first_name, admin["telegram_id"]),
        )
        conn.commit()
    finally:
        conn.close()
    await state.clear()
    await msg.answer(f"✅ Пользователь {first_name} (ID: {tid}) добавлен!", reply_markup=admin_kb())
    db_log(admin["telegram_id"], "admin_add_user", f"tg={tid}")
    try:
        await _bot.send_message(
            tid, "✅ Тебе открыт доступ к SMS Bot.\nНажми /start для начала работы."
        )
    except Exception:
        pass


@router.callback_query(F.data == "admin_rm_user")
async def cb_admin_rm_user(cb: CallbackQuery, state: FSMContext):
    try:
        user = db_get_user(cb.from_user.id)
        if not user or user["is_admin"] != 1:
            await cb.answer("Доступ запрещён", show_alert=True); return
        await cb.message.edit_text(
            "📝 Удалить пользователя\n\nПришли Telegram ID.\nСебя удалить нельзя.\n❌ /cancel чтобы отменить.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_admin")],
            ]),
        )
        await state.set_state(AdminState.rm_user_wait); await cb.answer()
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.message(AdminState.rm_user_wait)
async def admin_rm_user_input(msg: Message, state: FSMContext):
    admin = db_get_user(msg.from_user.id)
    if not admin or admin["is_admin"] != 1:
        await state.clear(); return
    raw = msg.text.strip()
    if raw.lower() in ("/cancel", "отмена"):
        await state.clear()
        await msg.answer("❌ Удаление отменено.", reply_markup=admin_kb()); return
    try: tid = int(raw)
    except ValueError:
        await msg.answer(
            "❌ Нужен числовой ID.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_admin")],
            ]),
        ); return
    if tid == admin["telegram_id"]:
        await msg.answer("❌ Себя удалить нельзя.", reply_markup=admin_kb()); await state.clear(); return
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("UPDATE users SET is_active = 0 WHERE telegram_id = ?", (tid,))
        conn.commit()
    finally:
        conn.close()
    await state.clear()
    await msg.answer(f"✅ Пользователь {tid} деактивирован.", reply_markup=admin_kb())
    db_log(admin["telegram_id"], "admin_rm_user", f"tg={tid}")


@router.callback_query(F.data == "cancel_admin")
async def cb_cancel_admin(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("❌ Действие отменено.", reply_markup=admin_kb()); await cb.answer()


@router.callback_query(F.data == "admin_keys")
async def cb_admin_keys(cb: CallbackQuery):
    try:
        admin = db_get_user(cb.from_user.id)
        if not admin or admin["is_admin"] != 1:
            await cb.answer("Доступ запрещён", show_alert=True); return
        rows = db_get_all_users()
        mask = mask_hero_key(HERO_KEY)
        try:
            bal = _hero_balance()
            bal_s = f"${bal:.2f}"
        except Exception:
            bal_s = "не удалось проверить"
        text = (
            f"🔑 Управление ключами\n\n"
            f"Админский ключ: {mask}\n"
            f"Баланс: {bal_s}\n\n"
        )
        kb = [[InlineKeyboardButton(text="✏️ Сменить админский ключ", callback_data="admin_change_hero_key")]]
        if not rows:
            text += "👥 Нет пользователей."
            kb.append([InlineKeyboardButton(text="🔙 Админ-панель", callback_data="admin_panel")])
            await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=None)
            await cb.answer()
            return
        for tid, username, fname, is_admin, created in rows:
            user = db_get_user(tid)
            name = fname or str(tid); uname = f" @{username}" if username else ""
            badge = " 👑" if is_admin else ""
            if user["use_admin_hero"]:
                status = "🔵 админский ключ"
                toggle_label = "❌ Отключить"
                toggle_data = f"admin_key_toggle:{tid}:off"
            elif user["hero_key"]:
                status = "🟢 свой ключ"
                toggle_label = "🔵 Включить админский"
                toggle_data = f"admin_key_toggle:{tid}:on"
            else:
                status = "🔴 нет ключа"
                toggle_label = "🔵 Включить админский"
                toggle_data = f"admin_key_toggle:{tid}:on"
            text += f"{name}{badge}{uname}\n  {status}\n\n"
            kb.append([InlineKeyboardButton(
                text=f"{toggle_label} — {name}", callback_data=toggle_data,
            )])
        kb.append([InlineKeyboardButton(text="🔙 Админ-панель", callback_data="admin_panel")])
        await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=None)
        await cb.answer()
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await cb.answer()
        else:
            await cb.answer(f"Ошибка: {e}", show_alert=True)
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data == "admin_change_hero_key")
async def cb_admin_change_hero_key(cb: CallbackQuery, state: FSMContext):
    try:
        admin = db_get_user(cb.from_user.id)
        if not admin or admin["is_admin"] != 1:
            await cb.answer("Доступ запрещён", show_alert=True)
            return
        await cb.message.edit_text(
            "✏️ Сменить админский ключ HeroSMS\n\n"
            "Пришли новый ключ текстом.\n"
            "Проверю баланс и сохраню в .env — рестарт не нужен.\n"
            "Сообщение с ключом удалю.\n\n"
            "❌ /cancel или кнопка чтобы отменить.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_admin")],
            ]),
            parse_mode=None,
        )
        await state.set_state(AdminState.waiting_admin_hero_key)
        await cb.answer()
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await cb.answer()
        else:
            await cb.answer(f"Ошибка: {e}", show_alert=True)
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.message(AdminState.waiting_admin_hero_key)
async def admin_hero_key_input(msg: Message, state: FSMContext):
    admin = db_get_user(msg.from_user.id)
    if not admin or admin["is_admin"] != 1:
        await state.clear()
        return
    raw = (msg.text or "").strip()
    if raw.lower() in ("/cancel", "отмена", "/start"):
        await state.clear()
        await msg.answer("❌ Смена ключа отменена.", reply_markup=admin_kb(), parse_mode=None)
        return

    async def _forget_secret():
        try:
            await msg.delete()
        except Exception:
            pass

    if len(raw) < MIN_HERO_KEY_LEN:
        await _forget_secret()
        await msg.answer(
            "❌ Слишком короткий ключ. HeroSMS ключ обычно 32+ символов.\nПришли другой или /cancel.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_admin")],
            ]),
            parse_mode=None,
        )
        return

    await _forget_secret()
    try:
        bal = _hero_balance(api_key=raw)
        mask = persist_admin_hero_key(raw)
    except Exception as e:
        await msg.answer(
            f"❌ Ключ не работает: {e}\n\nПришли другой или /cancel.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_admin")],
            ]),
            parse_mode=None,
        )
        return
    await state.clear()
    db_log(admin["telegram_id"], "admin_set_hero_key", f"balance=${bal:.2f} mask={mask}")
    await msg.answer(
        f"✅ Админский ключ обновлён.\nМаска: {mask}\nБаланс: ${bal:.2f}\n\n"
        "Ключ уже в .env и в процессе. Пользователи с админским ключом используют новый.",
        reply_markup=admin_kb(),
        parse_mode=None,
    )


@router.callback_query(F.data.startswith("admin_key_toggle:"))
async def cb_admin_key_toggle(cb: CallbackQuery):
    try:
        admin = db_get_user(cb.from_user.id)
        if not admin or admin["is_admin"] != 1:
            await cb.answer("Доступ запрещён", show_alert=True); return
        parts = cb.data.split(":"); tid = int(parts[1]); action = parts[2]
        conn = sqlite3.connect(DB_PATH)
        try:
            if action == "on":
                conn.execute("UPDATE users SET use_admin_hero = 1 WHERE telegram_id = ?", (tid,))
            else:
                conn.execute("UPDATE users SET use_admin_hero = 0 WHERE telegram_id = ?", (tid,))
            conn.commit()
        finally:
            conn.close()
        db_log(admin["telegram_id"], "admin_key_toggle", f"user={tid} use_admin_hero={'on' if action == 'on' else 'off'}")
        await cb.answer(f"{'✅ Включён' if action == 'on' else '❌ Отключён'} админский ключ для {tid}")
        await cb_admin_keys(cb)
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("admin_user_detail:"))
async def cb_admin_user_detail(cb: CallbackQuery):
    try:
        admin = db_get_user(cb.from_user.id)
        if not admin or admin["is_admin"] != 1:
            await cb.answer("Доступ запрещён", show_alert=True); return
        parts = cb.data.split(":"); user_db_id = int(parts[1])
        u = db_get_user_by_id(user_db_id)
        if not u:
            await cb.answer("Пользователь не найден", show_alert=True); return
        name = u["first_name"] or str(u["telegram_id"])
        uname = f" @{u['username']}" if u["username"] else ""
        badge = "👑 Админ" if u["is_admin"] else "👤 Пользователь"
        if u["use_admin_hero"]: key_status = "🔵 админский"
        elif u["hero_key"]: key_status = "🟢 свой"
        else: key_status = "🔴 нет"
        conn = sqlite3.connect(DB_PATH)
        try:
            act_total = conn.execute(
                "SELECT COUNT(*) FROM activations WHERE user_id = ?", (user_db_id,)
            ).fetchone()[0]
            act_ok = conn.execute(
                "SELECT COUNT(*) FROM activations WHERE user_id = ? AND status = 'completed'",
                (user_db_id,),
            ).fetchone()[0]
        finally:
            conn.close()
        text = (
            f"👤 {name}{uname}\n\n"
            f"ID: {u['telegram_id']}\nРоль: {badge}\n"
            f"Ключ: {key_status}\n"
            f"Активаций: {act_total} (✅ {act_ok})\n"
            f"Добавлен: {u['created_at'][:10]}\n"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="📜 История активаций", callback_data=f"admin_user_activations:{user_db_id}:0"
            )],
            [InlineKeyboardButton(text="🔙 К списку пользователей", callback_data="admin_users:0")],
        ])
        await cb.message.edit_text(text, reply_markup=kb); await cb.answer()
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("admin_user_activations:"))
async def cb_admin_user_activations(cb: CallbackQuery):
    try:
        admin = db_get_user(cb.from_user.id)
        if not admin or admin["is_admin"] != 1:
            await cb.answer("Доступ запрещён", show_alert=True); return
        parts = cb.data.split(":"); user_db_id = int(parts[1]); page = int(parts[2]) if len(parts) > 2 else 0
        per_page = 5
        rows = db_get_user_activations(user_db_id, limit=100)
        total = len(rows); total_pages = max(1, (total + per_page - 1) // per_page); page = max(0, min(page, total_pages - 1))
        chunk = rows[page * per_page : (page + 1) * per_page]
        if not chunk:
            await cb.message.edit_text(
                "Нет активаций.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 К профилю", callback_data=f"admin_user_detail:{user_db_id}")],
                ]),
            ); await cb.answer(); return
        text = f"📜 Активации ({total})\n\n"
        for a in chunk:
            phone = a["phone"]; ps = phone_local(phone, _row_get(a, "country")) if phone else "?"
            svc_name = _row_get(a, "service") or ""
            st = {"started": "🔄", "completed": "✅", "cancelled": "❌"}.get(a["status"], "❓")
            text += f"{st} {ps} ({svc_name}) | {a['created_at'][:16]}\n  Цена: ${a['cost']:.4f} | SMS: {a['sms_code'] or '—'}\n\n"
        kb = []
        pb = []
        if page > 0:
            pb.append(InlineKeyboardButton(text="◀", callback_data=f"admin_user_activations:{user_db_id}:{page - 1}"))
        if page < total_pages - 1:
            pb.append(InlineKeyboardButton(text="▶", callback_data=f"admin_user_activations:{user_db_id}:{page + 1}"))
        if pb: kb.append(pb)
        kb.append([InlineKeyboardButton(text="🔙 К профилю", callback_data=f"admin_user_detail:{user_db_id}")])
        await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
        await cb.answer()
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("admin_history"))
async def cb_admin_history(cb: CallbackQuery):
    try:
        admin = db_get_user(cb.from_user.id)
        if not admin or admin["is_admin"] != 1:
            await cb.answer("Доступ запрещён", show_alert=True); return
        parts = cb.data.split(":"); page = int(parts[1]) if len(parts) > 1 else 0; per_page = 5
        total = db_get_activations_count()
        if total == 0:
            await cb.message.edit_text("Нет активаций.", reply_markup=admin_kb()); await cb.answer(); return
        total_pages = (total + per_page - 1) // per_page; page = max(0, min(page, total_pages - 1))
        rows = db_get_activations_enriched(limit=per_page, offset=page * per_page)
        text = f"📜 История активаций ({total})\n\n"
        for a in rows:
            phone = a["phone"]; ps = phone_local(phone, _row_get(a, "country")) if phone else "?"
            name = a["first_name"] or str(a["telegram_id"])
            svc_name = _row_get(a, "service") or ""
            st = {"started": "🔄", "completed": "✅", "cancelled": "❌"}.get(a["status"], "❓")
            text += f"{st} {name} | {ps} ({svc_name})\n  {a['created_at'][:16]} | {a['sms_code'] or '—'}\n\n"
        kb = []
        pb = []
        if page > 0:
            pb.append(InlineKeyboardButton(text="◀", callback_data=f"admin_history:{page - 1}"))
        if page < total_pages - 1:
            pb.append(InlineKeyboardButton(text="▶", callback_data=f"admin_history:{page + 1}"))
        if pb: kb.append(pb)
        kb.append([InlineKeyboardButton(text="🔙 Админ-панель", callback_data="admin_panel")])
        await cb.message.edit_text(
            text + f"Стр. {page + 1}/{total_pages}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        ); await cb.answer()
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("admin_logs"))
async def cb_admin_logs(cb: CallbackQuery):
    try:
        admin = db_get_user(cb.from_user.id)
        if not admin or admin["is_admin"] != 1:
            await cb.answer("Доступ запрещён", show_alert=True); return
        parts = cb.data.split(":"); page = int(parts[1]) if len(parts) > 1 else 0; per_page = 10
        conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
        try:
            total = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
            if total == 0:
                await cb.message.edit_text("📋 Логи\n\nПусто.", reply_markup=admin_kb()); await cb.answer(); return
            total_pages = (total + per_page - 1) // per_page; page = max(0, min(page, total_pages - 1))
            rows = conn.execute(
                "SELECT * FROM logs ORDER BY id DESC LIMIT ? OFFSET ?",
                (per_page, page * per_page),
            ).fetchall()
        finally:
            conn.close()
        text = f"📋 Логи ({total})\n\n"
        for r in rows:
            text += f"[{r['created_at'][:16]}] tg={r['telegram_id']}: {r['action']}\n  {r['details'][:60]}\n\n"
        kb = []
        pb = []
        if page > 0:
            pb.append(InlineKeyboardButton(text="◀", callback_data=f"admin_logs:{page - 1}"))
        if page < total_pages - 1:
            pb.append(InlineKeyboardButton(text="▶", callback_data=f"admin_logs:{page + 1}"))
        if pb: kb.append(pb)
        kb.append([InlineKeyboardButton(text="🔙 Админ-панель", callback_data="admin_panel")])
        await cb.message.edit_text(
            text + f"Стр. {page + 1}/{total_pages}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        ); await cb.answer()
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data == "admin_back")
async def cb_admin_back(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    user = db_get_user(cb.from_user.id)
    is_admin = user["is_admin"] == 1 if user else False
    await cb.message.edit_text(
        "🤖 <b>SMS Bot</b>\n\nWolt: Казахстан (+7). Gmail: UK (+44).",
        reply_markup=menu_kb(None, is_admin=is_admin),
    ); await cb.answer()


async def hero_webhook(request: web.Request) -> web.Response:
    token = request.match_info.get("token", "")
    if not HERO_WEBHOOK_TOKEN or token != HERO_WEBHOOK_TOKEN:
        return web.json_response({"ok": False}, status=404)
    try:
        raw_body = await request.read()
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
    if not isinstance(payload, dict):
        return web.json_response({"ok": False, "error": "object_required"}, status=400)

    activation_id, code = parse_hero_webhook(payload)
    if not activation_id or not code:
        log.warning("HeroSMS webhook payload without activation/code")
        return web.json_response({"ok": True, "ignored": "missing_activation_or_code"})

    row = await asyncio.to_thread(db_get_activation_for_webhook, activation_id)
    if row is None:
        log.warning("HeroSMS webhook for unknown activation %s", activation_id)
        return web.json_response({"ok": True, "ignored": "unknown_activation"})
    if row["sms_code"] or row["status"] in ("completed", "webhook_processing"):
        return web.json_response({"ok": True, "duplicate": True})
    if not await asyncio.to_thread(db_claim_activation_sms, activation_id, code):
        return web.json_response({"ok": True, "duplicate": True})

    chat_id = int(row["telegram_id"])
    user_row = db_get_user(chat_id)
    active_state = _active_states.get(chat_id)
    if active_state is not None:
        await active_state.update_data(sms_code=code)
        await active_state.set_state(RegState.completed)
    is_admin = _is_admin(user_row)
    markup_state = RegState.completed if active_state is not None else None
    phone_short = phone_local(row["phone"], row["country"]) if row["phone"] else ""
    text = (
        "✅ <b>SMS получен через webhook!</b>\n\n"
        f"Сервис: {html_lib.escape(str(row['service'] or '—'))}\n"
        f"📱 Код: <code>{html_lib.escape(code)}</code>\n"
    )
    if phone_short:
        text += f"Номер: <code>{html_lib.escape(phone_short)}</code>\n"
    text += "\nНомер можно завершить и освободить."
    try:
        if _bot is None:
            raise RuntimeError("Telegram bot is not initialized")
        await _replace_ui(
            chat_id,
            text,
            reply_markup=menu_kb(markup_state, is_admin=is_admin),
        )
    except Exception:
        await asyncio.to_thread(db_release_webhook_sms, activation_id)
        if active_state is not None:
            await active_state.set_state(RegState.waiting_sms)
        log.exception("Failed to deliver HeroSMS webhook code for %s", activation_id)
        return web.json_response({"ok": False, "error": "telegram_delivery_failed"}, status=502)

    await asyncio.to_thread(db_finish_webhook_sms, activation_id)
    try:
        await asyncio.to_thread(
            _hero,
            "setStatus",
            api_key=get_user_hero_key(user_row),
            id=activation_id,
            status=HERO_COMPLETE,
        )
    except Exception as exc:
        log.warning("HeroSMS webhook complete failed for %s: %s", activation_id, exc)
    log.info("HeroSMS webhook delivered activation %s", activation_id)
    return web.json_response({"ok": True})


async def _start_hero_webhook_server():
    if not HERO_WEBHOOK_TOKEN:
        log.warning("HERO_WEBHOOK_TOKEN is empty; HeroSMS webhook listener is disabled")
        return None
    application = web.Application(client_max_size=2 * 1024 * 1024)
    application.router.add_post("/hooks/herosms/{token}", hero_webhook)
    runner = web.AppRunner(application, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, HERO_WEBHOOK_HOST, HERO_WEBHOOK_PORT)
    await site.start()
    log.info("HeroSMS webhook listener started on %s:%s", HERO_WEBHOOK_HOST, HERO_WEBHOOK_PORT)
    return runner


# ═══════════════════════════════════════════════════════════
#  Helpers — live UI (one message with current actions)
# ═══════════════════════════════════════════════════════════

async def _purge_ui(chat_id, keep=None):
    """Delete previously tracked bot messages so stale buttons disappear.

    If Telegram refuses to delete (too old), strip the inline keyboard instead.
    `keep` is the message_id that should stay (usually the one the user just tapped).
    """
    if _bot is None:
        return
    ids = db_ui_list(chat_id)
    kept = []
    keep_id = int(keep) if keep is not None else None
    for mid in ids:
        if keep_id is not None and int(mid) == keep_id:
            kept.append(int(mid))
            continue
        try:
            await _bot.delete_message(chat_id, mid)
        except Exception:
            try:
                await _bot.edit_message_reply_markup(
                    chat_id=chat_id, message_id=mid, reply_markup=None,
                )
            except Exception:
                pass
    db_ui_set(chat_id, kept)


def _track_ui(chat_id, message):
    if message is None:
        return
    mid = getattr(message, "message_id", None)
    if mid is not None:
        db_ui_add(chat_id, mid)


async def _safe_send_raw(chat_id, text, reply_markup=None):
    if _bot is None:
        log.error("_safe_send_raw: bot is not initialized")
        return None
    try:
        return await _bot.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception as e:
        log.error(f"_safe_send failed: {e}")
        try:
            return await _bot.send_message(chat_id, text)
        except Exception as e2:
            log.error(f"_safe_send fallback failed: {e2}")
            return None


async def _replace_ui(chat_id, text, reply_markup=None):
    """Delete stale UI messages, then send one new message with current actions."""
    await _purge_ui(chat_id)
    msg = await _safe_send_raw(chat_id, text, reply_markup=reply_markup)
    _track_ui(chat_id, msg)
    return msg


async def _safe_send(chat_id, text, reply_markup=None):
    """Background-task send: always replace the live panel instead of stacking messages."""
    return await _replace_ui(chat_id, text, reply_markup=reply_markup)


# ═══════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════

async def main():
    global _bot
    missing = [name for name, value in (
        ("BOT_TOKEN", BOT_TOKEN),
        ("HERO_KEY", HERO_KEY),
    ) if not value]
    if not ADMIN_ID:
        missing.append("ADMIN_ID")
    if missing:
        raise SystemExit(
            "Missing required config: " + ", ".join(missing) + ". Copy .env.example to .env and fill it in."
        )
    db_init()
    _bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    await _bot.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
    ])
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    webhook_runner = await _start_hero_webhook_server()
    log.info("SMS bot v2.6 started")
    try:
        await dp.start_polling(_bot)
    finally:
        if webhook_runner is not None:
            await webhook_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
