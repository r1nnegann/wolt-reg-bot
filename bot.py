#!/usr/bin/env python3
"""SMS Bot v2.15 — Outlook Wolt activation; durable number cancellation retained."""
import asyncio, html as html_lib, json, os, re, secrets, string, tempfile, time, sqlite3, datetime, traceback, logging, httpx
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
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

# HeroSMS getCountries `eng` (lowercase) → calling code without +.
DIAL_BY_ENG = {
    "afghanistan": "93", "albania": "355", "algeria": "213", "angola": "244",
    "anguilla": "1264", "antigua and barbuda": "1268", "argentina": "54",
    "armenia": "374", "aruba": "297", "australia": "61", "austria": "43",
    "azerbaijan": "994", "bahamas": "1242", "bahrain": "973", "bangladesh": "880",
    "barbados": "1246", "belarus": "375", "belgium": "32", "belize": "501",
    "benin": "229", "bhutan": "975", "bolivia": "591", "bosnia": "387",
    "botswana": "267", "brazil": "55", "brunei": "673", "bulgaria": "359",
    "burkina faso": "226", "burundi": "257", "cambodia": "855", "cameroon": "237",
    "canada": "1", "cape verde": "238", "cayman islands": "1345",
    "central african republic": "236", "chad": "235", "chile": "56", "china": "86",
    "colombia": "57", "comoros": "269", "congo": "242", "costa rica": "506",
    "croatia": "385", "cuba": "53", "cyprus": "357", "czech": "420", "denmark": "45",
    "djibouti": "253", "dominica": "1767", "dominican republic": "1809",
    "dr congo": "243", "ecuador": "593", "egypt": "20", "el salvador": "503",
    "equatorial guinea": "240", "eritrea": "291", "estonia": "372", "ethiopia": "251",
    "fiji": "679", "finland": "358", "france": "33", "french guiana": "594",
    "gabon": "241", "gambia": "220", "georgia": "995", "germany": "49", "ghana": "233",
    "gibraltar": "350", "greece": "30", "grenada": "1473", "guadeloupe": "590",
    "guatemala": "502", "guinea": "224", "guinea-bissau": "245", "guyana": "592",
    "haiti": "509", "honduras": "504", "hong kong": "852", "hungary": "36",
    "iceland": "354", "india": "91", "indonesia": "62", "iran": "98", "iraq": "964",
    "ireland": "353", "israel": "972", "italy": "39", "ivory coast": "225",
    "jamaica": "1876", "japan": "81", "jordan": "962", "kazakhstan": "7",
    "kenya": "254", "kosovo": "383", "kuwait": "965", "kyrgyzstan": "996",
    "laos": "856", "latvia": "371", "lebanon": "961", "lesotho": "266",
    "liberia": "231", "libya": "218", "liechtenstein": "423", "lithuania": "370",
    "luxembourg": "352", "macao": "853", "madagascar": "261", "malawi": "265",
    "malaysia": "60", "maldives": "960", "mali": "223", "malta": "356",
    "mauritania": "222", "mauritius": "230", "mexico": "52", "moldova": "373",
    "monaco": "377", "mongolia": "976", "montenegro": "382", "montserrat": "1664",
    "morocco": "212", "mozambique": "258", "myanmar": "95", "namibia": "264",
    "nepal": "977", "netherlands": "31", "new caledonia": "687", "new zealand": "64",
    "nicaragua": "505", "niger": "227", "nigeria": "234", "niue": "683",
    "north macedonia": "389", "norway": "47", "oman": "968", "pakistan": "92",
    "palestine": "970", "panama": "507", "papua": "675", "paraguay": "595",
    "peru": "51", "philippines": "63", "poland": "48", "portugal": "351",
    "puerto rico": "1787", "qatar": "974", "reunion": "262", "romania": "40",
    "russia": "7", "rwanda": "250", "saint kitts and nevis": "1869",
    "saint lucia": "1758", "saint vincent and the grenadines": "1784",
    "salvador": "503", "samoa": "685", "sao tome and principe": "239",
    "saudi arabia": "966", "senegal": "221", "serbia": "381", "seychelles": "248",
    "sierra leone": "232", "singapore": "65", "slovakia": "421", "slovenia": "386",
    "somalia": "252", "south africa": "27", "south sudan": "211", "spain": "34",
    "sri lanka": "94", "sudan": "249", "suriname": "597", "swaziland": "268",
    "sweden": "46", "switzerland": "41", "syria": "963", "taiwan": "886",
    "tajikistan": "992", "tanzania": "255", "thailand": "66", "timor-leste": "670",
    "togo": "228", "trinidad and tobago": "1868", "tunisia": "216", "turkey": "90",
    "turkmenistan": "993", "uae": "971", "uganda": "256", "ukraine": "380",
    "united kingdom": "44", "uruguay": "598", "usa": "1", "uzbekistan": "998",
    "venezuela": "58", "vietnam": "84", "yemen": "967", "zambia": "260",
    "zimbabwe": "263",
}

_COUNTRY_META: dict[int, dict] = {}
_OFFERS_CACHE: dict[str, dict] = {}
OFFERS_TTL = 45
GMAIL_PAGE_SIZE = 8
# UK, USA, KZ, Germany — always on the picker as shortcuts
GMAIL_SHORTCUTS = (16, 187, 2, 43)
GMAIL_SHORTCUT_LABELS = {16: "UK", 187: "США", 2: "KZ", 43: "DE"}

# Service codes from HeroSMS getServicesList:
# rr=Wolt, go=Google/youtube/Gmail (НЕ gm — gm это Mocospace)
SERVICES = {
    "wolt": {"code": "rr", "label": "Wolt",   "country": COUNTRY_KZ, "emoji": "📱"},
    "gmail": {"code": "go", "label": "Gmail",  "country": COUNTRY_UK, "emoji": "📧"},
}


def _calling_code(country_id, eng: str | None = None) -> str:
    try:
        cid = int(country_id)
    except (TypeError, ValueError):
        cid = None
    if cid in COUNTRY_INFO:
        return COUNTRY_INFO[cid]["prefix"]
    if eng:
        code = DIAL_BY_ENG.get(str(eng).strip().lower())
        if code:
            return code
    if cid is not None:
        meta = _COUNTRY_META.get(cid) or {}
        code = DIAL_BY_ENG.get(str(meta.get("eng") or "").strip().lower())
        if code:
            return code
    return ""


def country_info(country_id):
    try:
        cid = int(country_id)
    except (TypeError, ValueError):
        return {"code": "??", "name": "неизвестно", "dial": "", "prefix": ""}
    if cid in COUNTRY_INFO:
        return COUNTRY_INFO[cid]
    meta = _COUNTRY_META.get(cid) or {}
    prefix = _calling_code(cid, meta.get("eng"))
    name = meta.get("rus") or meta.get("eng") or f"страна {cid}"
    dial = f"+{prefix}" if prefix else ""
    return {"code": str(cid), "name": name, "dial": dial, "prefix": prefix}


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
        prefixes.append(str(prefix))
    else:
        for extra in ("44", "7"):
            prefixes.append(extra)
    for prefix in prefixes:
        if not digits.startswith(prefix) or len(digits) <= len(prefix):
            continue
        rest = digits[len(prefix):]
        if prefix == "7" and len(digits) != 11:
            continue
        if prefix == "44" and len(rest) < 9:
            continue
        if prefix == "1" and len(rest) < 10:
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
        # SQLite stores mailbox credentials and purchased-key snapshots.
        os.chmod(DB_PATH, 0o600)
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
            api_key TEXT,
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
        CREATE TABLE IF NOT EXISTS email_purchase_intents (
            user_id INTEGER PRIMARY KEY,
            token TEXT NOT NULL,
            api_key TEXT NOT NULL,
            quoted_cost REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'uncertain',
            mail_activation_id INTEGER,
            account_id INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ui_messages (
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            PRIMARY KEY (chat_id, message_id)
        );
        CREATE TABLE IF NOT EXISTS pending_number_actions (
            activation_id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('cancel', 'swap')),
            api_key TEXT NOT NULL,
            context_json TEXT NOT NULL DEFAULT '{}',
            due_at REAL NOT NULL,
            replacement_activation_id INTEGER,
            attempts INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'queued',
            created_at REAL NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE INDEX IF NOT EXISTS idx_pending_number_due
            ON pending_number_actions(status, due_at);
        """)
        c = conn.cursor()
        account_cols = {row[1] for row in c.execute("PRAGMA table_info(accounts)")}
        for name, definition in {
            "mail_provider": "TEXT NOT NULL DEFAULT 'mailtm'",
            "mail_activation_id": "INTEGER", "mail_api_key": "TEXT", "mail_cost": "REAL",
            "mail_status": "TEXT", "mail_session": "TEXT", "email_link_delivered": "INTEGER NOT NULL DEFAULT 0",
        }.items():
            if name not in account_cols:
                c.execute(f"ALTER TABLE accounts ADD COLUMN {name} {definition}")
        activation_cols = {row[1] for row in c.execute("PRAGMA table_info(activations)")}
        if "api_key" not in activation_cols:
            c.execute("ALTER TABLE activations ADD COLUMN api_key TEXT")
        action_cols = {row[1] for row in c.execute("PRAGMA table_info(pending_number_actions)")}
        if "replacement_activation_id" not in action_cols:
            c.execute("ALTER TABLE pending_number_actions ADD COLUMN replacement_activation_id INTEGER")
        if ADMIN_ID:
            c.execute("SELECT id FROM users WHERE telegram_id = ?", (ADMIN_ID,))
            if not c.fetchone():
                c.execute(
                    "INSERT INTO users (telegram_id, first_name, added_by, is_admin, use_admin_hero) VALUES (?, ?, ?, 1, 1)",
                    (ADMIN_ID, ADMIN_NAME, ADMIN_ID),
                )
            c.execute("UPDATE users SET use_admin_hero = 1 WHERE telegram_id = ? AND use_admin_hero = 0", (ADMIN_ID,))
        cols = {row[1] for row in c.execute("PRAGMA table_info(users)")}
        if "admin_key_id" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN admin_key_id INTEGER")
        c.execute("""
        CREATE TABLE IF NOT EXISTS admin_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            api_key TEXT NOT NULL UNIQUE,
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """)
        default = c.execute("SELECT id FROM admin_keys WHERE is_default = 1").fetchone()
        if default is None:
            any_key = c.execute("SELECT id FROM admin_keys ORDER BY id LIMIT 1").fetchone()
            if any_key:
                c.execute("UPDATE admin_keys SET is_default = 1 WHERE id = ?", (any_key[0],))
                default_id = any_key[0]
            elif HERO_KEY:
                c.execute(
                    "INSERT INTO admin_keys (name, api_key, is_default) VALUES (?, ?, 1)",
                    ("Основной", HERO_KEY),
                )
                default_id = c.lastrowid
            else:
                default_id = None
        else:
            default_id = default[0]
        if default_id:
            c.execute(
                "UPDATE users SET admin_key_id = ? "
                "WHERE use_admin_hero = 1 AND admin_key_id IS NULL",
                (default_id,),
            )
        conn.commit()
    finally:
        conn.close()


ADMIN_KEY_NAME_MAX = 32


def _normalize_key_name(name: str) -> str:
    cleaned = " ".join((name or "").split())
    if not cleaned:
        raise ValueError("пустое имя")
    if len(cleaned) > ADMIN_KEY_NAME_MAX:
        raise ValueError(f"имя длиннее {ADMIN_KEY_NAME_MAX} символов")
    return cleaned


def db_list_admin_keys():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT k.id, k.name, k.api_key, k.is_default, k.created_at, "
            "COUNT(u.id) AS assigned "
            "FROM admin_keys k "
            "LEFT JOIN users u ON u.admin_key_id = k.id AND u.is_active = 1 "
            "GROUP BY k.id "
            "ORDER BY k.is_default DESC, k.id ASC"
        ).fetchall()
    finally:
        conn.close()


def db_get_admin_key(key_id):
    if not key_id:
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM admin_keys WHERE id = ?", (int(key_id),)).fetchone()
    finally:
        conn.close()


def db_admin_key_secret(key_id):
    row = db_get_admin_key(key_id)
    secret = _row_get(row, "api_key")
    return secret if secret else None


def db_default_admin_key():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM admin_keys WHERE is_default = 1 ORDER BY id LIMIT 1"
        ).fetchone()
        if row:
            return row
        return conn.execute("SELECT * FROM admin_keys ORDER BY id LIMIT 1").fetchone()
    finally:
        conn.close()


def db_add_admin_key(name, api_key, make_default=False):
    cleaned = _normalize_key_name(name)
    key = (api_key or "").strip()
    if len(key) < MIN_HERO_KEY_LEN:
        raise ValueError("ключ слишком короткий")
    conn = sqlite3.connect(DB_PATH)
    try:
        existing = conn.execute("SELECT COUNT(*) FROM admin_keys").fetchone()[0]
        is_default = 1 if (make_default or existing == 0) else 0
        if is_default:
            conn.execute("UPDATE admin_keys SET is_default = 0")
        try:
            cur = conn.execute(
                "INSERT INTO admin_keys (name, api_key, is_default) VALUES (?, ?, ?)",
                (cleaned, key, is_default),
            )
        except sqlite3.IntegrityError as exc:
            msg = str(exc).lower()
            if "name" in msg:
                raise ValueError("имя уже занято") from exc
            if "api_key" in msg:
                raise ValueError("этот ключ уже есть в списке") from exc
            raise ValueError("не удалось сохранить ключ") from exc
        conn.commit()
        key_id = cur.lastrowid
    finally:
        conn.close()
    if is_default:
        persist_admin_hero_key(key)
    return key_id


def db_rename_admin_key(key_id, name):
    cleaned = _normalize_key_name(name)
    row = db_get_admin_key(key_id)
    if not row:
        raise ValueError("ключ не найден")
    conn = sqlite3.connect(DB_PATH)
    try:
        try:
            conn.execute("UPDATE admin_keys SET name = ? WHERE id = ?", (cleaned, int(key_id)))
        except sqlite3.IntegrityError as exc:
            raise ValueError("имя уже занято") from exc
        conn.commit()
    finally:
        conn.close()
    return cleaned


def db_replace_admin_key_secret(key_id, api_key):
    key = (api_key or "").strip()
    if len(key) < MIN_HERO_KEY_LEN:
        raise ValueError("ключ слишком короткий")
    row = db_get_admin_key(key_id)
    if not row:
        raise ValueError("ключ не найден")
    conn = sqlite3.connect(DB_PATH)
    try:
        dup = conn.execute(
            "SELECT name FROM admin_keys WHERE api_key = ? AND id != ?",
            (key, int(key_id)),
        ).fetchone()
        if dup:
            raise ValueError(f"этот ключ уже сохранён как «{dup[0]}»")
        conn.execute("UPDATE admin_keys SET api_key = ? WHERE id = ?", (key, int(key_id)))
        conn.commit()
    finally:
        conn.close()
    if _row_get(row, "is_default"):
        persist_admin_hero_key(key)
    return mask_hero_key(key)


def db_upsert_default_admin_secret(api_key):
    key = (api_key or "").strip()
    if len(key) < MIN_HERO_KEY_LEN:
        raise ValueError("ключ слишком короткий")
    conn = sqlite3.connect(DB_PATH)
    try:
        default = conn.execute(
            "SELECT id FROM admin_keys WHERE is_default = 1 ORDER BY id LIMIT 1"
        ).fetchone()
        if default:
            dup = conn.execute(
                "SELECT name FROM admin_keys WHERE api_key = ? AND id != ?",
                (key, default[0]),
            ).fetchone()
            if dup:
                raise ValueError(f"этот ключ уже сохранён как «{dup[0]}»")
            conn.execute("UPDATE admin_keys SET api_key = ? WHERE id = ?", (key, default[0]))
            conn.commit()
            return default[0]
        existing = conn.execute(
            "SELECT id FROM admin_keys WHERE api_key = ?", (key,)
        ).fetchone()
        if existing:
            conn.execute("UPDATE admin_keys SET is_default = 0")
            conn.execute("UPDATE admin_keys SET is_default = 1 WHERE id = ?", (existing[0],))
            conn.commit()
            return existing[0]
        cur = conn.execute(
            "INSERT INTO admin_keys (name, api_key, is_default) VALUES (?, ?, 1)",
            ("Основной", key),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def db_set_default_admin_key(key_id):
    row = db_get_admin_key(key_id)
    if not row:
        raise ValueError("ключ не найден")
    persist_admin_hero_key(_row_get(row, "api_key"))
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("UPDATE admin_keys SET is_default = 0")
        conn.execute("UPDATE admin_keys SET is_default = 1 WHERE id = ?", (int(key_id),))
        conn.commit()
    finally:
        conn.close()


def db_delete_admin_key(key_id):
    row = db_get_admin_key(key_id)
    if not row:
        raise ValueError("ключ не найден")
    if _row_get(row, "is_default"):
        raise ValueError("нельзя удалить основной — сначала назначь другой")
    conn = sqlite3.connect(DB_PATH)
    try:
        assigned = conn.execute(
            "SELECT COUNT(*) FROM users WHERE admin_key_id = ? AND is_active = 1",
            (int(key_id),),
        ).fetchone()[0]
        if assigned:
            raise ValueError(f"ключ назначен {assigned} польз. — сначала смени им ключ")
        conn.execute("DELETE FROM admin_keys WHERE id = ?", (int(key_id),))
        conn.commit()
    finally:
        conn.close()
    return _row_get(row, "name")


def db_assign_user_admin_key(telegram_id, key_id):
    conn = sqlite3.connect(DB_PATH)
    try:
        if key_id is None:
            conn.execute(
                "UPDATE users SET use_admin_hero = 0, admin_key_id = NULL WHERE telegram_id = ?",
                (int(telegram_id),),
            )
        else:
            row = db_get_admin_key(key_id)
            if not row:
                raise ValueError("ключ не найден")
            conn.execute(
                "UPDATE users SET use_admin_hero = 1, admin_key_id = ? WHERE telegram_id = ?",
                (int(key_id), int(telegram_id)),
            )
        conn.commit()
    finally:
        conn.close()


def user_assigned_key_label(user_row) -> str:
    kid = _row_get(user_row, "admin_key_id")
    if kid:
        row = db_get_admin_key(kid)
        if row:
            star = " ★" if _row_get(row, "is_default") else ""
            return f"🔵 {_row_get(row, 'name')}{star}"
    if _uses_admin_hero(user_row):
        default = db_default_admin_key()
        if default:
            return f"🔵 {_row_get(default, 'name')} ★"
        return "🔵 админский ключ"
    if _row_get(user_row, "hero_key"):
        return "🟢 свой ключ"
    return "🔴 нет ключа"


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


def db_user_is_disabled(tid):
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("SELECT is_active FROM users WHERE telegram_id = ?", (tid,)).fetchone()
        return row is not None and row[0] == 0
    finally:
        conn.close()


def db_deactivate_user(user_db_id, actor_tid):
    """Revoke access without destroying historical activations, mail or logs."""
    conn = sqlite3.connect(DB_PATH)
    try:
        target = conn.execute(
            "SELECT telegram_id, is_active FROM users WHERE id = ?", (user_db_id,)
        ).fetchone()
        if not target or target[1] != 1:
            raise ValueError("Пользователь уже удалён или не найден")
        if target[0] == ADMIN_ID or target[0] == actor_tid:
            raise ValueError("Себя и владельца бота удалить нельзя")
        conn.execute("UPDATE users SET is_active = 0 WHERE id = ? AND is_active = 1", (user_db_id,))
        conn.commit()
        return target[0]
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


def db_add_activation(user_id, activation_id, phone, cost, service, country, api_key=None):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        c = conn.execute(
            "INSERT INTO activations (user_id, activation_id, phone, service, country, cost, status, api_key) "
            "VALUES (?, ?, ?, ?, ?, ?, 'started', ?)",
            (user_id, activation_id, phone, service, country, cost, api_key or HERO_KEY),
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


def db_number_action(activation_id):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT * FROM pending_number_actions WHERE activation_id = ?", (activation_id,)
        ).fetchone()
    finally:
        conn.close()


def db_user_number_actions(user_id):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT * FROM pending_number_actions WHERE user_id = ? ORDER BY due_at",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()


def db_queue_number_action(activation_id, chat_id, kind, api_key, context=None, delay=120):
    """Atomic queue + SMS-claim fence. Repeated clicks never move the due time."""
    if kind not in ("cancel", "swap"):
        raise ValueError("Неизвестное действие")
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        old = conn.execute(
            "SELECT * FROM pending_number_actions WHERE activation_id = ?", (activation_id,)
        ).fetchone()
        if old:
            if old["chat_id"] != chat_id:
                raise ValueError("Активация не найдена")
            return old
        activation = conn.execute(
            "SELECT a.id, a.user_id, a.status, a.api_key, u.telegram_id FROM activations a "
            "JOIN users u ON u.id = a.user_id WHERE a.activation_id = ? "
            "ORDER BY a.id DESC LIMIT 1", (activation_id,),
        ).fetchone()
        if not activation or activation["telegram_id"] != chat_id:
            raise ValueError("Активация не найдена")
        if activation["status"] not in ("started", "cancel_failed"):
            raise ValueError("Номер уже завершён или ожидает SMS")
        # Separate queued rows per activation allow several swaps in succession.
        now = time.time()
        conn.execute(
            "INSERT INTO pending_number_actions "
            "(activation_id, user_id, chat_id, kind, api_key, context_json, due_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (activation_id, activation["user_id"], chat_id, kind, activation["api_key"] or api_key or HERO_KEY,
             json.dumps(context or {}, ensure_ascii=False), now + delay, now),
        )
        conn.execute(
            "UPDATE activations SET status = 'cancel_pending' WHERE id = ?",
            (activation["id"],),
        )
        account_id = (context or {}).get("account_id")
        if account_id:
            conn.execute(
                "UPDATE accounts SET status = 'cancel_pending' "
                "WHERE id = ? AND user_id = ? AND activation_id = ?",
                (account_id, activation["user_id"], activation_id),
            )
        conn.commit()
        return conn.execute(
            "SELECT * FROM pending_number_actions WHERE activation_id = ?", (activation_id,)
        ).fetchone()
    finally:
        conn.close()


def db_register_replacement(action, new, service, country):
    """Atomically associate a replacement purchase with its durable action."""
    aid = new["activationId"]
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT replacement_activation_id FROM pending_number_actions WHERE activation_id = ?",
            (action["activation_id"],),
        ).fetchone()
        if not current:
            raise ValueError("Операция смены уже закрыта")
        if current["replacement_activation_id"] is not None:
            raise ValueError("Замена уже куплена")
        conn.execute(
            "INSERT INTO activations (user_id, activation_id, phone, service, country, cost, status, api_key) "
            "VALUES (?, ?, ?, ?, ?, ?, 'started', ?)",
            (action["user_id"], aid, str(new["phoneNumber"]), service, country,
             float(new.get("activationCost") or 0), action["api_key"]),
        )
        conn.execute(
            "UPDATE pending_number_actions SET replacement_activation_id = ? WHERE activation_id = ?",
            (aid, action["activation_id"]),
        )
        account_id = json.loads(action["context_json"]).get("account_id")
        if account_id:
            conn.execute(
                "UPDATE accounts SET phone = ?, activation_id = ?, cost = ?, "
                "status = 'sms_waiting', country = ? "
                "WHERE id = ? AND user_id = ? AND activation_id = ?",
                (str(new["phoneNumber"]), aid, float(new.get("activationCost") or 0), country,
                 account_id, action["user_id"], action["activation_id"]),
            )
        conn.commit()
    finally:
        conn.close()


def db_due_number_actions(now=None):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT * FROM pending_number_actions WHERE status = 'queued' AND due_at <= ? "
            "ORDER BY due_at LIMIT 20", (time.time() if now is None else now,),
        ).fetchall()
    finally:
        conn.close()


def db_number_action_retry(activation_id, *, delay, failure=False):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "UPDATE pending_number_actions SET attempts = attempts + 1, due_at = ?, status = ? "
            "WHERE activation_id = ?",
            (time.time() + delay, "failed" if failure else "queued", activation_id),
        )
        if failure:
            conn.execute(
                "UPDATE activations SET status = 'cancel_failed' WHERE activation_id = ? "
                "AND status = 'cancel_pending'", (activation_id,),
            )
            row = conn.execute(
                "SELECT user_id, context_json FROM pending_number_actions WHERE activation_id = ?",
                (activation_id,),
            ).fetchone()
            if row:
                account_id = json.loads(row[1]).get("account_id")
                if account_id:
                    conn.execute(
                        "UPDATE accounts SET status = 'cancel_failed' "
                        "WHERE id = ? AND user_id = ? AND activation_id = ?",
                        (account_id, row[0], activation_id),
                    )
        conn.commit()
    finally:
        conn.close()


def db_number_action_finish(activation_id):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("DELETE FROM pending_number_actions WHERE activation_id = ?", (activation_id,))
        conn.commit()
    finally:
        conn.close()


def db_number_action_requeue(activation_id, chat_id):
    """Manual retry after a remote failure: another full two-minute wait."""
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM pending_number_actions WHERE activation_id = ? AND chat_id = ?",
            (activation_id, chat_id),
        ).fetchone()
        if not row or row["status"] != "failed":
            raise ValueError("Не найден неудавшийся запрос")
        conn.execute(
            "UPDATE pending_number_actions SET status = 'queued', attempts = 0, "
            "due_at = ? WHERE activation_id = ?", (time.time() + 120, activation_id),
        )
        conn.execute(
            "UPDATE activations SET status = 'cancel_pending' WHERE activation_id = ? "
            "AND status = 'cancel_failed'", (activation_id,),
        )
        account_id = json.loads(row["context_json"]).get("account_id")
        if account_id:
            conn.execute(
                "UPDATE accounts SET status = 'cancel_pending' "
                "WHERE id = ? AND user_id = ? AND activation_id = ? AND status = 'cancel_failed'",
                (account_id, row["user_id"], activation_id),
            )
        conn.commit()
        return conn.execute(
            "SELECT * FROM pending_number_actions WHERE activation_id = ?", (activation_id,)
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
            "AND status NOT IN ('completed', 'cancelled', 'cancel_pending', 'cancel_failed', 'webhook_processing')",
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


def db_successful_activation_summary(start=None, end=None, user_id=None):
    """Count and recorded USD cost of completed activations in [start, end), optionally by DB user ID."""
    conn = sqlite3.connect(DB_PATH)
    try:
        where = ["status = 'completed'"]
        args = []
        if user_id is not None:
            where.append("user_id = ?")
            args.append(user_id)
        if start is not None:
            where.append("completed_at >= ?")
            args.append(start.isoformat(timespec="seconds"))
        if end is not None:
            where.append("completed_at < ?")
            args.append(end.isoformat(timespec="seconds"))
        costs = conn.execute(
            f"SELECT cost FROM activations WHERE {' AND '.join(where)}", args
        ).fetchall()
        return len(costs), sum(
            (Decimal(str(cost)) for (cost,) in costs if cost is not None), Decimal("0")
        )
    finally:
        conn.close()


def stats_period_bounds(period, now=None):
    today = (now or datetime.datetime.now()).date()
    if period == "today":
        start, end = today, today + datetime.timedelta(days=1)
        label = "Сегодня"
    elif period == "week":
        start, end = today - datetime.timedelta(days=6), today + datetime.timedelta(days=1)
        label = "Последние 7 календарных дней"
    elif period == "month":
        start, end = today.replace(day=1), today + datetime.timedelta(days=1)
        label = "Текущий месяц"
    elif period == "all":
        return "Всё время", None, None
    else:
        raise ValueError("Неизвестный период")
    return label, datetime.datetime.combine(start, datetime.time.min), datetime.datetime.combine(end, datetime.time.min)


def parse_custom_stats_period(text):
    match = re.fullmatch(r"\s*(\d{2}\.\d{2}\.\d{4})\s*[-–— ]\s*(\d{2}\.\d{2}\.\d{4})\s*", text or "")
    if not match:
        raise ValueError("Формат: 01.09.2026 - 26.09.2026")
    try:
        start = datetime.datetime.strptime(match[1], "%d.%m.%Y").date()
        end = datetime.datetime.strptime(match[2], "%d.%m.%Y").date()
    except ValueError as exc:
        raise ValueError("Неверная дата") from exc
    if start > end:
        raise ValueError("Начало периода позже конца")
    if (end - start).days > 3660:
        raise ValueError("Период слишком длинный (максимум 10 лет)")
    return (
        f"{start:%d.%m.%Y} — {end:%d.%m.%Y}",
        datetime.datetime.combine(start, datetime.time.min),
        datetime.datetime.combine(end + datetime.timedelta(days=1), datetime.time.min),
    )


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
    "mail_provider", "mail_activation_id", "mail_api_key", "mail_cost",
    "mail_status", "mail_session", "email_link_delivered",
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


def db_email_intent(user_id):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM email_purchase_intents WHERE user_id=?", (user_id,)).fetchone()
    finally:
        conn.close()


def db_begin_email_intent(user_id, token, api_key, cost):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT status FROM email_purchase_intents WHERE user_id=?", (user_id,)).fetchone()
        if row and row[0] == "uncertain":
            raise HeroEmailError("Требуется ручная сверка HeroSMS")
        conn.execute(
            "INSERT OR REPLACE INTO email_purchase_intents (user_id,token,api_key,quoted_cost,status) "
            "VALUES (?,?,?,?,'uncertain')", (user_id, token, api_key, cost),
        )
        conn.commit()
    finally:
        conn.close()


def db_add_hero_account(user_id, data, api_key):
    data = _validate_hero_email(data)
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "INSERT INTO accounts (user_id,email,password,service,country,status,mail_provider,"
            "mail_activation_id,mail_api_key,mail_cost,mail_status,mail_session) "
            "VALUES (?,?,'','rr',?,'email_waiting','herosms',?,?,?,?,'waiting')",
            (user_id, data["email"], COUNTRY_KZ, data["id"], api_key, float(data["cost"]), data["status"]),
        )
        account_id = cur.lastrowid
        conn.execute("UPDATE email_purchase_intents SET status='saved',mail_activation_id=?,account_id=? WHERE user_id=?",
                     (data["id"], account_id, user_id))
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
    """Mail.tm inboxes of a user, newest first; limit=None reads all for bulk ops."""
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        if limit is None:
            return conn.execute(
                "SELECT * FROM accounts WHERE user_id = ? ORDER BY id DESC", (user_id,)
            ).fetchall()
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


HERO_EMAIL_BASE = "https://hero-sms.com/api/v1"


class HeroEmailError(ValueError):
    """Only fixed secret-safe messages; never expose provider bodies/URLs."""


def _hero_email_request(method, path, api_key, **kwargs):
    key = str(api_key or "").strip()
    if key.startswith("ApiKey "):
        key = key[7:].strip()
    if not key or key.startswith("ApiKey "):
        raise HeroEmailError("HeroSMS: неверный ключ")
    try:
        response = getattr(httpx, method.lower())(
            HERO_EMAIL_BASE + path, headers={"Authorization": "ApiKey " + key},
            timeout=MAIL_HTTP_TIMEOUT, **kwargs,
        )
        if method.upper() == "POST":
            log.info("HeroSMS email POST response: http_status=%d", response.status_code)
        if response.status_code not in {"GET": (200,), "POST": (200, 201), "DELETE": (204,)}[method.upper()]:
            raise HeroEmailError("HeroSMS: запрос отклонён")
        if response.status_code == 204:
            return None
        payload = response.json()
        if method.upper() == "POST":
            data = payload.get("data") if isinstance(payload, dict) else None
            fields = {name: type(data.get(name)).__name__ for name in
                      ("id", "site", "email", "status", "cost", "currency", "value", "message")} if isinstance(data, dict) else {}
            log.info("HeroSMS email POST schema: data_type=%s field_types=%s", type(data).__name__, fields)
        if not isinstance(payload, dict) or "data" not in payload:
            raise HeroEmailError("HeroSMS: неверный формат ответа")
        return payload["data"]
    except HeroEmailError:
        raise
    except Exception:
        raise HeroEmailError("HeroSMS: ответ не подтверждён") from None


def _validate_hero_email(data, expected_id=None, expected_email=None):
    try:
        if not isinstance(data, dict) or type(data["id"]) is not int or data["id"] <= 0:
            raise ValueError
        if expected_id is not None and data["id"] != int(expected_id):
            raise ValueError
        if data["site"] != "wolt.com" or not re.fullmatch(r"[a-zA-Z0-9._%+\-]+@outlook\.com", data["email"]):
            raise ValueError
        if expected_email is not None and data["email"] != expected_email:
            raise ValueError
        if data["status"] not in ("WAIT", "SUCCESS", "CANCEL", "CANCELLED", "CANCELED"):
            raise ValueError
        if type(data["currency"]) is not int or data["currency"] != 840:
            raise ValueError
        cost = Decimal(str(data["cost"]))
        if not cost.is_finite() or cost < 0 or isinstance(data["cost"], bool):
            raise ValueError
        if (data.get("message") is not None and not isinstance(data["message"], str)
                or data.get("value") is not None and not isinstance(data["value"], str)):
            raise ValueError
        return data
    except (ValueError, TypeError, KeyError, InvalidOperation):
        raise HeroEmailError("HeroSMS: неверная Outlook активация") from None


def hero_email_buy(api_key):
    # Never retry: a timeout/non-JSON response can still have charged the key.
    return _validate_hero_email(_hero_email_request(
        "POST", "/emails", api_key, json={"site": "wolt.com", "domain": "outlook.com"},
    ))


def hero_email_get(activation_id, api_key):
    if type(activation_id) is not int or activation_id <= 0:
        raise HeroEmailError("HeroSMS: неверный ID почты")
    return _validate_hero_email(_hero_email_request("GET", f"/emails/{activation_id}", api_key), activation_id)


def _hero_wolt_link(data):
    link = mail_extract_link({"html": data.get("message", ""), "text": data.get("value") or ""})
    try:
        parsed = urlsplit(link or "")
        if parsed.scheme == "https" and parsed.hostname in ("wolt.com", "www.wolt.com") and not parsed.username and not parsed.password and parsed.port in (None, 443):
            return link
    except ValueError:
        pass
    return None


def hero_email_delete(activation_id, api_key):
    if type(activation_id) is not int or activation_id <= 0:
        raise HeroEmailError("HeroSMS: неверный ID почты")
    _hero_email_request("DELETE", f"/emails/{activation_id}", api_key)


async def _cancel_hero_email(account):
    """Explicit cancel only; SUCCESS is consumed, never cancel/reorder it."""
    terminal = ("CANCEL", "CANCELLED", "CANCELED")
    if account["mail_status"] in ("SUCCESS", *terminal):
        return
    aid, key = account["mail_activation_id"], account["mail_api_key"]
    data = await asyncio.to_thread(hero_email_get, aid, key)
    _validate_hero_email(data, aid, account["email"])
    db_update_account(account["id"], mail_status=data["status"])
    if data["status"] == "SUCCESS" or data["status"] in terminal:
        return
    await asyncio.to_thread(hero_email_delete, aid, key)
    readback = await asyncio.to_thread(hero_email_get, aid, key)
    _validate_hero_email(readback, aid, account["email"])
    db_update_account(account["id"], mail_status=readback["status"])
    if readback["status"] not in terminal:
        raise HeroEmailError("Отмена почты не подтверждена; запись сохранена")


async def _delete_remote_mail(account):
    if _row_get(account, "mail_provider") == "herosms":
        # The durable purchase intent must survive stale snapshots and restarts.
        account = db_get_account(account["id"], account["user_id"])
        if not account:
            raise HeroEmailError("Почта не найдена; удаление не подтверждено")
        if account["mail_session"] == "sms_buying":
            raise HeroEmailError("Покупка SMS не подтверждена; почта сохранена, нужна ручная сверка HeroSMS")
        if account["activation_id"] and account["status"] != "completed":
            sms = db_get_activation_for_webhook(account["activation_id"])
            if not sms or sms["user_id"] != account["user_id"] or sms["status"] not in ("completed", "cancelled"):
                raise HeroEmailError("Статус/владелец SMS не подтверждён; почта сохранена")
        if not account["activation_id"] and account["status"] != "completed":
            db_update_account(account["id"], status="cancelled", mail_session="delete_requested")
        try:
            await _cancel_hero_email(account)
        except Exception:
            raise HeroEmailError("HeroSMS не подтвердил отмену; активация сохранена, нужна ручная проверка") from None
        return
    token = account["mail_token"]
    try:
        await asyncio.to_thread(mail_delete, account["mail_tm_id"], token)
    except MailTMAuthError:
        token = await asyncio.to_thread(mail_login, account["email"], account["password"])
        await asyncio.to_thread(mail_delete, account["mail_tm_id"], token)


def hero_email_offer(api_key):
    domains = _hero_email_request("GET", "/emails/domains", api_key, params={"site": "wolt.com"})
    if not isinstance(domains, list):
        raise HeroEmailError("HeroSMS: неверный список доменов")
    for row in domains:
        if isinstance(row, dict) and row.get("name") == "outlook.com":
            try:
                cost = float(row["cost"])
                count = row["count"]
                if isinstance(row["cost"], bool) or not Decimal(str(cost)).is_finite() or cost < 0 or type(count) is not int or count < 0:
                    raise ValueError
                return cost, count
            except (ValueError, TypeError, KeyError):
                raise HeroEmailError("HeroSMS: неверная цена Outlook") from None
    return 0.0, 0


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


def hero_countries(api_key=None) -> dict[int, dict]:
    """Live HeroSMS country list; fills _COUNTRY_META for labels/prefixes."""
    data = _hero_json("getCountries", api_key=api_key)
    out: dict[int, dict] = {}
    if not isinstance(data, dict):
        return out
    for key, meta in data.items():
        if not isinstance(meta, dict):
            continue
        try:
            cid = int(meta.get("id", key))
        except (TypeError, ValueError):
            continue
        eng = str(meta.get("eng") or "")
        rus = str(meta.get("rus") or "")
        out[cid] = {"id": cid, "eng": eng, "rus": rus, "prefix": _calling_code(cid, eng)}
    _COUNTRY_META.update(out)
    return out


def hero_service_offers(service_code, api_key=None, force=False):
    """Countries that currently have stock for a service, cheapest first."""
    cache_key = f"{service_code}:{api_key or 'admin'}"
    now = time.time()
    hit = _OFFERS_CACHE.get(cache_key)
    if not force and hit and now - hit["ts"] < OFFERS_TTL:
        return hit["offers"]
    countries = hero_countries(api_key=api_key)
    prices = _hero_json("getPrices", api_key=api_key, service=service_code)
    if not isinstance(prices, dict):
        prices = {}
    offers = []
    for cid, meta in countries.items():
        block = prices.get(str(cid)) or prices.get(cid) or {}
        if not isinstance(block, dict):
            continue
        info = block.get(service_code)
        if not isinstance(info, dict):
            continue
        count = int(info.get("count") or 0)
        if count <= 0:
            continue
        offers.append({
            "id": cid,
            "eng": meta["eng"],
            "rus": meta["rus"],
            "prefix": meta["prefix"],
            "cost": float(info.get("cost") or 0),
            "count": count,
        })
    offers.sort(key=lambda item: (item["cost"], -item["count"], item["rus"] or item["eng"]))
    _OFFERS_CACHE[cache_key] = {"ts": now, "offers": offers}
    return offers


def _fmt_stock(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "0"
    if n >= 1_000_000:
        text = f"{n / 1_000_000:.1f}м"
        return text.replace(".0м", "м")
    if n >= 1000:
        return f"{n // 1000}к"
    return str(n)


def _fmt_cost(cost) -> str:
    try:
        value = float(cost)
    except (TypeError, ValueError):
        return "$?"
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    if "." not in text:
        text += ".00"
    return f"${text}"


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


CANCEL_DELAY_SECONDS = 120
CANCEL_RETRY_SECONDS = 20
CANCEL_MAX_ATTEMPTS = 12
_pending_number_states = {}
_dp = None


def _number_pending_text(action):
    label = "прежнего номера" if action["kind"] == "swap" else "номера"
    if action["status"] == "failed":
        return (f"⚠️ HeroSMS не подтвердил отмену {label}. Деньги могли не вернуться.\n"
                "Новый номер не блокируется; отмену можно повторить через 2 минуты.")
    due = datetime.datetime.fromtimestamp(action["due_at"]).strftime("%H:%M:%S")
    return (f"⏳ Запланирована отмена {label}.\n\n"
            f"Запрос HeroSMS — не раньше {due} (время сервера), через 2 минуты после нажатия.\n"
            "Новые номера можно получать сразу; отмена идёт в фоне.")


def _number_pending_kb(action):
    rows = []
    if action["status"] == "failed":
        rows.append([InlineKeyboardButton(
            text="🔄 Повторить отмену через 2 мин",
            callback_data=f"number_retry_cancel:{action['activation_id']}",
        )])
    rows.append([InlineKeyboardButton(text="🔄 Проверить статус", callback_data="number_pending_status")])
    rows.append([InlineKeyboardButton(text="📍 Получить другой номер", callback_data="back_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _cancel_hero_activation(activation_id, api_key=None, account_id=None):
    """Durably schedule cancellation; never mark a failed API request as refunded."""
    row = db_get_activation_for_webhook(activation_id)
    if not row:
        log.warning("Cannot schedule cancellation for missing activation %s", activation_id)
        return None
    try:
        return db_queue_number_action(
            activation_id, row["telegram_id"], "cancel", api_key,
            {"account_id": account_id} if account_id else {}, delay=CANCEL_DELAY_SECONDS,
        )
    except ValueError as exc:
        log.warning("Cannot schedule cancellation for %s: %s", activation_id, exc)
        return None


async def _pending_fsm(chat_id):
    state = _pending_number_states.get(chat_id)
    if state is None and _dp is not None and _bot is not None:
        state = await _dp.fsm.get_context(bot=_bot, chat_id=chat_id, user_id=chat_id)
    return state


async def _finish_number_action(action, *, state=None):
    """Record confirmed remote cancellation; never touch a newer active number."""
    aid, chat_id = action["activation_id"], action["chat_id"]
    context = json.loads(action["context_json"])
    account_id = context.get("account_id")
    db_update_activation(aid, status="cancelled", completed_at=datetime.datetime.now().isoformat())
    if account_id:
        account = db_get_account(account_id, action["user_id"])
        if account and account["activation_id"] == aid:
            db_update_account(account_id, status="cancelled")
    db_number_action_finish(aid)
    log.info("Background HeroSMS cancellation confirmed for activation %s", aid)
    # A swap's replacement was already issued at click-time; no purchase here.
    if action["kind"] == "swap" or db_user_is_disabled(chat_id):
        return
    state = state or await _pending_fsm(chat_id)
    if not state or await state.get_state() != RegState.cancelling.state:
        return
    remaining = _pending_number_for_user(db_get_user(chat_id))
    if remaining:
        await _safe_send(chat_id, "✅ Один из номеров отменён.\n" + _number_pending_text(remaining),
                         reply_markup=_number_pending_kb(remaining))
    else:
        await state.clear()
        _pending_number_states.pop(chat_id, None)
        await _safe_send(chat_id, "✅ HeroSMS подтвердил отмену номера.",
                         reply_markup=menu_kb(None, is_admin=_is_admin(db_get_user(chat_id))))


async def _notify_cancel_failure(action, *, state=None):
    """Warn about balance risk without replacing the current-number panel."""
    chat_id = action["chat_id"]
    if db_user_is_disabled(chat_id):
        return
    state = state or await _pending_fsm(chat_id)
    if state and await state.get_state() == RegState.cancelling.state:
        await _safe_send(chat_id, _number_pending_text(action),
                         reply_markup=_number_pending_kb(action))
        return
    activation = db_get_activation_for_webhook(action["activation_id"])
    number = phone_local(activation["phone"], activation["country"]) if activation else str(action["activation_id"])
    await _safe_send_raw(chat_id,
                         f"⚠️ HeroSMS не подтвердил отмену прежнего номера {number}. "
                         "Расход мог сохраниться. В боте открой «Отмены номеров» и повтори отмену.")


async def _process_number_action(action, *, state=None):
    """Single due job. Never send CANCEL before due_at, even after restart."""
    if action["status"] != "queued" or time.time() < action["due_at"]:
        return
    aid, chat_id = action["activation_id"], action["chat_id"]
    async with _chat_lock(chat_id):
        fresh = db_number_action(aid)
        if not fresh or fresh["status"] != "queued" or time.time() < fresh["due_at"]:
            return
        activation = db_get_activation_for_webhook(aid)
        if activation and activation["status"] in ("completed", "webhook_processing"):
            db_number_action_finish(aid)
            state = state or await _pending_fsm(chat_id)
            if state and await state.get_state() == RegState.cancelling.state:
                await state.clear()
                _pending_number_states.pop(chat_id, None)
                await _safe_send(chat_id, "ℹ️ Номер уже получил SMS; отмена не выполнялась.",
                                 reply_markup=menu_kb(None, is_admin=_is_admin(db_get_user(chat_id))))
            return
        try:
            confirmed = bool(activation and activation["status"] == "cancelled")
            if not confirmed:
                try:
                    before = await asyncio.to_thread(_hero, "getStatus", api_key=fresh["api_key"], id=aid)
                    confirmed = before == "STATUS_CANCEL"
                except Exception:
                    pass
            if not confirmed:
                result = await asyncio.to_thread(
                    _hero, "setStatus", api_key=fresh["api_key"], id=aid, status=HERO_CANCEL,
                )
                if result != "ACCESS_CANCEL":
                    raise ValueError("HeroSMS не подтвердил отмену")
                after = await asyncio.to_thread(_hero, "getStatus", api_key=fresh["api_key"], id=aid)
                if after != "STATUS_CANCEL":
                    raise ValueError("HeroSMS не подтвердил статус STATUS_CANCEL")
            await _finish_number_action(fresh, state=state)
        except Exception as exc:
            if db_number_action(aid) is None:
                log.error("Number action finished but delivery failed for %s: %s", aid, type(exc).__name__)
                return
            failure = fresh["attempts"] + 1 >= CANCEL_MAX_ATTEMPTS
            db_number_action_retry(aid, delay=CANCEL_RETRY_SECONDS, failure=failure)
            error_code = ("EARLY_CANCEL_DENIED" if "EARLY_CANCEL_DENIED" in str(exc)
                          else type(exc).__name__)
            log.warning("HeroSMS cancel unconfirmed for %s (attempt %d): %s",
                        aid, fresh["attempts"] + 1, error_code)
            if failure:
                latest = db_number_action(aid)
                await _notify_cancel_failure(latest, state=state)


async def _number_action_worker():
    """Periodic durable queue: one coroutine; no blocked handler or event loop."""
    while True:
        try:
            for action in db_due_number_actions():
                await _process_number_action(action)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Pending HeroSMS action worker failed")
        await asyncio.sleep(5)


def _pending_number_for_user(user):
    actions = db_user_number_actions(user["id"]) if user else []
    return actions[0] if actions else None


async def _purchase_number(service_code, country, api_key=None, *, owner_chat=None, account_id=None, can_buy=None) -> dict:
    """Live price + balance + getNumberV2 with retries + setStatus READY.

    Returns the HeroSMS activation dict. Raises ValueError on failure.
    Cancels the number if READY fails after a successful buy.
    """
    current_balance = await asyncio.to_thread(_hero_balance, api_key)
    live_cost, live_avail = await asyncio.to_thread(hero_get_price, service_code, country, api_key)
    max_price = round(live_cost + PRICE_MARGIN, 4)
    if can_buy is not None and not await can_buy():
        raise ValueError("Сессия изменилась; номер не покупался")
    if current_balance < max_price:
        raise ValueError(
            f"Недостаточно средств: баланс ${current_balance:.2f}, нужно ${max_price:.4f}"
        )
    if live_avail <= 0:
        raise ValueError("Сейчас нет доступных номеров")

    activation = None
    last_error = None
    for attempt in range(MAX_BUY_ATTEMPTS):
        if can_buy is not None and not await can_buy():
            raise ValueError("Сессия изменилась; номер не покупался")
        try:
            activation = await asyncio.to_thread(
                hero_buy_number, service_code, country, max_price, api_key,
            )
            break
        except Exception as exc:
            last_error = exc
            log.warning("getNumberV2 attempt %s maxPrice=%s: %s", attempt + 1, max_price, type(exc).__name__ if can_buy is not None else exc)
            try:
                live_cost, live_avail = await asyncio.to_thread(
                    hero_get_price, service_code, country, api_key,
                )
                max_price = round(live_cost + PRICE_MARGIN * (attempt + 2), 4)
            except Exception as price_error:
                log.warning("price refresh on retry failed: %s", type(price_error).__name__ if can_buy is not None else price_error)
            if attempt < MAX_BUY_ATTEMPTS - 1:
                await asyncio.sleep(2 + attempt)

    if not activation:
        raise ValueError(str(last_error or "NO_NUMBERS"))

    aid = activation["activationId"]
    try:
        ready_raw = await asyncio.to_thread(
            _hero, "setStatus", api_key=api_key, id=aid, status=HERO_READY,
        )
        if ready_raw != "ACCESS_READY":
            raise ValueError("HeroSMS не подтвердил готовность номера")
    except Exception as exc:
        log.warning("setStatus READY failed for activation %s: %s", aid, type(exc).__name__ if can_buy is not None else exc)
        if owner_chat is not None:
            owner = db_get_user(owner_chat)
            if owner:
                if not db_get_activation_for_webhook(aid):
                    db_add_activation(owner["id"], aid, str(activation["phoneNumber"]),
                                      float(activation.get("activationCost") or live_cost),
                                      service_code, country, api_key=api_key)
                try:
                    db_queue_number_action(
                        aid, owner_chat, "cancel", api_key,
                        {"account_id": account_id} if account_id else {},
                        delay=CANCEL_DELAY_SECONDS,
                    )
                except Exception:
                    log.exception("Cannot queue READY-failed activation %s", aid)
        raise ValueError("HeroSMS не активировал номер; отмена запланирована через 2 минуты, если покупка записана") from exc

    if "activationCost" not in activation:
        activation["activationCost"] = live_cost
    return activation


def get_user_hero_key(user_row):
    kid = _row_get(user_row, "admin_key_id")
    if kid:
        secret = db_admin_key_secret(kid)
        if secret:
            return secret
    if _uses_admin_hero(user_row):
        default = db_default_admin_key()
        secret = _row_get(default, "api_key") if default else None
        return secret  # None → _hero() uses env HERO_KEY
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
    cancelling = State()
    completed = State()


class StatsState(StatesGroup):
    waiting_custom = State()

class AdminState(StatesGroup):
    add_user_wait = State()
    rm_user_wait = State()
    waiting_admin_hero_key = State()
    waiting_admin_key_name = State()
    waiting_admin_key_rename = State()
    waiting_stats_period = State()


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
                tracked = db_ui_list(chat_id)
                if tracked and mid not in tracked:
                    # A delayed click on a deleted panel must not purge the newer number.
                    if isinstance(event, CallbackQuery):
                        await event.answer("Экран устарел. Открой /start", show_alert=True)
                    return None
                await _purge_ui(chat_id, keep=mid)
                db_ui_add(chat_id, mid)
            except Exception as exc:
                log.warning("ui purge on callback failed: %s", exc)
        return await handler(event, data)


router.callback_query.middleware(UiCleanupMiddleware())


class DisabledUserMiddleware(BaseMiddleware):
    """Deactivated users cannot reuse old buttons or start a fresh session."""

    async def __call__(self, handler, event, data):
        sender = getattr(event, "from_user", None)
        if sender and db_user_is_disabled(sender.id):
            if isinstance(event, CallbackQuery):
                await event.answer("Доступ отключён администратором", show_alert=True)
            elif isinstance(event, Message):
                await event.answer("Доступ к боту отключён администратором.", parse_mode=None)
            return None
        return await handler(event, data)




router.callback_query.outer_middleware(DisabledUserMiddleware())
router.message.outer_middleware(DisabledUserMiddleware())


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


def menu_kb(current_state=None, is_admin=False, pending_account_id=None, extra_rows=None, combo=None, activation_id=None):
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
        kb.append([InlineKeyboardButton(text="🔄 Сменить номер", callback_data=f"change_number:{activation_id}" if activation_id else "change_number")])
        kb.append([InlineKeyboardButton(text="❌ Отменить", callback_data="cancel")])
    elif st in (RegState.cancelling, RegState.cancelling.state, "RegState:cancelling"):
        kb.append([InlineKeyboardButton(text="⏳ Проверить отмену", callback_data="number_pending_status")])
    elif st in (RegState.completed, RegState.completed.state, "RegState:completed"):
        if combo == "gmail_then_wolt":
            kb.append([InlineKeyboardButton(text="📱 Дальше: Wolt", callback_data="combo_wolt")])
            kb.append([InlineKeyboardButton(text="✅ Завершить без Wolt", callback_data="complete")])
        else:
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
    kb.append([InlineKeyboardButton(text="⏳ Отмены номеров", callback_data="number_pending_status")])
    kb.append([InlineKeyboardButton(text="📈 Моя статистика", callback_data="user_stats")])
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
        lines.append("Старый номер ждёт отмены в фоне (запрос через 2 мин). Введи <b>новый</b> в сервисе — жду SMS.")
    else:
        lines.append("Введи номер в сервисе. Если не подходит — «Сменить номер».")
    return "\n".join(lines)


def _session_markup(current_state, is_admin=False, link=None, pending_account_id=None, combo=None, activation_id=None):
    return menu_kb(
        current_state,
        is_admin=is_admin,
        pending_account_id=pending_account_id,
        extra_rows=_link_button_rows(link),
        combo=combo,
        activation_id=activation_id,
    )


def service_choice_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📧 Wolt: почта + SMS (KZ +7)", callback_data="start_wolt")],
        [InlineKeyboardButton(text="📧 Wolt: Outlook + SMS (HeroSMS)", callback_data="start_wolt_outlook")],
        [InlineKeyboardButton(text="📲 Wolt: только SMS (KZ +7)", callback_data="start_wolt_sms")],
        [InlineKeyboardButton(text="📧 Gmail (выбор страны)", callback_data="start_gmail")],
        [InlineKeyboardButton(text="🔗 Google → Wolt SMS", callback_data="start_combo")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_menu")],
    ])


def _gmail_offer_button(offer) -> InlineKeyboardButton:
    name = offer.get("rus") or offer.get("eng") or str(offer.get("id"))
    if len(name) > 16:
        name = name[:15] + "…"
    text = f"{name} · {_fmt_cost(offer.get('cost'))} · {_fmt_stock(offer.get('count'))}"
    return InlineKeyboardButton(text=text[:64], callback_data=f"gmail_c:{int(offer['id'])}")


def gmail_countries_kb(offers, page=0, page_size=GMAIL_PAGE_SIZE):
    """Paginated country picker. Returns (markup, page, pages, total)."""
    offers = list(offers or [])
    by_id = {int(item["id"]): item for item in offers}
    rows = []
    shortcuts = []
    for cid in GMAIL_SHORTCUTS:
        item = by_id.get(int(cid))
        if not item:
            continue
        label = GMAIL_SHORTCUT_LABELS.get(int(cid), str(cid))
        shortcuts.append(InlineKeyboardButton(
            text=f"{label} {_fmt_cost(item['cost'])}",
            callback_data=f"gmail_c:{int(cid)}",
        ))
    if shortcuts:
        rows.append(shortcuts)

    total = len(offers)
    pages = max(1, (total + page_size - 1) // page_size) if page_size else 1
    page = max(0, min(int(page or 0), pages - 1))
    chunk = offers[page * page_size:(page + 1) * page_size]
    for item in chunk:
        rows.append([_gmail_offer_button(item)])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"gmail_p:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data=f"gmail_p:{page}"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"gmail_p:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton(text="🔄 Обновить цены", callback_data="gmail_refresh"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="choose_service"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows), page, pages, total


def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users:0")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="➕ Добавить пользователя", callback_data="admin_add_user")],
        [InlineKeyboardButton(text="➖ Удалить пользователя", callback_data="admin_rm_user")],
        [InlineKeyboardButton(text="📜 История активаций", callback_data="admin_history:0")],
        [InlineKeyboardButton(text="📋 Логи", callback_data="admin_logs:0")],
        [InlineKeyboardButton(text="🔑 Управление ключами", callback_data="admin_keys")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")],
    ])


# ═══════════════════════════════════════════════════════════
#  /start
# ═══════════════════════════════════════════════════════════

@router.message(Command("start"))
async def cmd_start(msg: Message, state: FSMContext):
    if db_user_is_disabled(msg.from_user.id):
        await msg.answer("Доступ к боту отключён администратором.", parse_mode=None)
        return
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
    if _chat_lock(msg.from_user.id).locked():
        await msg.answer("⏳ Операция с номером ещё идёт. Попробуй /start через несколько секунд.")
        return
    current = await state.get_state()
    if current == RegState.cancelling.state:
        # The queued cancellation is durable; it never blocks a fresh purchase.
        await state.clear()
        _pending_number_states.pop(msg.from_user.id, None)
        current = None
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
        statuses=("email_waiting", "email_timeout", "email_error", "email_received", "number_error", "sms_waiting"),
    ) if current is None else None
    # pass raw state string — State.__eq__ accepts it; RegState(str) is invalid
    session_data = await state.get_data() if current == RegState.waiting_sms.state else {}
    current_phone = phone_local(session_data.get("phone"), session_data.get("country")) if session_data else ""
    active_line = (f"\n\nТекущий номер: <code>{html_lib.escape(current_phone)}</code>" if current_phone else "")
    await _replace_ui(
        msg.chat.id,
        "<b>🤖 SMS Bot</b>\n\n"
        "Wolt: Казахстан (+7). Gmail: любая страна, живая цена.\n"
        "Wolt или Gmail — на выбор.\n\n"
        "Перед покупкой покажу <b>актуальную цену</b> и баланс." + active_line,
        reply_markup=menu_kb(
            current,
            is_admin=is_admin,
            pending_account_id=pending["id"] if pending else None,
            activation_id=session_data.get("activation_id"),
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

        await state.update_data(combo=None, outlook_confirm=None, outlook_quote_request=None)
        await cb.message.edit_text(
            "📍 <b>Выбери регистрацию</b>\n\n"
            "• <b>Wolt: почта + SMS</b> — Mail.tm и номер KZ.\n"
            "• <b>Wolt: только SMS</b> — сразу номер KZ, без почты.\n"
            "• <b>Gmail</b> — номер выбранной страны.\n"
            "• <b>Google → Wolt SMS</b> — сначала SMS Google, после кода — SMS Wolt.",
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
    sms_key = get_user_hero_key(user_row)
    if _row_get(account, "mail_provider") == "herosms" and account["activation_id"]:
        activation = db_get_activation_for_webhook(account["activation_id"])
        if activation and activation["user_id"] == user_row["id"]:
            sms_key = _row_get(activation, "api_key") or sms_key
    await state.update_data(
        mail_provider=_row_get(account, "mail_provider", "mailtm"),
        mail_activation_id=_row_get(account, "mail_activation_id"),
        mail_api_key=_row_get(account, "mail_api_key"),
        mail_cost=_row_get(account, "mail_cost"),
        mail_watch_id=secrets.token_hex(12),
        service_key="wolt",
        service_code="rr",
        service_label="Wolt",
        service_emoji="📱",
        hero_key=sms_key,
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
    return await _replace_ui(chat_id, text, reply_markup=markup)


async def _start_sms_watch(chat_id, state):
    watch_id = secrets.token_hex(8)
    await state.update_data(sms_watch_id=watch_id)
    asyncio.create_task(_wait_sms(chat_id, state))


async def _wolt_buy_number(chat_id, state):
    data = await state.get_data()
    if data.get("mail_provider") != "herosms":
        return await _wolt_buy_number_core(chat_id, state)
    async with _chat_lock(chat_id):
        data = await state.get_data()
        user = db_get_user(chat_id)
        account = db_get_account(data.get("account_id"), user["id"]) if user else None
        if (not account or account["activation_id"] or account["mail_session"] == "sms_buying"
                or not account["email_link_delivered"] or not account["email_link"]
                or account["email_link"] != data.get("email_link")
                or not _hero_wolt_link({"value": account["email_link"]})
                or not await _hero_mail_watch_alive(chat_id, state, account["id"], data.get("mail_watch_id"))):
            return
        # Crash between external SMS purchase and persistence must not auto-rebuy.
        db_update_account(account["id"], mail_session="sms_buying")
        await _wolt_buy_number_core(chat_id, state)
        fresh = db_get_account(account["id"], user["id"])
        if fresh:
            db_update_account(account["id"], mail_session="sms_issued" if fresh["activation_id"] else "number_error")


async def _wolt_buy_number_core(chat_id, state):
    """Existing number stage; HeroSMS mail wrapper additionally guards delivery."""
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
    if data.get("mail_provider") == "herosms" and not await _hero_mail_watch_alive(chat_id, state, account_id, data.get("mail_watch_id")):
        return
    existing_cancels = {a["activation_id"] for a in db_user_number_actions(user_row["id"])}
    try:
        extra = {}
        if data.get("mail_provider") == "herosms":
            async def can_buy():
                return await _hero_mail_watch_alive(chat_id, state, account_id, data.get("mail_watch_id"))
            extra["can_buy"] = can_buy
        activation = await _purchase_number("rr", country, hk, owner_chat=chat_id, account_id=account_id, **extra)
    except Exception as exc:
        if data.get("mail_provider") == "herosms" and not await _hero_mail_watch_alive(chat_id, state, account_id, data.get("mail_watch_id")):
            return
        # An older background cancellation must not hijack this fresh purchase.
        new_cancel = any(a["activation_id"] not in existing_cancels
                         for a in db_user_number_actions(user_row["id"]))
        db_update_account(account_id, status="number_error", cost=data.get("cost"))
        await state.clear()
        detail = str(exc)
        public = (detail if isinstance(exc, ValueError) and len(detail) <= 160
                  and not any(x in detail.lower() for x in ("api_key", "apikey", "authorization", "http"))
                  else type(exc).__name__)
        if data.get("mail_provider") == "herosms":
            public = "HeroSMS не подтвердил покупку/готовность номера"
        await _safe_send(
            chat_id,
            f"❌ Не удалось купить номер Wolt: {html_lib.escape(public)}"
            + ("\n⏳ Купленный номер поставлен на отложенную отмену." if new_cancel else ""),
            reply_markup=_wolt_retry_kb(account_id, "number"),
        )
        return

    aid = activation["activationId"]
    phone = str(activation["phoneNumber"])
    phone_short = phone_local(phone, country)
    actual_cost = float(activation.get("activationCost") or data.get("cost") or 0)
    db_add_activation(data["db_user_id"], aid, phone, actual_cost, "rr", country, api_key=hk)
    if data.get("mail_provider") == "herosms" and not await _hero_mail_watch_alive(chat_id, state, account_id, data.get("mail_watch_id")):
        db_update_account(account_id, activation_id=aid, phone=phone, cost=actual_cost)
        db_queue_number_action(aid, chat_id, "cancel", hk, {"account_id": account_id}, delay=CANCEL_DELAY_SECONDS)
        return
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
        reply_markup=_session_markup(RegState.waiting_sms, is_admin=is_admin, link=link, activation_id=aid),
    )
    await _start_sms_watch(chat_id, state)


async def _show_confirm(cb: CallbackQuery, state: FSMContext, service_key: str, country=None):
    """Show price + balance and ask for confirmation."""
    try:
        user_row = db_get_user(cb.from_user.id)
        if not user_row: return
        svc = SERVICES[service_key]
        hk = get_user_hero_key(user_row)

        await cb.message.edit_text("⏳ Проверяю цены...")
        await cb.answer()

        if country is None:
            country = int(svc["country"])
        else:
            country = int(country)
        if country not in COUNTRY_INFO and country not in _COUNTRY_META:
            try:
                hero_countries(api_key=hk)
            except Exception:
                pass
        cost, avail = hero_get_price(svc["code"], country, api_key=hk)
        balance = _hero_balance(api_key=hk)
        max_price = cost + PRICE_MARGIN

        data = await state.get_data()
        combo = data.get("combo")
        if service_key == "gmail":
            back_cb = "start_combo" if combo == "gmail_then_wolt" else "start_gmail"
            back_label = "🔙 К странам"
            refresh_cb = f"gmail_c:{country}"
        elif service_key == "wolt":
            back_cb = "choose_service"
            back_label = "🔙 Назад к выбору"
            refresh_cb = "start_wolt_sms"
        else:
            back_cb = "choose_service"
            back_label = "🔙 Назад к выбору"
            refresh_cb = f"start_{service_key}"

        combo_block = ""
        if combo == "gmail_then_wolt" and service_key == "gmail":
            try:
                wolt_cost, wolt_avail = hero_get_price("rr", COUNTRY_KZ, api_key=hk)
                combo_block = (
                    f"\nПотом Wolt SMS KZ: <b>{_fmt_cost(wolt_cost)}</b> · {wolt_avail:,} шт"
                    " (без почты)"
                )
                if balance < max_price + wolt_cost + PRICE_MARGIN:
                    combo_block += "\n⚠️ На оба шага баланса может не хватить."
            except Exception:
                combo_block = "\nПотом Wolt SMS KZ (без почты) — цена на шаге 2."
            combo_block += "\nПосле SMS Google появится кнопка «Дальше: Wolt»."

        if balance < max_price:
            await cb.message.edit_text(
                f"❌ <b>Недостаточно средств</b>\n\n"
                f"Сервис: {svc['emoji']} {svc['label']}\n"
                f"Страна: {country_label(country)}\n"
                f"Цена: {_fmt_cost(cost)}\n"
                f"Баланс: ${balance:.2f}\n"
                f"Нужно: ${max_price:.4f}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=back_label, callback_data=back_cb)],
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
            f"Цена сейчас: <b>{_fmt_cost(cost)}</b>\n"
            f"Лимит покупки: <b>${max_price:.4f}</b> (+margin)\n"
            f"Баланс: <b>${balance:.2f}</b>\n"
            f"Доступно номеров: {avail:,}"
            f"{combo_block}\n\n"
            f"<i>Перед покупкой цена обновится ещё раз. После ✅ — номер и ожидание SMS.</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"✅ Купить за ~{_fmt_cost(cost)}", callback_data="confirm_buy")],
                [InlineKeyboardButton(text="🔄 Обновить цену", callback_data=refresh_cb)],
                [InlineKeyboardButton(text=back_label, callback_data=back_cb)],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
            ]),
        )
    except Exception as e:
        db_error_log(cb.from_user.id, "confirm_price", str(e), traceback.format_exc())
        await cb.message.edit_text(
            f"❌ Ошибка проверки цен: {e}",
            reply_markup=menu_kb(None, is_admin=_is_admin(user_row)),
        )


async def _hero_mail_watch_alive(chat_id, state, account_id, watch_id):
    user = db_get_user(chat_id)
    data = await state.get_data()
    account = db_get_account(account_id, user["id"]) if user else None
    return bool(account and account["status"] not in ("cancelled", "completed", "cancel_pending", "cancel_failed")
                and await state.get_state() == RegState.waiting_wolt_email.state
                and data.get("account_id") == account_id and data.get("mail_watch_id") == watch_id)


async def _hero_email_recovery(chat_id, state, account_id, watch_id, text):
    if not await _hero_mail_watch_alive(chat_id, state, account_id, watch_id):
        return
    db_update_account(account_id, status="email_error")
    await state.clear()
    await _safe_send(chat_id, text, reply_markup=_wolt_retry_kb(account_id, "email"))


async def _wait_hero_wolt_email(chat_id, state):
    started = time.time()
    initial = await state.get_data()
    watch_id = initial.get("mail_watch_id")
    account_id = initial.get("account_id")
    while time.time() - started < MAIL_WAIT_SECONDS:
        data = await state.get_data()
        if not await _hero_mail_watch_alive(chat_id, state, account_id, watch_id):
            return
        link = _hero_wolt_link({"value": data.get("email_link")})
        mail_status = "SUCCESS" if link else "WAIT"
        if not link:
            try:
                mail = await asyncio.to_thread(hero_email_get, data["mail_activation_id"], data["mail_api_key"])
                if not await _hero_mail_watch_alive(chat_id, state, account_id, watch_id):
                    return
                _validate_hero_email(mail, data["mail_activation_id"], data["email"])
            except Exception:
                await _hero_email_recovery(chat_id, state, account_id, watch_id,
                    "⚠️ Не удалось проверить HeroSMS Outlook. Активация сохранена; можно проверить ещё раз. SMS не покупался.")
                return
            mail_status = mail["status"]
            db_update_account(account_id, mail_status=mail_status)
            link = _hero_wolt_link(mail) if mail_status == "SUCCESS" else None
        if mail_status != "WAIT" and not link:
            await _hero_email_recovery(chat_id, state, account_id, watch_id,
                "⚠️ В ответе HeroSMS нет пригодной ссылки Wolt. Активация сохранена для повторной проверки; SMS не покупался.")
            return
        if link:
            db_update_account(account_id, email_link=link, status="email_received")
            await state.update_data(email_link=link)
            try:
                delivered = await _send_wolt_link(chat_id, data["email"], link)
            except Exception:
                delivered = None
            if not await _hero_mail_watch_alive(chat_id, state, account_id, watch_id):
                return
            if delivered is None:
                db_update_account(account_id, status="email_error")
                await state.clear()
                await _safe_send(chat_id, "⚠️ Ссылку не удалось доставить. Outlook и ссылка сохранены; SMS не покупался.",
                                 reply_markup=_wolt_retry_kb(account_id, "email"))
                return
            db_update_account(account_id, email_link_delivered=1)
            await _wolt_buy_number(chat_id, state)
            return
        await asyncio.sleep(MAIL_POLL_SECONDS)
    if not await _hero_mail_watch_alive(chat_id, state, account_id, watch_id):
        return
    db_update_account(account_id, status="email_timeout")
    await state.clear()
    await _safe_send(chat_id, "⚠️ Письмо Wolt не пришло за 5 минут. Outlook активация сохранена для проверки; возврат не обещан.",
                     reply_markup=_wolt_retry_kb(account_id, "email"))


async def _wait_wolt_email(chat_id, state):
    """Wait for the Wolt email, then start the HeroSMS part of the flow."""
    if (await state.get_data()).get("mail_provider") == "herosms":
        return await _wait_hero_wolt_email(chat_id, state)
    started = time.time()
    try:
        while time.time() - started < MAIL_WAIT_SECONDS:
            if db_user_is_disabled(chat_id):
                return
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


async def _outlook_quote(cb, state, key, user):
    request_id = secrets.token_hex(12)
    await state.update_data(outlook_quote_request=request_id, service_key="wolt_outlook", outlook_confirm=None)
    await state.set_state(RegState.confirming)
    mail_cost, stock = await asyncio.to_thread(hero_email_offer, key)
    sms_cost, sms_stock = await asyncio.to_thread(hero_get_price, "rr", COUNTRY_KZ, key)
    balance = await asyncio.to_thread(_hero_balance, key)
    if (not db_get_user(cb.from_user.id) or await state.get_state() != RegState.confirming.state
            or (await state.get_data()).get("outlook_quote_request") != request_id
            or (await state.get_data()).get("service_key") != "wolt_outlook"):
        return
    total = float(Decimal(str(mail_cost)) + Decimal(str(sms_cost)) + Decimal(str(PRICE_MARGIN)))
    token = secrets.token_hex(12)
    await state.update_data(outlook_confirm=token, mail_quote=mail_cost, outlook_total=total,
                            hero_key=key, mail_api_key=key, service_key="wolt_outlook",
                            db_user_id=user["id"], cost=sms_cost)
    await state.set_state(RegState.confirming)
    rows = []
    if stock > 0 and sms_stock > 0 and balance >= total:
        rows.append([InlineKeyboardButton(text="✅ Купить Outlook и продолжить", callback_data=f"confirm_outlook:{token}")])
    await cb.message.edit_text(
        f"📧 <b>Outlook + Wolt SMS KZ (HeroSMS)</b>\n\n"
        f"Outlook: ${mail_cost:.4f} · {stock} шт\nWolt SMS KZ: ${sms_cost:.4f} · {sms_stock} шт\n"
        f"Баланс: ${balance:.2f}\nОценка всего с SMS margin: ${total:.4f}\n\n"
        "Покупка только после подтверждения. Outlook привязан к wolt.com, это не постоянный ящик."
        + ("\n❌ Нет запаса или недостаточно средств." if not rows else ""),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows + [
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="start_wolt_outlook")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
        ]),
    )


@router.callback_query(F.data == "start_wolt_outlook")
async def cb_start_wolt_outlook(cb: CallbackQuery, state: FSMContext):
    user = db_get_user(cb.from_user.id)
    if not user or await state.get_state() not in (None, RegState.confirming.state):
        await cb.answer("Доступ запрещён или уже есть активная сессия", show_alert=True)
        return
    async with _chat_lock(cb.from_user.id):
        if not db_get_user(cb.from_user.id) or await state.get_state() not in (None, RegState.confirming.state):
            await cb.answer("Сессия изменилась", show_alert=True)
            return
        intent = db_email_intent(user["id"])
        if intent and intent["status"] == "uncertain":
            await cb.answer("Покупка Outlook не подтверждена. Нужна сверка HeroSMS вручную; повторная покупка заблокирована.", show_alert=True)
            return
        key = get_user_hero_key(user) or (HERO_KEY if _uses_admin_hero(user) else None)
        if not key:
            await cb.answer("Нужен HeroSMS ключ", show_alert=True)
            return
        try:
            await _outlook_quote(cb, state, key, user)
            await cb.answer()
        except Exception:
            await cb.answer("Не удалось проверить цены HeroSMS. Попробуй позже.", show_alert=True)


@router.callback_query(F.data.startswith("confirm_outlook:"))
async def cb_confirm_outlook(cb: CallbackQuery, state: FSMContext):
    async with _chat_lock(cb.from_user.id):
        user = db_get_user(cb.from_user.id)
        data = await state.get_data()
        token = cb.data.split(":", 1)[1]
        if (not user or await state.get_state() != RegState.confirming.state or not token
                or data.get("service_key") != "wolt_outlook" or data.get("outlook_confirm") != token):
            await cb.answer("Подтверждение устарело", show_alert=True)
            return
        intent = db_email_intent(user["id"])
        if intent and intent["status"] == "uncertain":
            await cb.answer("Нужна сверка покупки HeroSMS вручную", show_alert=True)
            return
        # Provider I/O can outlive Telegram's callback-query lifetime. Acknowledge
        # once before waiting; later results must never depend on answering it.
        try:
            await cb.answer()
        except TelegramBadRequest:
            return

        async def notify(text):
            await _safe_send_raw(cb.from_user.id, text)

        key = data["mail_api_key"]
        try:
            mail_cost, stock = await asyncio.to_thread(hero_email_offer, key)
            sms_cost, sms_stock = await asyncio.to_thread(hero_get_price, "rr", COUNTRY_KZ, key)
            balance = await asyncio.to_thread(_hero_balance, key)
        except Exception:
            await notify("Не удалось обновить цены HeroSMS. Покупки не было.")
            return
        if (not db_get_user(cb.from_user.id) or await state.get_state() != RegState.confirming.state
                or (await state.get_data()).get("outlook_confirm") != token
                or (await state.get_data()).get("service_key") != "wolt_outlook"):
            await notify("Сессия изменилась; покупки не было")
            return
        total = float(Decimal(str(mail_cost)) + Decimal(str(sms_cost)) + Decimal(str(PRICE_MARGIN)))
        if (mail_cost > data["mail_quote"] or total > data["outlook_total"]
                or (get_user_hero_key(user) or HERO_KEY) != key or stock <= 0 or sms_stock <= 0 or balance < total):
            await _outlook_quote(cb, state, get_user_hero_key(user) or HERO_KEY, user)
            await notify("Цена/доступность изменилась: проверь и подтверди заново.")
            return
        await state.update_data(outlook_confirm=None, outlook_purchase=token)
        phase = "purchase_intent"
        try:
            db_begin_email_intent(user["id"], token, key, mail_cost)
            phase = "purchase_response"
            mail = await asyncio.to_thread(hero_email_buy, key)
            phase = "save_account"
            account_id = db_add_hero_account(user["id"], mail, key)
            if (not db_get_user(cb.from_user.id) or await state.get_state() != RegState.confirming.state
                    or (await state.get_data()).get("outlook_purchase") != token
                    or (await state.get_data()).get("service_key") != "wolt_outlook"):
                await notify("Outlook сохранён; сессия изменилась. Продолжение доступно через «Мои почты».")
                return
            phase = "restore_session"
            await _load_wolt_state(state, db_get_account(account_id, user["id"]), user)
            await state.set_state(RegState.waiting_wolt_email)
            phase = "deliver_address"
            await cb.message.edit_text(
                f"📧 <b>Outlook Wolt готов</b>\n\n<code>{html_lib.escape(mail['email'])}</code>\n\n"
                "Введи email в Wolt. Жду письмо 5 минут, пришлю ссылку, затем куплю SMS KZ.\n"
                "Активация только для wolt.com, не постоянный ящик; возврат не обещан.",
                reply_markup=menu_kb(RegState.waiting_wolt_email, is_admin=_is_admin(user)),
            )
            phase = "start_mail_watcher"
            asyncio.create_task(_wait_wolt_email(cb.message.chat.id, state))
        except Exception as exc:
            log.error("Outlook purchase failed: phase=%s error_class=%s", phase, type(exc).__name__)
            if (await state.get_data()).get("outlook_purchase") == token:
                await state.clear()
            await notify("Покупка Outlook не подтверждена. Проверь HeroSMS вручную; не покупай повторно вслепую.")


@router.callback_query(F.data == "start_wolt")
async def cb_start_wolt(cb: CallbackQuery, state: FSMContext):
    await state.update_data(outlook_confirm=None, outlook_quote_request=None)
    await _begin_wolt_registration(cb, state)


@router.callback_query(F.data == "start_wolt_sms")
async def cb_start_wolt_sms(cb: CallbackQuery, state: FSMContext):
    current = await state.get_state()
    if current not in (None, RegState.confirming.state):
        await cb.answer("Уже есть активная сессия", show_alert=True)
        return
    await state.update_data(combo=None, outlook_confirm=None, outlook_quote_request=None)
    await _show_confirm(cb, state, "wolt")


async def _cancel_hero_mail_session(cb, state, account, user_row):
    # Stop the watcher before awaiting provider cancellation; keep recovery data.
    db_update_account(account["id"], status="cancelled", mail_session="cancel_requested")
    await state.clear()
    try:
        await _cancel_hero_email(account)
        db_update_account(account["id"], mail_session="closed")
        text = "❌ Outlook сессия закрыта. Запись сохранена; использованное письмо не отменяется. Возврат не обещан."
    except Exception:
        db_update_account(account["id"], mail_session="cancel_unconfirmed")
        text = "⚠️ Сессия закрыта, отмена Outlook не подтверждена. ID и исходный ключ сохранены; проверь HeroSMS вручную. Возврат не обещан."
    await cb.message.edit_text(text, reply_markup=menu_kb(None, is_admin=_is_admin(user_row)))
    await cb.answer()


async def _resume_hero_wolt(cb, state, account, user_row):
    current = await state.get_state()
    data = await state.get_data()
    if (_chat_lock(cb.from_user.id).locked()
            or current not in (None, RegState.confirming.state) and data.get("account_id") != account["id"]):
        await cb.answer("Уже есть другая активная сессия или идёт операция", show_alert=True)
        return
    async with _chat_lock(cb.from_user.id):
        account = db_get_account(account["id"], user_row["id"])
        if not account:
            await cb.answer("Почта не найдена", show_alert=True)
        elif account["activation_id"] and account["status"] == "sms_waiting":
            await _resume_wolt_sms_session(cb, state, account, user_row)
        elif account["activation_id"] or account["status"] in ("cancelled", "completed", "cancel_pending", "cancel_failed"):
            await cb.answer("Номер уже выдавался; повторная покупка не запускается", show_alert=True)
        elif account["mail_session"] == "sms_buying":
            await cb.answer("Покупка SMS не подтверждена: нужна ручная сверка HeroSMS, повтор заблокирован", show_alert=True)
        else:
            await _restart_wolt_email_session(cb, state, account, user_row)


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
        reply_markup=_session_markup(RegState.waiting_sms, is_admin=_is_admin(user_row), link=link, activation_id=account["activation_id"]),
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
        if _row_get(account, "mail_provider") == "herosms":
            return await _resume_hero_wolt(cb, state, account, user_row)
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
        if _row_get(account, "mail_provider") == "herosms":
            return await _resume_hero_wolt(cb, state, account, user_row)
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
        if _row_get(account, "mail_provider") == "herosms":
            return await _resume_hero_wolt(cb, state, account, user_row)
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
            action = db_queue_number_action(
                account["activation_id"], cb.from_user.id, "cancel",
                get_user_hero_key(user_row), {"account_id": account_id},
                delay=CANCEL_DELAY_SECONDS,
            )
            await state.update_data(sms_watch_id=secrets.token_hex(8), activation_id=None)
            await state.set_state(RegState.cancelling)
            _pending_number_states[cb.from_user.id] = state
            await cb.message.edit_text(_number_pending_text(action),
                                       reply_markup=_number_pending_kb(action), parse_mode=None)
            await cb.answer()
            return
        if _row_get(account, "mail_provider") == "herosms" and account["status"] != "completed":
            if _chat_lock(cb.from_user.id).locked():
                await cb.answer("Идёт операция; попробуй позже")
                return
            async with _chat_lock(cb.from_user.id):
                return await _cancel_hero_mail_session(cb, state, account, user_row)
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
    await state.update_data(combo=None, outlook_confirm=None, outlook_quote_request=None)
    await _show_gmail_countries(cb, state, page=0, refresh=True)


@router.callback_query(F.data == "start_combo")
async def cb_start_combo(cb: CallbackQuery, state: FSMContext):
    await state.update_data(combo="gmail_then_wolt", outlook_confirm=None, outlook_quote_request=None)
    await _show_gmail_countries(cb, state, page=0, refresh=True)


@router.callback_query(F.data == "gmail_refresh")
async def cb_gmail_refresh(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    page = int(data.get("gmail_page") or 0)
    await _show_gmail_countries(cb, state, page=page, refresh=True)


@router.callback_query(F.data.startswith("gmail_p:"))
async def cb_gmail_page(cb: CallbackQuery, state: FSMContext):
    try:
        page = int(cb.data.split(":", 1)[1])
    except (TypeError, ValueError):
        page = 0
    await _show_gmail_countries(cb, state, page=page, refresh=False)


@router.callback_query(F.data.startswith("gmail_c:"))
async def cb_gmail_country(cb: CallbackQuery, state: FSMContext):
    try:
        country = int(cb.data.split(":", 1)[1])
    except (TypeError, ValueError):
        await cb.answer("Неизвестная страна", show_alert=True)
        return
    await _show_confirm(cb, state, "gmail", country=country)


async def _show_gmail_countries(cb: CallbackQuery, state: FSMContext, page=0, refresh=False):
    user_row = db_get_user(cb.from_user.id)
    if not user_row:
        await cb.answer("Доступ запрещён", show_alert=True)
        return
    current = await state.get_state()
    if current not in (None, RegState.confirming.state):
        await cb.answer("Уже есть активная сессия", show_alert=True)
        return
    hk = get_user_hero_key(user_row)
    if hk is None and not _uses_admin_hero(user_row):
        await cb.message.edit_text(
            "🔑 Нужен HeroSMS API ключ.",
            reply_markup=menu_kb(None, is_admin=_is_admin(user_row)),
        )
        await state.set_state(RegState.waiting_hero_key)
        await cb.answer()
        return
    cache_key = f"go:{hk or 'admin'}"
    hit = _OFFERS_CACHE.get(cache_key)
    cached = bool(hit and (time.time() - hit["ts"] < OFFERS_TTL))
    if refresh or not cached:
        try:
            await cb.message.edit_text("⏳ Гружу страны и цены Gmail...")
        except TelegramBadRequest:
            pass
        await cb.answer()
        try:
            offers = await asyncio.to_thread(hero_service_offers, "go", hk, refresh)
        except Exception as exc:
            log.error("gmail countries load failed: %s", exc, exc_info=True)
            db_error_log(cb.from_user.id, "gmail_countries", str(exc), traceback.format_exc())
            await cb.message.edit_text(
                f"❌ Не удалось загрузить страны Gmail: {html_lib.escape(str(exc))}",
                reply_markup=service_choice_kb(),
            )
            return
    else:
        await cb.answer()
        offers = hit["offers"]
    if not offers:
        await cb.message.edit_text(
            "❌ Сейчас нет доступных номеров Gmail ни в одной стране.",
            reply_markup=service_choice_kb(),
        )
        return
    kb, page, pages, total = gmail_countries_kb(offers, page=page)
    session = await state.get_data()
    combo = session.get("combo")
    await state.update_data(
        service_key="gmail",
        service_code="go",
        service_label="Gmail",
        service_emoji="📧",
        hero_key=hk,
        db_user_id=user_row["id"],
        gmail_page=page,
    )
    await state.set_state(RegState.confirming)
    cheapest = offers[0]
    cheap_name = html_lib.escape(cheapest.get("rus") or cheapest.get("eng") or "")
    title = "📧 <b>Google → Wolt · шаг 1/2 — страна Google</b>" if combo == "gmail_then_wolt" else "📧 <b>Gmail — выбери страну</b>"
    extra = "После SMS Google будет шаг Wolt SMS (без почты).\n" if combo == "gmail_then_wolt" else ""
    text = (
        f"{title}\n\n"
        f"{extra}"
        "Сортировка: дешевле сверху. Цена и наличие живые.\n"
        f"В наличии: <b>{total}</b> стран, страница {page + 1}/{pages}.\n"
        f"Самый дешёвый: {cheap_name} · {_fmt_cost(cheapest['cost'])}"
    )
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


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

        existing_cancels = {a["activation_id"] for a in db_user_number_actions(user_row["id"])}
        try:
            activation = await _purchase_number(svc_code, country, hk, owner_chat=cb.from_user.id)
        except Exception as e:
            db_error_log(cb.from_user.id, "buy_fail", type(e).__name__)
            new_cancel = any(a["activation_id"] not in existing_cancels
                             for a in db_user_number_actions(user_row["id"]))
            detail = str(e)
            public = (detail if isinstance(e, ValueError) and len(detail) <= 160
                      and not any(x in detail.lower() for x in ("api_key", "apikey", "authorization", "http"))
                      else type(e).__name__)
            await state.clear()
            await cb.message.edit_text(
                f"❌ Не удалось купить номер.\n<code>{html_lib.escape(public)}</code>"
                + ("\n⏳ Купленный номер поставлен на отложенную отмену." if new_cancel else ""),
                reply_markup=menu_kb(None, is_admin=is_admin),
            )
            return

        phone = activation["phoneNumber"]
        phone_short = phone_local(phone, country)
        actual_cost = float(activation.get("activationCost") or data.get("cost") or 0)

        db_add_activation(db_user_id, activation["activationId"], phone, actual_cost, svc_code, country, api_key=hk)

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
            reply_markup=menu_kb(RegState.waiting_sms, is_admin=is_admin, activation_id=activation["activationId"]),
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
            if db_user_is_disabled(chat_id):
                _forget_active_state(chat_id, state)
                return False
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
                log.warning("sms_check error aid=%s: %s", aid, type(e).__name__ if data.get("mail_provider") == "herosms" else e)
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
                combo = fresh.get("combo")
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
                if combo == "gmail_then_wolt":
                    code_text += "\nКогда Google готов — жми «Дальше: Wolt»."
                else:
                    code_text += "\nНажми «Завершить» когда закончишь."
                await _safe_send(
                    chat_id,
                    code_text,
                    reply_markup=_session_markup(
                        RegState.completed,
                        is_admin=is_admin,
                        link=fresh.get("email_link"),
                        combo=combo,
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
                    log.warning("Resend failed aid=%s: %s", aid, type(e).__name__ if data.get("mail_provider") == "herosms" else e)
                resend_done = True

            await asyncio.sleep(7)

        if not await _still_mine():
            return

        # Timeout — durable delayed cancellation. Never promise a refund early.
        action = await _cancel_hero_activation(aid, hk, account_id)
        await state.clear()
        _forget_active_state(chat_id, state)
        await _safe_send(
            chat_id,
            (f"⚠️ SMS не пришло за {SMS_WAIT_SECONDS // 60} мин.\n"
             "Отмена номера запланирована через 2 минуты; подтверждение придёт отдельно."
             if action else "⚠️ SMS не пришло; автоматическую отмену запланировать не удалось. Проверь номер вручную."),
            reply_markup=_number_pending_kb(action) if action else menu_kb(None),
        )

    except Exception as e:
        log.error("_wait_sms CRASHED: %s", type(e).__name__ if locals().get("data", {}).get("mail_provider") == "herosms" else e,
                  exc_info=locals().get("data", {}).get("mail_provider") != "herosms")
        try:
            data = await state.get_data()
            aid = data.get("activation_id")
            hk = data.get("hero_key")
            account_id = data.get("account_id")
            action = await _cancel_hero_activation(aid, hk, account_id) if aid else None
            if not aid and account_id:
                db_update_account(account_id, status="error")
        except Exception:
            action = None
        await state.clear()
        _forget_active_state(chat_id, state)
        await _safe_send(
            chat_id,
            "⚠️ Ошибка SMS-ожидания. Отмена номера запланирована через 2 минуты."
            if action else "⚠️ Ошибка SMS-ожидания. Автоматическую отмену подтвердить не удалось — проверь номер вручную.",
            reply_markup=_number_pending_kb(action) if action else menu_kb(None),
        )


async def _swap_number(chat_id, state) -> bool:
    """Buy the replacement now; cancel the previous number later in the background."""
    if await state.get_state() != RegState.waiting_sms.state:
        return False
    data = await state.get_data()
    aid = data.get("activation_id")
    if not aid:
        return False
    if db_number_action(aid):
        raise ValueError("Этот номер уже ожидает отмены")
    context = {k: data.get(k) for k in (
        "service_key", "service_code", "service_label", "service_emoji",
        "country", "account_id", "email", "email_link", "cost", "combo",
    ) if data.get(k) is not None}
    service = context.get("service_code") or "rr"
    country = int(context.get("country") or COUNTRY_KZ)
    account_id = context.get("account_id")
    action = db_queue_number_action(
        aid, chat_id, "swap", data.get("hero_key"), context,
        delay=CANCEL_DELAY_SECONDS,
    )
    # Invalidate the old SMS watcher before buying, without awaiting the cancellation.
    await state.update_data(changing_number=True, sms_watch_id=secrets.token_hex(8), activation_id=None)
    await state.set_state(RegState.cancelling)
    await _safe_send(chat_id, "⏳ Покупаю новый номер. Старый отменится в фоне не раньше чем через 2 минуты.")
    try:
        new = await _purchase_number(service, country, action["api_key"],
                                     owner_chat=chat_id, account_id=account_id)
        db_register_replacement(action, new, service, country)
    except Exception as exc:
        # If a new number was bought but couldn't be persisted, queue its cleanup too.
        if "new" in locals():
            new_aid = new["activationId"]
            try:
                if not db_get_activation_for_webhook(new_aid):
                    db_add_activation(action["user_id"], new_aid, str(new["phoneNumber"]),
                                      float(new.get("activationCost") or 0), service, country,
                                      api_key=action["api_key"])
                db_queue_number_action(new_aid, chat_id, "cancel", action["api_key"],
                                       delay=CANCEL_DELAY_SECONDS)
            except Exception:
                log.exception("Could not queue orphan replacement %s", new_aid)
        if account_id:
            db_update_account(account_id, status="number_error", activation_id=None, phone="")
        await state.clear()
        _forget_active_state(chat_id, state)
        detail = str(exc)
        public = (detail if isinstance(exc, ValueError)
                  and len(detail) <= 160
                  and not any(x in detail.lower() for x in ("api_key", "apikey", "authorization", "http"))
                  else type(exc).__name__)
        await _safe_send(
            chat_id,
            f"❌ Не удалось выдать новый номер: {html_lib.escape(public)}\n"
            "Старый номер ожидает фоновой отмены."
            + (" Купленный номер тоже поставлен на отложенную отмену."
               if "new" in locals() else ""),
            reply_markup=_wolt_retry_kb(account_id, "number") if account_id else menu_kb(None, is_admin=_is_admin(db_get_user(chat_id))),
        )
        return False
    new_aid = new["activationId"]
    phone = str(new["phoneNumber"])
    cost = float(new.get("activationCost") or context.get("cost") or 0)
    await state.update_data(
        account_id=account_id, hero_key=action["api_key"],
        db_user_id=action["user_id"], activation_id=new_aid,
        phone=phone, actual_cost=cost, cost=cost, country=country,
        changing_number=False,
    )
    await state.set_state(RegState.waiting_sms)
    await _safe_send(
        chat_id,
        _number_ready_text(
            swapped=True, phone_short=phone_local(phone, country), cost=cost,
            email=context.get("email"), link=context.get("email_link"),
            service_emoji=context.get("service_emoji", ""),
            service_label=context.get("service_label", ""),
        ),
        reply_markup=_session_markup(
            RegState.waiting_sms, is_admin=_is_admin(db_get_user(chat_id)),
            link=context.get("email_link"), activation_id=new_aid,
        ),
    )
    await _start_sms_watch(chat_id, state)
    db_log(chat_id, "change_number_queued", f"old={aid} new={new_aid}")
    return True


def _number_actions_view(user, *, page=0, has_active_number=False):
    actions = db_user_number_actions(user["id"]) if user else []
    size = 8
    pages = max(1, (len(actions) + size - 1) // size)
    page = max(0, min(page, pages - 1))
    lines = [f"⏳ Отмены номеров: {len(actions)} · стр. {page + 1}/{pages}"]
    rows = []
    for action in actions[page * size:(page + 1) * size]:
        activation = db_get_activation_for_webhook(action["activation_id"])
        phone = phone_local(activation["phone"], activation["country"]) if activation else str(action["activation_id"])
        if action["status"] == "failed":
            status = "⚠️ не подтверждена"
            rows.append([InlineKeyboardButton(text=f"🔄 Повторить отмену {phone}",
                                              callback_data=f"number_retry_cancel:{action['activation_id']}")])
        else:
            due = datetime.datetime.fromtimestamp(action["due_at"]).strftime("%H:%M:%S")
            status = f"⏳ запрос не раньше {due}"
        lines.append(f"{phone} · {status}")
    nav = []
    if page:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"number_pending_status:{page - 1}"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"number_pending_status:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data=f"number_pending_status:{page}")])
    rows.append([InlineKeyboardButton(text="📱 К текущему номеру" if has_active_number else "🔙 В меню",
                                      callback_data="status" if has_active_number else "back_menu")])
    if not actions:
        lines.append("Нет ожидающих отмен. Подтверждённые HeroSMS отмены убираются из списка.")
    return "\n\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "number_pending_status")
@router.callback_query(F.data.startswith("number_pending_status:"))
async def cb_number_pending_status(cb: CallbackQuery, state: FSMContext):
    try:
        page = int(cb.data.split(":", 1)[1]) if ":" in cb.data else 0
        user = db_get_user(cb.from_user.id)
        active = await state.get_state() == RegState.waiting_sms.state
        text, markup = _number_actions_view(user, page=page, has_active_number=active)
        await cb.message.edit_text(text, reply_markup=markup, parse_mode=None)
        await cb.answer()
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            await cb.answer()
        else:
            await cb.answer(f"Ошибка: {exc}", show_alert=True)
    except Exception as exc:
        log.warning("number pending status failed: %s", type(exc).__name__)
        await cb.answer("Не удалось показать очередь отмен", show_alert=True)


@router.callback_query(F.data.startswith("number_retry_cancel:"))
async def cb_number_retry_cancel(cb: CallbackQuery, state: FSMContext = None):
    try:
        aid = int(cb.data.split(":", 1)[1])
        async with _chat_lock(cb.from_user.id):
            db_number_action_requeue(aid, cb.from_user.id)
        active = bool(state and await state.get_state() == RegState.waiting_sms.state)
        text, markup = _number_actions_view(db_get_user(cb.from_user.id), has_active_number=active)
        await cb.message.edit_text(text, reply_markup=markup, parse_mode=None)
        await cb.answer()
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            await cb.answer()
        else:
            await cb.answer("Не удалось обновить очередь", show_alert=True)
    except Exception as exc:
        await cb.answer(f"Ошибка: {exc}", show_alert=True)


@router.callback_query(F.data == "change_number")
@router.callback_query(F.data.startswith("change_number:"))
async def cb_change_number(cb: CallbackQuery, state: FSMContext):
    try:
        current = await state.get_state()
        if current != RegState.waiting_sms.state:
            await cb.answer("Сейчас нет активного номера", show_alert=True)
            return
        data = await state.get_data()
        aid = data.get("activation_id")
        if not aid:
            await cb.answer("Сейчас нет активного номера", show_alert=True)
            return
        if cb.data.startswith("change_number:") and cb.data != f"change_number:{aid}":
            await cb.answer("Это кнопка от прежнего номера. Открой актуальный экран.", show_alert=True)
            return
        lock = _chat_lock(cb.from_user.id)
        if lock.locked() or data.get("changing_number"):
            await cb.answer("Уже меняю номер…")
            return
        async with lock:
            if await _swap_number(cb.message.chat.id, state):
                await cb.answer("Новый номер готов, старый отменится в фоне")
            else:
                await cb.answer("Новый номер не куплен — смотри сообщение", show_alert=True)
    except Exception as exc:
        log.error("change_number failed: %s", type(exc).__name__, exc_info=True)
        await cb.answer("Не удалось сменить номер. Проверь статус в боте.", show_alert=True)


# ═══════════════════════════════════════════════════════════
#  Back / Cancel / Complete
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "back_menu")
async def cb_back_menu(cb: CallbackQuery, state: FSMContext):
    if _chat_lock(cb.from_user.id).locked():
        await cb.answer("Операция с номером ещё идёт")
        return
    user = db_get_user(cb.from_user.id)
    await state.clear()
    _forget_active_state(cb.from_user.id, state)
    is_admin = user["is_admin"] == 1 if user else False
    await cb.message.edit_text(
        "🤖 <b>SMS Bot</b>\n\nWolt: Казахстан (+7). Gmail: любая страна, живая цена.",
        reply_markup=menu_kb(None, is_admin=is_admin),
    )
    await cb.answer()


@router.callback_query(F.data == "cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    try:
        user = db_get_user(cb.from_user.id)
        current = await state.get_state()
        if _chat_lock(cb.from_user.id).locked():
            await cb.answer("Идёт покупка или отмена номера, попробуй ещё раз")
            return
        data_now = await state.get_data()
        pending = db_number_action(data_now.get("activation_id")) if data_now.get("activation_id") else None
        if current == RegState.cancelling.state and not data_now.get("activation_id"):
            pending = pending or _pending_number_for_user(user)
        if pending:
            await cb.message.edit_text(_number_pending_text(pending),
                                       reply_markup=_number_pending_kb(pending), parse_mode=None)
            await cb.answer()
            return
        async with _chat_lock(cb.from_user.id):
            data = await state.get_data()
            aid = data.get("activation_id")
            if aid and current != RegState.completed.state:
                context = {"account_id": data["account_id"]} if data.get("account_id") else {}
                action = db_queue_number_action(
                    aid, cb.from_user.id, "cancel", data.get("hero_key"), context,
                    delay=CANCEL_DELAY_SECONDS,
                )
                await state.update_data(sms_watch_id=secrets.token_hex(8), activation_id=None)
                await state.set_state(RegState.cancelling)
                _pending_number_states[cb.from_user.id] = state
                await cb.message.edit_text(_number_pending_text(action),
                                           reply_markup=_number_pending_kb(action), parse_mode=None)
                db_log(cb.from_user.id, "cancel_queued", f"activation={aid}")
                await cb.answer()
                return
            if data.get("mail_provider") == "herosms" and data.get("account_id") and current != RegState.completed.state:
                account = db_get_account(data["account_id"], user["id"] if user else -1)
                if account:
                    return await _cancel_hero_mail_session(cb, state, account, user)
            await state.clear()
            _forget_active_state(cb.from_user.id, state)
            if current != RegState.completed.state and data.get("account_id"):
                db_update_account(data["account_id"], status="cancelled")
            await cb.message.edit_text(
                "❌ Сессия завершена. Номер не покупался или уже был завершён.",
                reply_markup=menu_kb(None, is_admin=_is_admin(user)), parse_mode=None,
            )
            await cb.answer()
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            await cb.answer()
        else:
            await cb.answer(f"Ошибка: {exc}", show_alert=True)
    except Exception as exc:
        await cb.answer(f"Ошибка: {exc}", show_alert=True)


@router.callback_query(F.data == "combo_wolt")
async def cb_combo_wolt(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    current = await state.get_state()
    if data.get("combo") != "gmail_then_wolt" or current != RegState.completed.state:
        await cb.answer("Сначала получи SMS Google", show_alert=True)
        return
    user_row = db_get_user(cb.from_user.id)
    hk = data.get("hero_key") or get_user_hero_key(user_row)
    old_aid = data.get("activation_id")
    if old_aid:
        try:
            await asyncio.to_thread(
                _hero, "setStatus", api_key=hk, id=old_aid, status=HERO_COMPLETE,
            )
        except Exception as exc:
            log.warning("combo: complete Gmail activation %s failed: %s", old_aid, exc)
    await state.update_data(
        combo=None, activation_id=None, phone=None, sms_code=None, service_key=None,
    )
    await state.set_state(RegState.confirming)
    await _show_confirm(cb, state, "wolt")


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
            reply_markup=_session_markup(current, is_admin=is_admin, link=data.get("email_link"),
                                         activation_id=data.get("activation_id")),
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
    "cancel_pending": "⏳ отмена номера запланирована",
    "cancel_failed": "⚠️ отмена не подтверждена",
    "completed": "✅ завершён",
    "cancelled": "❌ отменён",
    "email_timeout": "⚠️ письмо не пришло",
    "email_error": "⚠️ ошибка почты",
    "number_error": "⚠️ ошибка номера",
    "timeout": "⚠️ таймаут",
}


MAIL_ACTIVE_STATUSES = frozenset({"email_waiting", "email_received", "sms_waiting", "cancel_pending", "cancel_failed"})


def _mail_bulk_candidates(user_id, max_id=None):
    accounts = db_get_user_accounts(user_id, limit=None)
    if max_id is not None:
        accounts = [a for a in accounts if a["id"] <= max_id]
    pending = [a for a in accounts if a["status"] in MAIL_ACTIVE_STATUSES]
    return accounts, pending


async def _delete_all_user_mailboxes(user_id, max_id, api_key=None):
    """Cancel pending numbers, delete remote inboxes, then remove owned local rows."""
    accounts, _ = _mail_bulk_candidates(user_id, max_id)
    deleted, failed = 0, 0
    for acc in accounts:
        # Check ownership/status again, because a running task may have changed it.
        fresh = db_get_account(acc["id"], user_id)
        if fresh is None:
            continue
        if fresh["status"] in ("cancel_pending", "cancel_failed"):
            failed += 1
            continue
        if fresh["status"] != "completed" and fresh["activation_id"]:
            activation = db_get_activation_for_webhook(fresh["activation_id"])
            if activation and activation["status"] not in ("completed", "cancelled"):
                try:
                    db_queue_number_action(
                        fresh["activation_id"], activation["telegram_id"], "cancel",
                        api_key, {"account_id": fresh["id"]}, delay=CANCEL_DELAY_SECONDS,
                    )
                except Exception as exc:
                    log.warning("mail bulk: cannot queue cancellation %s: %s", fresh["activation_id"], exc)
                failed += 1  # Keep inbox until cancellation has been confirmed.
                continue
        try:
            await _delete_remote_mail(fresh)
        except Exception as exc:
            failed += 1
            log.warning("mail bulk delete failed account_id=%s: %s", fresh["id"], exc)
            continue
        if db_delete_account(fresh["id"], user_id):
            deleted += 1
    return deleted, failed


def _mail_provider_label(account):
    return "HeroSMS Outlook" if _row_get(account, "mail_provider") == "herosms" else "Mail.tm"


def _account_status_label(account) -> str:
    return ACCOUNT_STATUS_LABELS.get(
        _row_get(account, "status"), _row_get(account, "status") or "—"
    )


def mail_accounts_kb(user_id: int) -> InlineKeyboardMarkup:
    rows = []
    for acc in db_get_user_accounts(user_id):
        rows.append([InlineKeyboardButton(
            text=f"📧 {acc['email']} · {_mail_provider_label(acc)}",
            callback_data=f"mail_view:{acc['id']}",
        )])
    if rows:
        rows.append([InlineKeyboardButton(text="🗑 Удалить все мои почты", callback_data="mail_del_all")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mail_view_kb(account_id: int, link: str | None = None, provider="mailtm") -> InlineKeyboardMarkup:
    rows = _link_button_rows(link)
    rows.extend([
        [InlineKeyboardButton(text="📨 Проверить существующую активацию" if provider == "herosms" else "📨 Получить последнее письмо", callback_data=f"mail_link:{account_id}")],
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
        total = len(db_get_user_accounts(user["id"], limit=None))
        if not accounts:
            await cb.message.edit_text(
                "📬 <b>Мои почты</b>\n\nПока нет ни одной почты.\n"
                "Они создаются при регистрации Wolt.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="back_menu")],
                ]),
            )
        else:
            lines = [f"📬 <b>Мои почты</b> — {total} (показаны последние {len(accounts)}):"]
            for acc in accounts:
                lines.append(f"• <code>{html_lib.escape(acc['email'])}</code> · {_mail_provider_label(acc)} — {_account_status_label(acc)}")
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
        if _row_get(account, "mail_provider") == "herosms":
            lines.append("HeroSMS Outlook: активация только для wolt.com, не постоянный ящик. Проверка существующего ID бесплатна; новое письмо/повторный вход не гарантированы. Платный reorder не запускается.")
            lines.append(f"Почта: ${float(account['mail_cost'] or 0):.4f} (отдельно от SMS)")
        else:
            lines.append("«Последнее письмо» — заново проверю Mail.tm и пришлю свежую ссылку Wolt.")
        await cb.message.edit_text("\n".join(lines), reply_markup=mail_view_kb(account_id, provider=_row_get(account, "mail_provider", "mailtm")))
        await cb.answer()
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await cb.answer()
        else:
            await cb.answer(f"Ошибка Telegram: {e}", show_alert=True)
    except Exception as e:
        log.error(f"cb_mail_view failed: {e}", exc_info=True)
        await cb.answer(f"Ошибка: {e}", show_alert=True)


async def _check_saved_hero_mail(cb, account, user):
    try:
        mail = await asyncio.to_thread(hero_email_get, account["mail_activation_id"], account["mail_api_key"])
        if not db_get_user(cb.from_user.id) or not db_get_account(account["id"], user["id"]):
            await cb.answer("Почта больше недоступна", show_alert=True)
            return
        _validate_hero_email(mail, account["mail_activation_id"], account["email"])
        link = _hero_wolt_link(mail) if mail["status"] == "SUCCESS" else None
        db_update_account(account["id"], mail_status=mail["status"], **({"email_link": link} if link else {}))
        text = f"📧 <code>{html_lib.escape(account['email'])}</code> · HeroSMS Outlook\n\n"
        text += "Только wolt.com, не постоянный ящик. Проверен существующий ID, без платного reorder.\n"
        if link:
            text += f'<a href="{html_lib.escape(link, quote=True)}">🔗 Открыть ссылку Wolt</a>'
        else:
            text += "Пригодной ссылки пока нет. Активация сохранена, проверь позже."
        await cb.message.edit_text(text, reply_markup=mail_view_kb(account["id"], link, provider="herosms"))
        await cb.answer()
    except Exception:
        await cb.answer("HeroSMS не подтвердил проверку; активация сохранена. Попробуй позже.", show_alert=True)


@router.callback_query(F.data.startswith("mail_link:"))
async def cb_mail_link(cb: CallbackQuery):
    try:
        account_id = int(cb.data.split(":", 1)[1])
        user = db_get_user(cb.from_user.id)
        account = db_get_account(account_id, user["id"] if user else None)
        if not user or not account:
            await cb.answer("Почта не найдена", show_alert=True)
            return
        if _row_get(account, "mail_provider") == "herosms":
            return await _check_saved_hero_mail(cb, account, user)

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


@router.callback_query(F.data == "mail_del_all")
async def cb_mail_del_all(cb: CallbackQuery):
    try:
        user = db_get_user(cb.from_user.id)
        if not user:
            await cb.answer("Доступ запрещён", show_alert=True)
            return
        accounts, pending = _mail_bulk_candidates(user["id"])
        if not accounts:
            await cb.answer("Список почт уже пуст", show_alert=True)
            return
        warning = (
            f"\n⚠️ С незавершённой регистрацией/отменой: {len(pending)}. "
            "Для активных номеров отмену запланирую через 2 минуты; "
            "при отказе HeroSMS почта останется, отмену нужно повторить отдельно."
            if pending else ""
        )
        await cb.message.edit_text(
            f"🗑 Удалить ВСЕ свои почты ({len(accounts)})?\n\n"
            "Mail.tm: ящики и письма удалятся безвозвратно. HeroSMS Outlook: отмена pending активаций требует подтверждения, использованные удаляются только локально; возврат не обещан. "
            "Статистика успешных SMS-активаций сохранится."
            f"{warning}\n\nЭто действие нельзя отменить.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text=f"✅ Удалить {len(accounts)} почт", callback_data=f"mail_del_all_do:{accounts[0]['id']}"
                )],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="mail_list")],
            ]),
            parse_mode=None,
        )
        await cb.answer()
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await cb.answer()
        else:
            await cb.answer(f"Ошибка: {e}", show_alert=True)
    except Exception as e:
        log.error("cb_mail_del_all failed: %s", e, exc_info=True)
        await cb.answer("Не удалось открыть удаление", show_alert=True)


@router.callback_query(F.data.startswith("mail_del_all_do:"))
async def cb_mail_del_all_do(cb: CallbackQuery, state: FSMContext):
    try:
        user = db_get_user(cb.from_user.id)
        if not user:
            await cb.answer("Доступ запрещён", show_alert=True)
            return
        max_id = int(cb.data.split(":", 1)[1])
        accounts, pending = _mail_bulk_candidates(user["id"], max_id)
        if not accounts:
            await cb.answer("Все почты уже удалены", show_alert=True)
            return
        await cb.answer()
        await cb.message.edit_text(
            f"⏳ Удаляю {len(accounts)} почт у соответствующих провайдеров...",
            reply_markup=None,
            parse_mode=None,
        )
        async with _chat_lock(cb.from_user.id):
            current_data = await state.get_data()
            if any(a["id"] == current_data.get("account_id") for a in accounts):
                await state.clear()
                _forget_active_state(cb.from_user.id, state)
            deleted, failed = await _delete_all_user_mailboxes(
                user["id"], max_id, get_user_hero_key(user)
            )
        text = f"✅ Удалено почт: {deleted}."
        if failed:
            text += (f"\n⏳ Не удалены: {failed} — ожидание подтверждения HeroSMS "
                     "или ошибка Mail.tm. Почты сохранены, проверь их статус и повтори.")
        remaining = len(db_get_user_accounts(user["id"], limit=None))
        text += f"\nОсталось в списке: {remaining}."
        db_log(cb.from_user.id, "mail_delete_all", f"deleted={deleted} failed={failed}")
        await cb.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📬 К почтам", callback_data="mail_list")],
            ]),
            parse_mode=None,
        )
    except Exception as e:
        log.error("cb_mail_del_all_do failed: %s", e, exc_info=True)
        await cb.message.edit_text(
            f"⚠️ Удаление остановлено: {e}\nПроверь список почт и повтори при необходимости.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📬 К почтам", callback_data="mail_list")],
            ]),
            parse_mode=None,
        )


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
            + ("HeroSMS Outlook: pending активацию отменю только с подтверждением; использованную удалю лишь локально. Постоянный Outlook ящик не удаляется. Возврат не обещан."
               if _row_get(account, "mail_provider") == "herosms" else
               "Ящик будет удалён с Mail.tm <b>безвозвратно</b>, вместе с письмами."),
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
    lock = _chat_lock(cb.from_user.id)
    if lock.locked():
        await cb.answer("Операция уже выполняется; почта сохранена. Повтори позже.", show_alert=True)
        return
    async with lock:
        return await _cb_mail_del_do_core(cb)


async def _cb_mail_del_do_core(cb: CallbackQuery):
    try:
        account_id = int(cb.data.split(":", 1)[1])
        user = db_get_user(cb.from_user.id)
        account = db_get_account(account_id, user["id"] if user else None)
        if not user or not account:
            await cb.answer("Почта не найдена", show_alert=True)
            return

        # Preserve the inbox until the number is confirmed cancelled remotely.
        if account["status"] in ("cancel_pending", "cancel_failed"):
            action = db_number_action(account["activation_id"])
            if action:
                await cb.message.edit_text(_number_pending_text(action),
                                           reply_markup=_number_pending_kb(action), parse_mode=None)
                await cb.answer()
            else:
                await cb.answer("Отмена номера не подтверждена. Почта сохранена.", show_alert=True)
            return
        if account["activation_id"] and account["status"] != "completed":
            activation = db_get_activation_for_webhook(account["activation_id"])
            if activation and activation["status"] not in ("completed", "cancelled"):
                action = db_queue_number_action(
                    account["activation_id"], cb.from_user.id, "cancel",
                    get_user_hero_key(user), {"account_id": account_id}, delay=CANCEL_DELAY_SECONDS,
                )
                await cb.message.edit_text(
                    "⏳ Номер будет отменён не раньше чем через 2 минуты. "
                    "Почта сохранена: удалить её можно после подтверждения HeroSMS.",
                    reply_markup=_number_pending_kb(action), parse_mode=None,
                )
                await cb.answer()
                return

        mail_tm_deleted = False
        try:
            await _delete_remote_mail(account)
            mail_tm_deleted = True
        except Exception as exc:
            log.warning("mail delete: Mail.tm delete failed for %s: %s", account["email"], exc)

        if not mail_tm_deleted:
            await cb.answer("Провайдер не подтвердил удаление/отмену; почта сохранена. Возврат не обещан.", show_alert=True)
            return
        db_delete_account(account_id, user["id"])
        db_log(cb.from_user.id, "mail_delete", account["email"])

        remaining = db_get_user_accounts(user["id"])
        if remaining:
            lines = [f"📬 <b>Мои почты</b> — {len(remaining)}:"]
            for acc in remaining:
                lines.append(f"• <code>{html_lib.escape(acc['email'])}</code> · {_mail_provider_label(acc)} — {_account_status_label(acc)}")
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


def _stats_text_and_kb(label, start, end):
    s = db_get_stats()
    count, total_cost = db_successful_activation_summary(start, end)
    rate = f"{s['successful'] / s['total_activations'] * 100:.0f}%" if s["total_activations"] else "—"
    text = (
        f"📊 Статистика\n\n"
        f"👥 Пользователей: {s['total_users']}\n"
        f"🔄 Активаций всего: {s['total_activations']}\n"
        f"✅ Успешных за всё время: {s['successful']} ({rate})\n\n"
        f"📅 Период: {label}\n"
        f"✅ Успешных активаций: {count}\n"
        f"💵 Сумма их стоимости: ${total_cost:.4f}\n"
        "(сумма цен купленных номеров, не прибыль)\n"
    )
    buttons = [
        [InlineKeyboardButton(text="Сегодня", callback_data="admin_stats:today"),
         InlineKeyboardButton(text="7 дней", callback_data="admin_stats:week")],
        [InlineKeyboardButton(text="Этот месяц", callback_data="admin_stats:month"),
         InlineKeyboardButton(text="Всё время", callback_data="admin_stats:all")],
        [InlineKeyboardButton(text="📅 Свой период", callback_data="admin_stats:custom")],
        [InlineKeyboardButton(text="🔙 В админ-панель", callback_data="admin_panel")],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(cb: CallbackQuery):
    await _show_admin_stats(cb, "all")


async def _show_admin_stats(cb, period):
    try:
        if not _admin_only(cb.from_user.id):
            await cb.answer("Доступ запрещён", show_alert=True)
            return
        label, start, end = stats_period_bounds(period)
        text, kb = _stats_text_and_kb(label, start, end)
        await cb.message.edit_text(text, reply_markup=kb, parse_mode=None)
        await cb.answer()
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await cb.answer()
        else:
            await cb.answer(f"Ошибка: {e}", show_alert=True)
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("admin_stats:"))
async def cb_admin_stats_period(cb: CallbackQuery, state: FSMContext):
    period = cb.data.split(":", 1)[1]
    if not _admin_only(cb.from_user.id):
        await cb.answer("Доступ запрещён", show_alert=True)
        return
    if period == "custom":
        await state.set_state(AdminState.waiting_stats_period)
        await cb.message.edit_text(
            "📅 Введи даты начала и конца включительно: 01.09.2026 - 26.09.2026\n"
            "Время — по часовому поясу сервера.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_admin")],
            ]),
            parse_mode=None,
        )
        await cb.answer()
        return
    await _show_admin_stats(cb, period)


@router.message(AdminState.waiting_stats_period)
async def admin_stats_custom_input(msg: Message, state: FSMContext):
    if not _admin_only(msg.from_user.id):
        await state.clear()
        return
    if (msg.text or "").strip().lower() in ("/cancel", "отмена", "/start"):
        await state.clear()
        await msg.answer("❌ Период отменён.", reply_markup=admin_kb(), parse_mode=None)
        return
    try:
        label, start, end = parse_custom_stats_period(msg.text)
        text, kb = _stats_text_and_kb(label, start, end)
    except ValueError as e:
        await msg.answer(f"❌ {e}\nПришли даты или /cancel.", parse_mode=None)
        return
    await state.clear()
    await msg.answer(text, reply_markup=kb, parse_mode=None)


def _scoped_stats_text_and_kb(user, label, start, end, *, admin_view=False):
    """One user's completed activations, never the shared-key/global balance."""
    user_id = user["id"]
    all_count, _ = db_successful_activation_summary(user_id=user_id)
    count, total_cost = db_successful_activation_summary(start, end, user_id=user_id)
    title = (f"📈 Статистика: {user['first_name'] or user['telegram_id']} "
             f"(ID {user['telegram_id']})") if admin_view else "📈 Моя статистика"
    text = (
        f"{title}\n\n"
        f"✅ Успешных за всё время: {all_count}\n\n"
        f"📅 Период: {label}\n"
        f"✅ Успешных активаций: {count}\n"
        f"💵 Сумма их стоимости: ${total_cost:.4f}\n"
        "(сумма цен купленных номеров, не прибыль)"
    )
    prefix = f"admin_user_stats:{user_id}:" if admin_view else "user_stats:"
    back = f"admin_user_detail:{user_id}" if admin_view else "back_menu"
    rows = [
        [InlineKeyboardButton(text="Сегодня", callback_data=prefix + "today"),
         InlineKeyboardButton(text="7 дней", callback_data=prefix + "week")],
        [InlineKeyboardButton(text="Этот месяц", callback_data=prefix + "month"),
         InlineKeyboardButton(text="Всё время", callback_data=prefix + "all")],
        [InlineKeyboardButton(text="📅 Свой период", callback_data=prefix + "custom")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=back)],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _stats_target(tid, target_id=None):
    actor = db_get_user(tid)
    if not actor:
        return None
    if target_id is None:
        return actor
    if not _is_admin(actor):
        return None
    return db_get_user_by_id(target_id)


async def _show_scoped_stats(cb, period, target_id=None):
    try:
        user = _stats_target(cb.from_user.id, target_id)
        if not user:
            await cb.answer("Пользователь не найден или нет доступа", show_alert=True)
            return
        label, start, end = stats_period_bounds(period)
        text, kb = _scoped_stats_text_and_kb(user, label, start, end,
                                             admin_view=target_id is not None)
        await cb.message.edit_text(text, reply_markup=kb, parse_mode=None)
        await cb.answer()
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await cb.answer()
        else:
            await cb.answer(f"Ошибка: {e}", show_alert=True)
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data == "user_stats")
async def cb_user_stats(cb: CallbackQuery):
    await _show_scoped_stats(cb, "all")


@router.callback_query(F.data.startswith("user_stats:"))
async def cb_user_stats_period(cb: CallbackQuery, state: FSMContext):
    await _scoped_stats_period(cb, state, cb.data.split(":", 1)[1])


@router.callback_query(F.data.startswith("admin_user_stats:"))
async def cb_admin_user_stats_period(cb: CallbackQuery, state: FSMContext):
    try:
        _, target_id, period = cb.data.split(":", 2)
        await _scoped_stats_period(cb, state, period, int(target_id))
    except (ValueError, TypeError):
        await cb.answer("Неверный запрос", show_alert=True)


async def _scoped_stats_period(cb, state, period, target_id=None):
    if period != "custom":
        await _show_scoped_stats(cb, period, target_id)
        return
    user = _stats_target(cb.from_user.id, target_id)
    if not user:
        await cb.answer("Пользователь не найден или нет доступа", show_alert=True)
        return
    current = await state.get_state()
    if current and current.startswith("RegState:"):
        await cb.answer("Сначала заверши текущую регистрацию, затем выбери свой период", show_alert=True)
        return
    await state.set_state(StatsState.waiting_custom)
    await state.update_data(stats_target_id=target_id)
    try:
        await cb.message.edit_text(
            "📅 Введи даты начала и конца включительно: 01.09.2026 - 26.09.2026\n"
            "Время — по часовому поясу сервера.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="stats_cancel")],
            ]), parse_mode=None,
        )
        await cb.answer()
    except Exception as e:
        await state.clear()
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data == "stats_cancel")
async def cb_stats_cancel(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    target_id = data.get("stats_target_id")
    await state.clear()
    await _show_scoped_stats(cb, "all", target_id)


@router.message(StatsState.waiting_custom)
async def scoped_stats_custom_input(msg: Message, state: FSMContext):
    data = await state.get_data()
    target_id = data.get("stats_target_id")
    user = _stats_target(msg.from_user.id, target_id)
    if not user:
        await state.clear()
        await msg.answer("Доступ запрещён", parse_mode=None)
        return
    if (msg.text or "").strip().lower() in ("/cancel", "отмена", "/start"):
        await state.clear()
        await msg.answer("❌ Период отменён.", reply_markup=menu_kb(None, is_admin=_is_admin(db_get_user(msg.from_user.id))), parse_mode=None)
        return
    try:
        label, start, end = parse_custom_stats_period(msg.text)
        text, kb = _scoped_stats_text_and_kb(user, label, start, end,
                                             admin_view=target_id is not None)
    except ValueError as e:
        await msg.answer(f"❌ {e}\nПришли даты или /cancel.", parse_mode=None)
        return
    await state.clear()
    await _purge_ui(msg.chat.id)
    sent = await msg.answer(text, reply_markup=kb, parse_mode=None)
    _track_ui(msg.chat.id, sent)


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
    if tid == admin["telegram_id"] or tid == ADMIN_ID:
        await msg.answer("❌ Себя и владельца бота удалить нельзя.", reply_markup=admin_kb()); await state.clear(); return
    conn = sqlite3.connect(DB_PATH)
    try:
        target = conn.execute("SELECT id FROM users WHERE telegram_id = ? AND is_active = 1", (tid,)).fetchone()
    finally:
        conn.close()
    await state.clear()
    if not target:
        await msg.answer("Пользователь не найден среди активных.", reply_markup=admin_kb(), parse_mode=None)
        return
    await msg.answer(
        f"Пользователь {tid} найден. Подтверди отключение доступа:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➖ Отключить", callback_data=f"admin_user_remove:{target[0]}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_users:0")],
        ]),
        parse_mode=None,
    )


@router.callback_query(F.data == "cancel_admin")
async def cb_cancel_admin(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("❌ Действие отменено.", reply_markup=admin_kb()); await cb.answer()


def _admin_cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_admin")],
    ])


def _admin_only(tid):
    user = db_get_user(tid)
    if not user or user["is_admin"] != 1:
        return None
    return user


async def _render_admin_keys(cb: CallbackQuery):
    keys = db_list_admin_keys()
    rows = db_get_all_users()
    text = "🔑 Админские ключи\n\n★ — основной (ещё в .env как запасной).\n\n"
    kb = []
    if not keys:
        text += "Пока нет сохранённых ключей.\n\n"
    for k in keys:
        star = " ★" if _row_get(k, "is_default") else ""
        try:
            bal = await asyncio.to_thread(_hero_balance, _row_get(k, "api_key"))
            bal_s = f"${bal:.2f}"
        except Exception:
            bal_s = "ошибка"
        assigned = int(_row_get(k, "assigned", 0) or 0)
        text += (
            f"{_row_get(k, 'name')}{star}\n"
            f"  {mask_hero_key(_row_get(k, 'api_key'))}  {bal_s}  ·  {assigned} чел.\n\n"
        )
        kb.append([InlineKeyboardButton(
            text=f"⚙️ {_row_get(k, 'name')}{star}",
            callback_data=f"admin_key_view:{k['id']}",
        )])
    kb.append([
        InlineKeyboardButton(text="➕ Добавить ключ", callback_data="admin_key_add"),
        InlineKeyboardButton(text="✏️ Сменить основной", callback_data="admin_change_hero_key"),
    ])
    text += "👥 Назначение\n"
    if not rows:
        text += "Нет пользователей.\n"
    else:
        for tid, username, fname, is_admin, created in rows:
            user = db_get_user(tid)
            name = fname or str(tid)
            uname = f" @{username}" if username else ""
            badge = " 👑" if is_admin else ""
            status = user_assigned_key_label(user)
            text += f"{name}{badge}{uname}\n  {status}\n\n"
            kb.append([InlineKeyboardButton(
                text=f"👤 {name}",
                callback_data=f"admin_assign:{tid}",
            )])
    kb.append([InlineKeyboardButton(text="🔙 Админ-панель", callback_data="admin_panel")])
    await cb.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode=None,
    )


@router.callback_query(F.data == "admin_keys")
async def cb_admin_keys(cb: CallbackQuery):
    try:
        if not _admin_only(cb.from_user.id):
            await cb.answer("Доступ запрещён", show_alert=True)
            return
        await _render_admin_keys(cb)
        await cb.answer()
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await cb.answer()
        else:
            await cb.answer(f"Ошибка: {e}", show_alert=True)
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data == "admin_key_add")
async def cb_admin_key_add(cb: CallbackQuery, state: FSMContext):
    try:
        if not _admin_only(cb.from_user.id):
            await cb.answer("Доступ запрещён", show_alert=True)
            return
        await state.set_state(AdminState.waiting_admin_key_name)
        await state.update_data(admin_key_mode="add")
        await cb.message.edit_text(
            "➕ Новый админский ключ\n\n"
            "Сначала пришли имя, например: Основной, Резерв, Казахстан.\n"
            "Потом отдельным сообщением — сам ключ HeroSMS.\n\n"
            "/cancel чтобы отменить.",
            reply_markup=_admin_cancel_kb(),
            parse_mode=None,
        )
        await cb.answer()
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.message(AdminState.waiting_admin_key_name)
async def admin_key_name_input(msg: Message, state: FSMContext):
    admin = _admin_only(msg.from_user.id)
    if not admin:
        await state.clear()
        return
    raw = (msg.text or "").strip()
    if raw.lower() in ("/cancel", "отмена", "/start"):
        await state.clear()
        await msg.answer("❌ Добавление ключа отменено.", reply_markup=admin_kb(), parse_mode=None)
        return
    if len(raw) >= MIN_HERO_KEY_LEN and " " not in raw:
        await msg.answer(
            "Это похоже на ключ. Сначала короткое имя, ключ — следующим сообщением.",
            reply_markup=_admin_cancel_kb(),
            parse_mode=None,
        )
        return
    try:
        name = _normalize_key_name(raw)
    except ValueError as e:
        await msg.answer(f"❌ {e}\nПришли другое имя или /cancel.", reply_markup=_admin_cancel_kb(), parse_mode=None)
        return
    await state.update_data(admin_key_mode="add", pending_key_name=name)
    await state.set_state(AdminState.waiting_admin_hero_key)
    await msg.answer(
        f"Имя: {name}\n\n"
        "Теперь пришли ключ HeroSMS текстом.\n"
        "Проверю баланс. Сообщение с ключом удалю.",
        reply_markup=_admin_cancel_kb(),
        parse_mode=None,
    )


@router.callback_query(F.data.startswith("admin_key_view:"))
async def cb_admin_key_view(cb: CallbackQuery):
    try:
        if not _admin_only(cb.from_user.id):
            await cb.answer("Доступ запрещён", show_alert=True)
            return
        key_id = int(cb.data.split(":")[1])
        row = db_get_admin_key(key_id)
        if not row:
            await cb.answer("Ключ не найден", show_alert=True)
            return
        star = " ★" if _row_get(row, "is_default") else ""
        try:
            bal = await asyncio.to_thread(_hero_balance, _row_get(row, "api_key"))
            bal_s = f"${bal:.2f}"
        except Exception:
            bal_s = "ошибка"
        assigned = 0
        for item in db_list_admin_keys():
            if item["id"] == key_id:
                assigned = int(_row_get(item, "assigned", 0) or 0)
                break
        created = str(_row_get(row, "created_at") or "")[:10]
        text = (
            f"⚙️ {_row_get(row, 'name')}{star}\n\n"
            f"Маска: {mask_hero_key(_row_get(row, 'api_key'))}\n"
            f"Баланс: {bal_s}\n"
            f"Назначен: {assigned} чел.\n"
            f"Создан: {created or '—'}\n"
        )
        kb = [
            [InlineKeyboardButton(text="✏️ Переименовать", callback_data=f"admin_key_rename:{key_id}")],
            [InlineKeyboardButton(text="🔑 Заменить секрет", callback_data=f"admin_key_replace:{key_id}")],
        ]
        if not _row_get(row, "is_default"):
            kb.append([InlineKeyboardButton(text="★ Сделать основным", callback_data=f"admin_key_default:{key_id}")])
            kb.append([InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin_key_del:{key_id}")])
        kb.append([InlineKeyboardButton(text="🔙 К ключам", callback_data="admin_keys")])
        await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=None)
        await cb.answer()
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await cb.answer()
        else:
            await cb.answer(f"Ошибка: {e}", show_alert=True)
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("admin_key_rename:"))
async def cb_admin_key_rename(cb: CallbackQuery, state: FSMContext):
    try:
        if not _admin_only(cb.from_user.id):
            await cb.answer("Доступ запрещён", show_alert=True)
            return
        key_id = int(cb.data.split(":")[1])
        row = db_get_admin_key(key_id)
        if not row:
            await cb.answer("Ключ не найден", show_alert=True)
            return
        await state.set_state(AdminState.waiting_admin_key_rename)
        await state.update_data(admin_key_id=key_id)
        await cb.message.edit_text(
            f"✏️ Новое имя для «{_row_get(row, 'name')}»\n\nПришли имя текстом или /cancel.",
            reply_markup=_admin_cancel_kb(),
            parse_mode=None,
        )
        await cb.answer()
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.message(AdminState.waiting_admin_key_rename)
async def admin_key_rename_input(msg: Message, state: FSMContext):
    admin = _admin_only(msg.from_user.id)
    if not admin:
        await state.clear()
        return
    raw = (msg.text or "").strip()
    if raw.lower() in ("/cancel", "отмена", "/start"):
        await state.clear()
        await msg.answer("❌ Переименование отменено.", reply_markup=admin_kb(), parse_mode=None)
        return
    data = await state.get_data()
    try:
        name = db_rename_admin_key(data.get("admin_key_id"), raw)
    except ValueError as e:
        await msg.answer(f"❌ {e}\nПришли другое имя или /cancel.", reply_markup=_admin_cancel_kb(), parse_mode=None)
        return
    await state.clear()
    db_log(admin["telegram_id"], "admin_rename_key", f"name={name}")
    await msg.answer(f"✅ Ключ переименован в «{name}».", reply_markup=admin_kb(), parse_mode=None)


@router.callback_query(F.data.startswith("admin_key_replace:"))
async def cb_admin_key_replace(cb: CallbackQuery, state: FSMContext):
    try:
        if not _admin_only(cb.from_user.id):
            await cb.answer("Доступ запрещён", show_alert=True)
            return
        key_id = int(cb.data.split(":")[1])
        row = db_get_admin_key(key_id)
        if not row:
            await cb.answer("Ключ не найден", show_alert=True)
            return
        await state.set_state(AdminState.waiting_admin_hero_key)
        await state.update_data(admin_key_mode="replace", admin_key_id=key_id)
        await cb.message.edit_text(
            f"🔑 Заменить секрет «{_row_get(row, 'name')}»\n\n"
            "Пришли новый ключ текстом. Проверю баланс, сообщение удалю.",
            reply_markup=_admin_cancel_kb(),
            parse_mode=None,
        )
        await cb.answer()
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("admin_key_default:"))
async def cb_admin_key_default(cb: CallbackQuery):
    try:
        if not _admin_only(cb.from_user.id):
            await cb.answer("Доступ запрещён", show_alert=True)
            return
        key_id = int(cb.data.split(":")[1])
        row = db_get_admin_key(key_id)
        db_set_default_admin_key(key_id)
        db_log(cb.from_user.id, "admin_set_default_key", f"name={_row_get(row, 'name')}")
        await cb.answer(f"Основной теперь «{_row_get(row, 'name')}»")
        cb.data = f"admin_key_view:{key_id}"
        await cb_admin_key_view(cb)
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("admin_key_del:"))
async def cb_admin_key_del(cb: CallbackQuery):
    try:
        if not _admin_only(cb.from_user.id):
            await cb.answer("Доступ запрещён", show_alert=True)
            return
        key_id = int(cb.data.split(":")[1])
        row = db_get_admin_key(key_id)
        if not row:
            await cb.answer("Ключ не найден", show_alert=True)
            return
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"admin_key_del_do:{key_id}")],
            [InlineKeyboardButton(text="🔙 Нет", callback_data=f"admin_key_view:{key_id}")],
        ])
        await cb.message.edit_text(
            f"Удалить ключ «{_row_get(row, 'name')}»?\n"
            "Пользователей на нём быть не должно. Основной удалить нельзя.",
            reply_markup=kb,
            parse_mode=None,
        )
        await cb.answer()
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("admin_key_del_do:"))
async def cb_admin_key_del_do(cb: CallbackQuery):
    try:
        if not _admin_only(cb.from_user.id):
            await cb.answer("Доступ запрещён", show_alert=True)
            return
        key_id = int(cb.data.split(":")[1])
        name = db_delete_admin_key(key_id)
        db_log(cb.from_user.id, "admin_delete_key", f"name={name}")
        await cb.answer(f"Удалён «{name}»")
        await _render_admin_keys(cb)
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("admin_assign:"))
async def cb_admin_assign(cb: CallbackQuery):
    try:
        if not _admin_only(cb.from_user.id):
            await cb.answer("Доступ запрещён", show_alert=True)
            return
        tid = int(cb.data.split(":")[1])
        user = db_get_user(tid)
        if not user:
            await cb.answer("Пользователь не найден", show_alert=True)
            return
        name = user["first_name"] or str(tid)
        text = (
            f"👤 Назначить ключ: {name}\n"
            f"Сейчас: {user_assigned_key_label(user)}\n\n"
            "Выбери ключ из пула или оставь свой."
        )
        kb = []
        for k in db_list_admin_keys():
            star = " ★" if _row_get(k, "is_default") else ""
            mark = " ✓" if _row_get(user, "admin_key_id") == k["id"] else ""
            kb.append([InlineKeyboardButton(
                text=f"{_row_get(k, 'name')}{star}{mark}",
                callback_data=f"admin_assign_set:{tid}:{k['id']}",
            )])
        kb.append([InlineKeyboardButton(
            text="🟢 Свой / без админского",
            callback_data=f"admin_assign_own:{tid}",
        )])
        kb.append([InlineKeyboardButton(text="🔙 К ключам", callback_data="admin_keys")])
        await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=None)
        await cb.answer()
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await cb.answer()
        else:
            await cb.answer(f"Ошибка: {e}", show_alert=True)
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("admin_assign_set:"))
async def cb_admin_assign_set(cb: CallbackQuery):
    try:
        if not _admin_only(cb.from_user.id):
            await cb.answer("Доступ запрещён", show_alert=True)
            return
        _, tid_s, kid_s = cb.data.split(":")
        tid = int(tid_s)
        key_id = int(kid_s)
        row = db_get_admin_key(key_id)
        db_assign_user_admin_key(tid, key_id)
        db_log(cb.from_user.id, "admin_assign_key", f"user={tid} key={_row_get(row, 'name')}")
        await cb.answer(f"Назначен «{_row_get(row, 'name')}»")
        cb.data = f"admin_assign:{tid}"
        await cb_admin_assign(cb)
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("admin_assign_own:"))
async def cb_admin_assign_own(cb: CallbackQuery):
    try:
        if not _admin_only(cb.from_user.id):
            await cb.answer("Доступ запрещён", show_alert=True)
            return
        tid = int(cb.data.split(":")[1])
        db_assign_user_admin_key(tid, None)
        db_log(cb.from_user.id, "admin_assign_key", f"user={tid} key=own")
        await cb.answer("Свой ключ / без админского")
        cb.data = f"admin_assign:{tid}"
        await cb_admin_assign(cb)
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data == "admin_change_hero_key")
async def cb_admin_change_hero_key(cb: CallbackQuery, state: FSMContext):
    try:
        if not _admin_only(cb.from_user.id):
            await cb.answer("Доступ запрещён", show_alert=True)
            return
        await cb.message.edit_text(
            "✏️ Сменить основной ключ HeroSMS\n\n"
            "Пришли новый ключ текстом.\n"
            "Проверю баланс и сохраню в .env — рестарт не нужен.\n"
            "Сообщение с ключом удалю.\n\n"
            "❌ /cancel или кнопка чтобы отменить.",
            reply_markup=_admin_cancel_kb(),
            parse_mode=None,
        )
        await state.set_state(AdminState.waiting_admin_hero_key)
        await state.update_data(admin_key_mode="replace_default")
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
            reply_markup=_admin_cancel_kb(),
            parse_mode=None,
        )
        return

    await _forget_secret()
    data = await state.get_data()
    mode = data.get("admin_key_mode") or "replace_default"
    try:
        bal = _hero_balance(api_key=raw)
        if mode == "add":
            name = data.get("pending_key_name")
            if not name:
                raise ValueError("сначала задай имя ключа")
            db_add_admin_key(name, raw)
            mask = mask_hero_key(raw)
            log_action = "admin_add_key"
            details = f"name={name} balance=${bal:.2f} mask={mask}"
            done = f"✅ Ключ «{name}» добавлен.\nМаска: {mask}\nБаланс: ${bal:.2f}"
        elif mode == "replace":
            key_id = data.get("admin_key_id")
            row = db_get_admin_key(key_id)
            mask = db_replace_admin_key_secret(key_id, raw)
            name = _row_get(row, "name") or "?"
            log_action = "admin_replace_key"
            details = f"name={name} balance=${bal:.2f} mask={mask}"
            done = f"✅ Секрет «{name}» обновлён.\nМаска: {mask}\nБаланс: ${bal:.2f}"
        else:
            mask = persist_admin_hero_key(raw)
            db_upsert_default_admin_secret(raw)
            log_action = "admin_set_hero_key"
            details = f"balance=${bal:.2f} mask={mask}"
            done = (
                f"✅ Основной ключ обновлён.\nМаска: {mask}\nБаланс: ${bal:.2f}\n\n"
                "Ключ уже в .env и в процессе. Кого назначил на основной — используют новый."
            )
    except Exception as e:
        await msg.answer(
            f"❌ Ключ не работает: {e}\n\nПришли другой или /cancel.",
            reply_markup=_admin_cancel_kb(),
            parse_mode=None,
        )
        return
    await state.clear()
    db_log(admin["telegram_id"], log_action, details)
    await msg.answer(done, reply_markup=admin_kb(), parse_mode=None)


@router.callback_query(F.data.startswith("admin_key_toggle:"))
async def cb_admin_key_toggle(cb: CallbackQuery):
    try:
        if not _admin_only(cb.from_user.id):
            await cb.answer("Доступ запрещён", show_alert=True)
            return
        parts = cb.data.split(":")
        tid = int(parts[1])
        action = parts[2]
        if action == "on":
            default = db_default_admin_key()
            db_assign_user_admin_key(tid, default["id"] if default else None)
            if default is None:
                conn = sqlite3.connect(DB_PATH)
                try:
                    conn.execute("UPDATE users SET use_admin_hero = 1 WHERE telegram_id = ?", (tid,))
                    conn.commit()
                finally:
                    conn.close()
        else:
            db_assign_user_admin_key(tid, None)
        db_log(cb.from_user.id, "admin_key_toggle", f"user={tid} use_admin_hero={'on' if action == 'on' else 'off'}")
        await cb.answer(f"{'✅ Включён' if action == 'on' else '❌ Отключён'} админский ключ для {tid}")
        await cb_admin_keys(cb)
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("admin_user_remove_do:"))
async def cb_admin_user_remove_do(cb: CallbackQuery):
    try:
        admin = _admin_only(cb.from_user.id)
        if not admin:
            await cb.answer("Доступ запрещён", show_alert=True)
            return
        user_db_id = int(cb.data.split(":", 1)[1])
        tid = db_deactivate_user(user_db_id, admin["telegram_id"])
        db_log(admin["telegram_id"], "admin_rm_user", f"tg={tid}")
        await cb.message.edit_text(
            f"✅ Доступ пользователя {tid} отключён.\n"
            "Его почты и история активаций сохранены. Восстановить можно через «Добавить пользователя».",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="👥 К пользователям", callback_data="admin_users:0")],
            ]),
            parse_mode=None,
        )
        await cb.answer()
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("admin_user_remove:"))
async def cb_admin_user_remove(cb: CallbackQuery):
    try:
        admin = _admin_only(cb.from_user.id)
        if not admin:
            await cb.answer("Доступ запрещён", show_alert=True)
            return
        user_db_id = int(cb.data.split(":", 1)[1])
        target = db_get_user_by_id(user_db_id)
        if not target:
            await cb.answer("Пользователь уже удалён", show_alert=True)
            return
        if target["telegram_id"] in (admin["telegram_id"], ADMIN_ID):
            await cb.answer("Себя и владельца бота удалить нельзя", show_alert=True)
            return
        name = target["first_name"] or str(target["telegram_id"])
        await cb.message.edit_text(
            f"➖ Удалить пользователя «{name}» (ID {target['telegram_id']})?\n\n"
            "Доступ к боту будет отключён. Почты и история активаций сохранятся.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Да, отключить доступ", callback_data=f"admin_user_remove_do:{user_db_id}")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin_user_detail:{user_db_id}")],
            ]),
            parse_mode=None,
        )
        await cb.answer()
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await cb.answer()
        else:
            await cb.answer(f"Ошибка: {e}", show_alert=True)
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
        key_status = user_assigned_key_label(u)
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
                text="📈 Статистика пользователя", callback_data=f"admin_user_stats:{user_db_id}:all"
            )],
            [InlineKeyboardButton(
                text="📜 История активаций", callback_data=f"admin_user_activations:{user_db_id}:0"
            )],
            [InlineKeyboardButton(
                text="🔑 Назначить ключ", callback_data=f"admin_assign:{u['telegram_id']}"
            )],
            *([] if u["telegram_id"] in (cb.from_user.id, ADMIN_ID) else [[InlineKeyboardButton(
                text="➖ Удалить пользователя", callback_data=f"admin_user_remove:{user_db_id}"
            )]]),
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
        "🤖 <b>SMS Bot</b>\n\nWolt: Казахстан (+7). Gmail: любая страна, живая цена.",
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
    if db_user_is_disabled(row["telegram_id"]):
        return web.json_response({"ok": True, "ignored": "user_disabled"})
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
    combo = None
    if active_state is not None:
        try:
            combo = (await active_state.get_data()).get("combo")
        except Exception:
            combo = None
    phone_short = phone_local(row["phone"], row["country"]) if row["phone"] else ""
    text = (
        "✅ <b>SMS получен через webhook!</b>\n\n"
        f"Сервис: {html_lib.escape(str(row['service'] or '—'))}\n"
        f"📱 Код: <code>{html_lib.escape(code)}</code>\n"
    )
    if phone_short:
        text += f"Номер: <code>{html_lib.escape(phone_short)}</code>\n"
    if combo == "gmail_then_wolt":
        text += "\nКогда Google готов — жми «Дальше: Wolt»."
    else:
        text += "\nНомер можно завершить и освободить."
    try:
        if _bot is None:
            raise RuntimeError("Telegram bot is not initialized")
        await _replace_ui(
            chat_id,
            text,
            reply_markup=menu_kb(markup_state, is_admin=is_admin, combo=combo),
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
    global _bot, _dp
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
    _dp = dp
    dp.include_router(router)
    webhook_runner = await _start_hero_webhook_server()
    worker = asyncio.create_task(_number_action_worker())
    log.info("SMS bot v2.15 started")
    try:
        await dp.start_polling(_bot)
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        if webhook_runner is not None:
            await webhook_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
