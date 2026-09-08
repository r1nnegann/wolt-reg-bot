import os
import tempfile
import unittest
import asyncio
from pathlib import Path
from types import SimpleNamespace

# Import-time config: keep tests independent of a local .env / production secrets.
os.environ.setdefault("BOT_TOKEN", "test-bot-token")
os.environ.setdefault("HERO_KEY", "test-hero-key-xxxxxxxxxxxxxxxx")
os.environ.setdefault("ADMIN_ID", "1")

import httpx

import bot


class MailLinkTests(unittest.TestCase):
    def test_extracts_latest_wolt_https_link_from_html_and_unescapes(self):
        message = {
            "from": {"address": "no-reply@wolt.com"},
            "subject": "Confirm your Wolt account",
            "html": [
                '<p>Open <a href="https://wolt.com/en-gb/login?token=abc&amp;source=email">Wolt</a></p>'
            ],
            "text": "",
        }
        self.assertEqual(
            bot.mail_extract_link(message),
            "https://wolt.com/en-gb/login?token=abc&source=email",
        )

    def test_extracts_wolt_app_link_when_that_is_the_only_link(self):
        message = {
            "from": "no-reply@wolt.com",
            "subject": "Wolt login",
            "html": '<a href="wolt-app://email-login?login_link=https%3A%2F%2Fwolt.com%2Flogin%3Ftoken%3Dabc">Open</a>',
            "text": "",
        }
        self.assertEqual(
            bot.mail_extract_link(message),
            "wolt-app://email-login?login_link=https%3A%2F%2Fwolt.com%2Flogin%3Ftoken%3Dabc",
        )

    def test_ignores_non_wolt_links(self):
        message = {
            "from": {"address": "notifications@example.com"},
            "subject": "Welcome",
            "html": '<a href="https://example.com/confirm">Confirm</a>',
            "text": "https://example.com/confirm",
        }
        self.assertIsNone(bot.mail_extract_link(message))


class MailApiTests(unittest.TestCase):
    def test_mail_create_fetches_active_domain_and_returns_token(self):
        original_get = bot.httpx.get
        original_post = bot.httpx.post
        calls = []

        def fake_get(url, **kwargs):
            calls.append(("GET", url, kwargs))
            return httpx.Response(
                200,
                json=[
                    {"domain": "inactive.example", "isActive": False, "isPrivate": False},
                    {"domain": "active.example", "isActive": True, "isPrivate": False},
                ],
            )

        def fake_post(url, **kwargs):
            calls.append(("POST", url, kwargs))
            if url.endswith("/accounts"):
                return httpx.Response(201, json={"id": "mail-id", "address": kwargs["json"]["address"]})
            if url.endswith("/token"):
                return httpx.Response(200, json={"token": "jwt-token"})
            raise AssertionError(url)

        bot.httpx.get = fake_get
        bot.httpx.post = fake_post
        try:
            result = bot.mail_create()
        finally:
            bot.httpx.get = original_get
            bot.httpx.post = original_post

        self.assertEqual(result["id"], "mail-id")
        self.assertEqual(result["address"].split("@", 1)[1], "active.example")
        self.assertEqual(result["token"], "jwt-token")
        self.assertTrue(result["password"])
        self.assertEqual([method for method, _, _ in calls], ["GET", "POST", "POST"])

    def test_mail_fetch_marks_401_as_auth_error_for_relogin(self):
        original_get = bot.httpx.get
        bot.httpx.get = lambda *args, **kwargs: httpx.Response(
            401, json={"detail": "Expired JWT Token"}
        )
        try:
            with self.assertRaises(bot.MailTMAuthError):
                bot.mail_fetch("expired-token")
        finally:
            bot.httpx.get = original_get


class AccountDbTests(unittest.TestCase):
    def test_mailbox_account_is_persisted_and_updated(self):
        old_db_path = bot.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            bot.DB_PATH = str(Path(tmp) / "test.db")
            try:
                bot.db_init()
                user = bot.db_get_user(bot.ADMIN_ID)
                account_id = bot.db_add_account(
                    user_id=user["id"],
                    email="random@active.example",
                    password="password123",
                    mail_tm_id="mail-id",
                    mail_token="jwt-token",
                )
                account = bot.db_get_account(account_id, user["id"])
                self.assertEqual(account["status"], "email_waiting")
                bot.db_update_account(account_id, status="email_received", email_link="https://wolt.com/login")
                updated = bot.db_get_account(account_id, user["id"])
                self.assertEqual(updated["status"], "email_received")
                self.assertEqual(updated["email_link"], "https://wolt.com/login")
            finally:
                bot.DB_PATH = old_db_path


class WoltUiTests(unittest.TestCase):
    def test_wolt_email_state_has_cancel_action(self):
        markup = bot.menu_kb(bot.RegState.waiting_wolt_email)
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("cancel", callbacks)

    def test_waiting_sms_menu_has_change_number(self):
        markup = bot.menu_kb(bot.RegState.waiting_sms)
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("change_number", callbacks)
        self.assertIn("cancel", callbacks)

    def test_http_link_rows_skip_wolt_app_scheme(self):
        http_rows = bot._link_button_rows("https://wolt.com/login?token=abc")
        texts = [button.text for row in http_rows for button in row]
        self.assertTrue(any("Открыть" in text for text in texts))
        app_rows = bot._link_button_rows("wolt-app://email-login?x=1")
        urls = [getattr(button, "url", None) for row in app_rows for button in row]
        self.assertTrue(all(url is None for url in urls))

    def test_service_choice_keeps_gmail_and_wolt_entries(self):
        markup = bot.service_choice_kb()
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("start_wolt", callbacks)
        self.assertIn("start_gmail", callbacks)
        labels = [button.text for row in markup.inline_keyboard for button in row]
        self.assertTrue(any("KZ" in text or "+7" in text for text in labels))
        self.assertFalse(any("Wolt" in text and "UK" in text for text in labels))


class PhoneLocalTests(unittest.TestCase):
    def test_strips_kz_and_uk_prefixes_without_masking(self):
        self.assertEqual(bot.phone_local("77001234567", 2), "7001234567")
        self.assertEqual(bot.phone_local("447700900123", 16), "7700900123")
        self.assertEqual(bot.phone_local("447700900123"), "7700900123")
        self.assertEqual(bot.phone_local("77001234567"), "7001234567")

    def test_wolt_service_is_kazakhstan(self):
        self.assertEqual(bot.SERVICES["wolt"]["country"], 2)
        self.assertEqual(bot.SERVICES["gmail"]["country"], 16)
        self.assertEqual(bot.country_label(2), "Казахстан (+7)")


class _FakeState:
    def __init__(self, data):
        self.data = dict(data)
        self.current = bot.RegState.waiting_wolt_email.state

    async def get_state(self):
        return self.current

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def set_state(self, state):
        self.current = state.state if hasattr(state, "state") else state

    async def clear(self):
        self.data.clear()
        self.current = None


class WoltEmailFlowTests(unittest.TestCase):
    def test_email_detection_persists_link_and_starts_number_stage(self):
        old_db_path = bot.DB_PATH
        old_fetch = bot.mail_fetch
        old_full = bot.mail_get_message
        old_send = bot._send_wolt_link
        old_buy = bot._wolt_buy_number
        with tempfile.TemporaryDirectory() as tmp:
            bot.DB_PATH = str(Path(tmp) / "test.db")
            try:
                bot.db_init()
                user = bot.db_get_user(bot.ADMIN_ID)
                account_id = bot.db_add_account(
                    user_id=user["id"],
                    email="random@active.example",
                    password="password123",
                    mail_tm_id="mail-id",
                    mail_token="jwt-token",
                )
                state = _FakeState({
                    "account_id": account_id,
                    "db_user_id": user["id"],
                    "email": "random@active.example",
                    "password": "password123",
                    "mail_token": "jwt-token",
                })
                sent = []
                bought = []

                bot.mail_fetch = lambda token: [{
                    "id": "message-id",
                    "from": {"address": "no-reply@wolt.com"},
                    "subject": "Confirm Wolt",
                    "createdAt": "2026-08-01T00:00:00+00:00",
                }]
                bot.mail_get_message = lambda token, message_id: {
                    "from": {"address": "no-reply@wolt.com"},
                    "html": '<a href="https://wolt.com/login?token=abc">Open</a>',
                }

                async def fake_send(chat_id, email, link):
                    sent.append((chat_id, email, link))

                async def fake_buy(chat_id, fake_state):
                    bought.append((chat_id, fake_state))

                bot._send_wolt_link = fake_send
                bot._wolt_buy_number = fake_buy
                asyncio.run(bot._wait_wolt_email(bot.ADMIN_ID, state))

                account = bot.db_get_account(account_id, user["id"])
                self.assertEqual(account["status"], "email_received")
                self.assertEqual(account["email_link"], "https://wolt.com/login?token=abc")
                self.assertEqual(sent[0][2], "https://wolt.com/login?token=abc")
                self.assertEqual(len(bought), 1)
            finally:
                bot.mail_fetch = old_fetch
                bot.mail_get_message = old_full
                bot._send_wolt_link = old_send
                bot._wolt_buy_number = old_buy
                bot.DB_PATH = old_db_path


class WoltNumberFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_number_stage_buys_rr_and_persists_sms_waiting(self):
        old_db_path = bot.DB_PATH
        old_balance = bot._hero_balance
        old_price = bot.hero_get_price
        old_buy = bot.hero_buy_number
        old_hero = bot._hero
        old_send = bot._safe_send
        old_create_task = bot.asyncio.create_task
        with tempfile.TemporaryDirectory() as tmp:
            bot.DB_PATH = str(Path(tmp) / "test.db")
            try:
                bot.db_init()
                user = bot.db_get_user(bot.ADMIN_ID)
                account_id = bot.db_add_account(
                    user_id=user["id"],
                    email="random@active.example",
                    password="password123",
                    mail_tm_id="mail-id",
                    mail_token="jwt-token",
                    cost=0.0429,
                    status="email_received",
                )
                state = _FakeState({
                    "account_id": account_id,
                    "db_user_id": user["id"],
                    "hero_key": None,
                    "email": "random@active.example",
                    "password": "password123",
                    "mail_token": "jwt-token",
                    "service_label": "Wolt",
                    "service_emoji": "📱",
                    "cost": 0.0429,
                })
                sent = []
                bought = {}
                bot._hero_balance = lambda api_key=None: 2.0
                bot.hero_get_price = lambda service, country, api_key=None: (0.09, 10)
                def fake_buy(service, country, max_price, api_key=None):
                    bought["service"] = service
                    bought["country"] = country
                    return {
                        "activationId": 987654,
                        "phoneNumber": "77001234567",
                        "activationCost": "0.09",
                    }
                bot.hero_buy_number = fake_buy
                bot._hero = lambda *args, **kwargs: "ACCESS_READY"

                async def fake_send(*args, **kwargs):
                    sent.append((args, kwargs))

                bot._safe_send = fake_send
                bot.asyncio.create_task = lambda coro: (coro.close(), None)[1]
                await bot._wolt_buy_number(bot.ADMIN_ID, state)

                account = bot.db_get_account(account_id, user["id"])
                self.assertEqual(account["status"], "sms_waiting")
                self.assertEqual(account["activation_id"], 987654)
                self.assertEqual(account["phone"], "77001234567")
                self.assertEqual(account["country"], 2)
                self.assertEqual(bought["country"], 2)
                self.assertEqual(bought["service"], "rr")
                activation = bot.db_get_recent_activations(1)[0]
                self.assertEqual(activation["service"], "rr")
                self.assertEqual(activation["country"], 2)
                self.assertEqual(activation["status"], "started")
                self.assertEqual(state.current, bot.RegState.waiting_sms.state)
                self.assertTrue(sent)
            finally:
                bot._hero_balance = old_balance
                bot.hero_get_price = old_price
                bot.hero_buy_number = old_buy
                bot._hero = old_hero
                bot._safe_send = old_send
                bot.asyncio.create_task = old_create_task
                bot.DB_PATH = old_db_path


class _FakeMessage:
    def __init__(self):
        self.chat = SimpleNamespace(id=bot.ADMIN_ID)
        self.edits = []

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


class _FakeCallback:
    def __init__(self):
        self.from_user = SimpleNamespace(id=bot.ADMIN_ID)
        self.message = _FakeMessage()
        self.answers = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))


class WoltStartFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_creates_mailbox_before_any_number_purchase(self):
        old_db_path = bot.DB_PATH
        old_balance = bot._hero_balance
        old_price = bot.hero_get_price
        old_mail_create = bot.mail_create
        old_create_task = bot.asyncio.create_task
        with tempfile.TemporaryDirectory() as tmp:
            bot.DB_PATH = str(Path(tmp) / "test.db")
            try:
                bot.db_init()
                state = _FakeState({})
                state.current = bot.RegState.confirming.state
                cb = _FakeCallback()
                bot._hero_balance = lambda api_key=None: 2.0
                bot.hero_get_price = lambda service, country, api_key=None: (0.0429, 10)
                bot.mail_create = lambda: {
                    "id": "mail-id",
                    "address": "random@active.example",
                    "password": "password123",
                    "token": "jwt-token",
                }
                bot.asyncio.create_task = lambda coro: (coro.close(), None)[1]
                await bot._begin_wolt_registration(cb, state)

                user = bot.db_get_user(bot.ADMIN_ID)
                account = bot.db_get_latest_account(user["id"])
                self.assertEqual(account["email"], "random@active.example")
                self.assertEqual(account["status"], "email_waiting")
                self.assertEqual(account["country"], 2)
                self.assertEqual(state.current, bot.RegState.waiting_wolt_email.state)
                self.assertTrue(any("Почта Wolt готова" in text for text, _ in cb.message.edits))
                self.assertTrue(any("KZ" in text or "+7" in text for text, _ in cb.message.edits))
            finally:
                bot._hero_balance = old_balance
                bot.hero_get_price = old_price
                bot.mail_create = old_mail_create
                bot.asyncio.create_task = old_create_task
                bot.DB_PATH = old_db_path


class HeroWebhookTests(unittest.TestCase):
    def test_parses_activation_and_code_from_supported_payload_shapes(self):
        self.assertEqual(
            bot.parse_hero_webhook({"activationId": "42", "code": "12345"}),
            (42, "12345"),
        )
        self.assertEqual(
            bot.parse_hero_webhook({"activation_id": 43, "text": "Your code: 987654"}),
            (43, "987654"),
        )
        self.assertEqual(bot.parse_hero_webhook({"activationId": "bad"}), (None, None))

    def test_claims_activation_once_for_idempotent_webhook_delivery(self):
        old_db_path = bot.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            bot.DB_PATH = str(Path(tmp) / "test.db")
            try:
                bot.db_init()
                user = bot.db_get_user(bot.ADMIN_ID)
                bot.db_add_activation(user["id"], 777, "447700900123", 0.0429, "rr", 16)
                self.assertTrue(bot.db_claim_activation_sms(777, "12345"))
                self.assertFalse(bot.db_claim_activation_sms(777, "12345"))
                row = bot.db_get_activation_for_webhook(777)
                self.assertEqual(row["sms_code"], "12345")
                self.assertEqual(row["status"], "webhook_processing")
            finally:
                bot.DB_PATH = old_db_path

    def test_http_webhook_sends_known_code_once_on_duplicate_delivery(self):
        old_db_path = bot.DB_PATH
        old_token = bot.HERO_WEBHOOK_TOKEN
        old_bot = bot._bot
        old_hero = bot._hero
        old_states = dict(bot._active_states)

        class FakeBot:
            def __init__(self):
                self.sent = []
                self.deleted = []
                self._n = 0

            async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
                self._n += 1
                self.sent.append((chat_id, text, reply_markup))
                return SimpleNamespace(message_id=self._n, chat=SimpleNamespace(id=chat_id))

            async def delete_message(self, chat_id, message_id):
                self.deleted.append((chat_id, message_id))

            async def edit_message_reply_markup(self, **kwargs):
                return None

        async def run_case():
            with tempfile.TemporaryDirectory() as tmp:
                bot.DB_PATH = str(Path(tmp) / "test.db")
                bot.HERO_WEBHOOK_TOKEN = "test-token"
                bot._bot = FakeBot()
                bot._active_states.clear()
                bot._hero = lambda *args, **kwargs: "ACCESS_ACTIVATION"
                bot.db_init()
                user = bot.db_get_user(bot.ADMIN_ID)
                bot.db_add_activation(user["id"], 888, "447700900123", 0.0429, "rr", 16)
                app = bot.web.Application()
                app.router.add_post("/hooks/herosms/{token}", bot.hero_webhook)
                from aiohttp.test_utils import TestClient, TestServer

                async with TestClient(TestServer(app)) as client:
                    first = await client.post(
                        "/hooks/herosms/test-token",
                        json={"activationId": 888, "code": "24680"},
                    )
                    second = await client.post(
                        "/hooks/herosms/test-token",
                        json={"activationId": 888, "code": "24680"},
                    )
                    self.assertEqual(first.status, 200)
                    self.assertEqual(second.status, 200)
                    self.assertEqual(len(bot._bot.sent), 1)
                row = bot.db_get_activation_for_webhook(888)
                self.assertEqual(row["status"], "completed")
                self.assertEqual(row["sms_code"], "24680")

        try:
            asyncio.run(run_case())
        finally:
            bot.DB_PATH = old_db_path
            bot.HERO_WEBHOOK_TOKEN = old_token
            bot._bot = old_bot
            bot._hero = old_hero
            bot._active_states.clear()
            bot._active_states.update(old_states)


class ChangeNumberTests(unittest.IsolatedAsyncioTestCase):
    async def test_swap_cancels_old_activation_and_buys_new(self):
        old_db_path = bot.DB_PATH
        old_balance = bot._hero_balance
        old_price = bot.hero_get_price
        old_buy = bot.hero_buy_number
        old_hero = bot._hero
        old_send = bot._safe_send
        old_create_task = bot.asyncio.create_task
        cancelled = []
        with tempfile.TemporaryDirectory() as tmp:
            bot.DB_PATH = str(Path(tmp) / "test.db")
            try:
                bot.db_init()
                user = bot.db_get_user(bot.ADMIN_ID)
                account_id = bot.db_add_account(
                    user_id=user["id"],
                    email="random@active.example",
                    password="password123",
                    mail_tm_id="mail-id",
                    mail_token="jwt-token",
                    cost=0.09,
                    status="sms_waiting",
                )
                bot.db_update_account(account_id, phone="77001111111", activation_id=111)
                bot.db_add_activation(user["id"], 111, "77001111111", 0.09, "rr", 2)
                state = _FakeState({
                    "account_id": account_id,
                    "db_user_id": user["id"],
                    "hero_key": None,
                    "email": "random@active.example",
                    "email_link": "https://wolt.com/login?token=abc",
                    "service_code": "rr",
                    "service_key": "wolt",
                    "service_label": "Wolt",
                    "service_emoji": "📱",
                    "country": 2,
                    "activation_id": 111,
                    "phone": "77001111111",
                    "sms_watch_id": "old-watch",
                    "cost": 0.09,
                })
                state.current = bot.RegState.waiting_sms.state
                sent = []
                bot._hero_balance = lambda api_key=None: 2.0
                bot.hero_get_price = lambda service, country, api_key=None: (0.09, 10)

                def fake_buy(service, country, max_price, api_key=None):
                    return {
                        "activationId": 222,
                        "phoneNumber": "77002222222",
                        "activationCost": "0.09",
                    }

                def fake_hero(action, api_key=None, **params):
                    if action == "setStatus" and params.get("status") == bot.HERO_CANCEL:
                        cancelled.append(params.get("id"))
                    return "ACCESS_READY"

                async def fake_send(*args, **kwargs):
                    sent.append((args, kwargs))

                bot.hero_buy_number = fake_buy
                bot._hero = fake_hero
                bot._safe_send = fake_send
                bot.asyncio.create_task = lambda coro: (coro.close(), None)[1]
                ok = await bot._swap_number(bot.ADMIN_ID, state)
                self.assertTrue(ok)
                self.assertIn(111, cancelled)
                account = bot.db_get_account(account_id, user["id"])
                self.assertEqual(account["activation_id"], 222)
                self.assertEqual(account["phone"], "77002222222")
                self.assertEqual(account["status"], "sms_waiting")
                self.assertEqual(state.data["activation_id"], 222)
                self.assertEqual(state.current, bot.RegState.waiting_sms.state)
                old_row = bot.db_get_activation_for_webhook(111)
                self.assertEqual(old_row["status"], "cancelled")
                self.assertTrue(any("Новый номер" in str(item) for item in sent))
            finally:
                bot._hero_balance = old_balance
                bot.hero_get_price = old_price
                bot.hero_buy_number = old_buy
                bot._hero = old_hero
                bot._safe_send = old_send
                bot.asyncio.create_task = old_create_task
                bot.DB_PATH = old_db_path

    async def test_wait_sms_stops_when_watch_id_changes(self):
        old_check = bot.sms_check
        old_get = bot.db_get_activation_for_webhook
        try:
            state = _FakeState({
                "activation_id": 1,
                "sms_watch_id": "aaa",
                "hero_key": None,
            })
            state.current = bot.RegState.waiting_sms.state
            called = {"n": 0}

            def fake_check(*args, **kwargs):
                called["n"] += 1
                state.data["sms_watch_id"] = "bbb"
                return False, "WAIT"

            bot.sms_check = fake_check
            bot.db_get_activation_for_webhook = lambda aid: None
            await bot._wait_sms(1, state)
            self.assertGreaterEqual(called["n"], 1)
            self.assertEqual(state.current, bot.RegState.waiting_sms.state)
        finally:
            bot.sms_check = old_check
            bot.db_get_activation_for_webhook = old_get


class UiReplaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_replace_ui_deletes_previous_bot_messages(self):
        old_db_path = bot.DB_PATH
        old_bot = bot._bot

        class FakeBot:
            def __init__(self):
                self.sent = []
                self.deleted = []
                self._n = 0

            async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
                self._n += 1
                self.sent.append((chat_id, text, reply_markup))
                return SimpleNamespace(message_id=self._n, chat=SimpleNamespace(id=chat_id))

            async def delete_message(self, chat_id, message_id):
                self.deleted.append((chat_id, message_id))

            async def edit_message_reply_markup(self, **kwargs):
                return None

        with tempfile.TemporaryDirectory() as tmp:
            bot.DB_PATH = str(Path(tmp) / "test.db")
            fake = FakeBot()
            bot._bot = fake
            try:
                bot.db_init()
                await bot._replace_ui(42, "one")
                await bot._replace_ui(42, "two")
                self.assertEqual([text for _, text, _ in fake.sent], ["one", "two"])
                self.assertEqual(fake.deleted, [(42, 1)])
                self.assertEqual(bot.db_ui_list(42), [2])
            finally:
                bot.DB_PATH = old_db_path
                bot._bot = old_bot


class AdminHeroKeyTests(unittest.TestCase):
    def test_persist_admin_hero_key_updates_env_and_runtime_without_touching_other_vars(self):
        old_env_path = bot.ENV_PATH
        old_hero_key = bot.HERO_KEY
        with tempfile.TemporaryDirectory() as tmp:
            env_path = str(Path(tmp) / ".env")
            Path(env_path).write_text(
                "BOT_TOKEN=keep-me\nHERO_KEY=old-admin-key-xxxxxxxxxxxxxxxx\nHERO_WEBHOOK_TOKEN=hook\n",
                encoding="utf-8",
            )
            bot.ENV_PATH = env_path
            bot.HERO_KEY = "old-admin-key-xxxxxxxxxxxxxxxx"
            try:
                new_key = "new-admin-key-yyyyyyyyyyyyyyyy"
                mask = bot.persist_admin_hero_key(new_key)
                self.assertEqual(mask, "••••yyyy")
                self.assertEqual(bot.HERO_KEY, new_key)
                self.assertEqual(os.environ.get("HERO_KEY"), new_key)
                text = Path(env_path).read_text(encoding="utf-8")
                self.assertIn("BOT_TOKEN=keep-me", text)
                self.assertIn("HERO_WEBHOOK_TOKEN=hook", text)
                self.assertIn(f"HERO_KEY={new_key}", text)
                self.assertNotIn("old-admin-key-xxxxxxxxxxxxxxxx", text)
            finally:
                bot.ENV_PATH = old_env_path
                bot.HERO_KEY = old_hero_key
                os.environ["HERO_KEY"] = old_hero_key

    def test_persist_rejects_short_key_without_writing(self):
        old_env_path = bot.ENV_PATH
        old_hero_key = bot.HERO_KEY
        with tempfile.TemporaryDirectory() as tmp:
            env_path = str(Path(tmp) / ".env")
            Path(env_path).write_text("HERO_KEY=old-admin-key-xxxxxxxxxxxxxxxx\n", encoding="utf-8")
            bot.ENV_PATH = env_path
            bot.HERO_KEY = "old-admin-key-xxxxxxxxxxxxxxxx"
            try:
                with self.assertRaises(ValueError):
                    bot.persist_admin_hero_key("short")
                self.assertEqual(bot.HERO_KEY, "old-admin-key-xxxxxxxxxxxxxxxx")
                self.assertIn("old-admin-key-xxxxxxxxxxxxxxxx", Path(env_path).read_text(encoding="utf-8"))
            finally:
                bot.ENV_PATH = old_env_path
                bot.HERO_KEY = old_hero_key
                os.environ["HERO_KEY"] = old_hero_key


class _AdminKeyMessage:
    def __init__(self, text, user_id=None):
        tid = user_id or bot.ADMIN_ID
        self.text = text
        self.from_user = SimpleNamespace(id=tid)
        self.chat = SimpleNamespace(id=tid)
        self.message_id = 77
        self.answers = []
        self.deleted = False

    async def answer(self, text, reply_markup=None, **kwargs):
        self.answers.append((text, reply_markup, kwargs))
        return SimpleNamespace(message_id=len(self.answers) + 100)

    async def delete(self):
        self.deleted = True


class AdminHeroKeyFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = self._tmp.name
        self.old = {
            "DB_PATH": bot.DB_PATH,
            "ENV_PATH": bot.ENV_PATH,
            "HERO_KEY": bot.HERO_KEY,
            "_hero_balance": bot._hero_balance,
        }
        bot.DB_PATH = str(Path(tmp) / "test.db")
        bot.ENV_PATH = str(Path(tmp) / ".env")
        Path(bot.ENV_PATH).write_text(
            "HERO_KEY=old-admin-key-xxxxxxxxxxxxxxxx\nBOT_TOKEN=keep-me\n",
            encoding="utf-8",
        )
        bot.HERO_KEY = "old-admin-key-xxxxxxxxxxxxxxxx"
        bot.db_init()

    async def asyncTearDown(self):
        bot.DB_PATH = self.old["DB_PATH"]
        bot.ENV_PATH = self.old["ENV_PATH"]
        bot.HERO_KEY = self.old["HERO_KEY"]
        bot._hero_balance = self.old["_hero_balance"]
        os.environ["HERO_KEY"] = self.old["HERO_KEY"]
        self._tmp.cleanup()

    def _logged_blob(self):
        conn = __import__("sqlite3").connect(bot.DB_PATH)
        try:
            rows = conn.execute("SELECT action, details FROM logs").fetchall()
        finally:
            conn.close()
        return " ".join(f"{action} {details}" for action, details in rows)

    async def test_keys_screen_offers_change_admin_key(self):
        cb = _FakeCallback()
        bot._hero_balance = lambda api_key=None: 1.23
        await bot.cb_admin_keys(cb)
        text, kwargs = cb.message.edits[-1]
        markup = kwargs.get("reply_markup")
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("admin_change_hero_key", callbacks)
        self.assertIn("••••xxxx", text)
        self.assertIn("$1.23", text)
        self.assertNotIn("old-admin-key-xxxxxxxxxxxxxxxx", text)

    async def test_valid_key_is_saved_message_deleted_and_secret_not_logged(self):
        new_key = "new-admin-key-yyyyyyyyyyyyyyyy"
        seen = []

        def fake_balance(api_key=None):
            seen.append(api_key)
            if api_key == new_key:
                return 4.56
            raise AssertionError(api_key)

        bot._hero_balance = fake_balance
        state = _FakeState({})
        msg = _AdminKeyMessage(new_key)
        await bot.admin_hero_key_input(msg, state)
        self.assertEqual(seen, [new_key])
        self.assertTrue(msg.deleted)
        self.assertEqual(bot.HERO_KEY, new_key)
        self.assertIn(f"HERO_KEY={new_key}", Path(bot.ENV_PATH).read_text(encoding="utf-8"))
        self.assertIsNone(state.current)
        blob = self._logged_blob()
        self.assertIn("admin_set_hero_key", blob)
        self.assertNotIn(new_key, blob)
        answers = " ".join(text for text, _, _ in msg.answers)
        self.assertIn("••••yyyy", answers)
        self.assertIn("$4.56", answers)
        self.assertNotIn(new_key, answers)

    async def test_invalid_key_does_not_replace_admin_key(self):
        def fake_balance(api_key=None):
            raise ValueError("HeroSMS API error BAD_KEY: Unauthorized")

        bot._hero_balance = fake_balance
        state = _FakeState({})
        await state.set_state(bot.AdminState.waiting_admin_hero_key)
        msg = _AdminKeyMessage("bad-admin-key-zzzzzzzzzzzzzzzz")
        await bot.admin_hero_key_input(msg, state)
        self.assertTrue(msg.deleted)
        self.assertEqual(bot.HERO_KEY, "old-admin-key-xxxxxxxxxxxxxxxx")
        self.assertIn("old-admin-key-xxxxxxxxxxxxxxxx", Path(bot.ENV_PATH).read_text(encoding="utf-8"))
        self.assertEqual(state.current, bot.AdminState.waiting_admin_hero_key.state)
        answers = " ".join(text for text, _, _ in msg.answers)
        self.assertIn("не работает", answers.lower())
        self.assertNotIn("bad-admin-key-zzzzzzzzzzzzzzzz", answers)
        self.assertNotIn("bad-admin-key-zzzzzzzzzzzzzzzz", self._logged_blob())

    async def test_cancel_text_does_not_persist_new_key(self):
        bot._hero_balance = lambda api_key=None: (_ for _ in ()).throw(AssertionError("should not validate"))
        state = _FakeState({})
        await state.set_state(bot.AdminState.waiting_admin_hero_key)
        msg = _AdminKeyMessage("/cancel")
        await bot.admin_hero_key_input(msg, state)
        self.assertIsNone(state.current)
        self.assertFalse(msg.deleted)
        self.assertEqual(bot.HERO_KEY, "old-admin-key-xxxxxxxxxxxxxxxx")
        answers = " ".join(text for text, _, _ in msg.answers)
        self.assertIn("отменена", answers.lower())

    async def test_non_admin_cannot_change_admin_key(self):
        conn = __import__("sqlite3").connect(bot.DB_PATH)
        try:
            conn.execute(
                "INSERT INTO users (telegram_id, first_name, is_admin, use_admin_hero) VALUES (?, ?, 0, 0)",
                (4242, "Guest"),
            )
            conn.commit()
        finally:
            conn.close()
        cb = _FakeCallback()
        cb.from_user = SimpleNamespace(id=4242)
        cb.data = "admin_change_hero_key"
        state = _FakeState({})
        await bot.cb_admin_change_hero_key(cb, state)
        self.assertTrue(any("запрещ" in str(item).lower() for item in cb.answers))
        self.assertNotEqual(state.current, bot.AdminState.waiting_admin_hero_key.state)
        msg = _AdminKeyMessage("stolen-admin-key-wwwwwwwwwwwwww", user_id=4242)
        await bot.admin_hero_key_input(msg, state)
        self.assertEqual(bot.HERO_KEY, "old-admin-key-xxxxxxxxxxxxxxxx")
        self.assertFalse(msg.deleted)


if __name__ == "__main__":
    unittest.main()
