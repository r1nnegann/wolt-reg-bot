import os
import tempfile
import unittest
import asyncio
from pathlib import Path
from types import SimpleNamespace

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

    def test_completed_combo_offers_wolt_next(self):
        markup = bot.menu_kb(bot.RegState.completed, combo="gmail_then_wolt")
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("combo_wolt", callbacks)
        self.assertIn("complete", callbacks)
        labels = [button.text for row in markup.inline_keyboard for button in row]
        self.assertTrue(any("Wolt" in text for text in labels))
        plain = bot.menu_kb(bot.RegState.completed)
        plain_cb = [button.callback_data for row in plain.inline_keyboard for button in row]
        self.assertNotIn("combo_wolt", plain_cb)

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
        self.assertIn("start_wolt_sms", callbacks)
        self.assertIn("start_gmail", callbacks)
        self.assertIn("start_combo", callbacks)
        labels = [button.text for row in markup.inline_keyboard for button in row]
        self.assertTrue(any("KZ" in text or "+7" in text for text in labels))
        self.assertFalse(any("Wolt" in text and "UK" in text for text in labels))
        self.assertTrue(any("Gmail" in text for text in labels))
        self.assertFalse(any("Gmail" in text and "+44" in text for text in labels))
        self.assertTrue(any("Google" in text and "Wolt" in text for text in labels))
        self.assertTrue(any("почта" in text.lower() and "SMS" in text for text in labels))
        self.assertTrue(any("только SMS" in text for text in labels))


class PhoneLocalTests(unittest.TestCase):
    def test_strips_kz_and_uk_prefixes_without_masking(self):
        self.assertEqual(bot.phone_local("77001234567", 2), "7001234567")
        self.assertEqual(bot.phone_local("447700900123", 16), "7700900123")
        self.assertEqual(bot.phone_local("447700900123"), "7700900123")
        self.assertEqual(bot.phone_local("77001234567"), "7001234567")

    def test_strips_prefix_for_gmail_country_from_meta(self):
        old = dict(bot._COUNTRY_META)
        try:
            bot._COUNTRY_META[4] = {"eng": "Philippines", "rus": "Филиппины", "prefix": "63"}
            self.assertEqual(bot.phone_local("639171234567", 4), "9171234567")
            self.assertEqual(bot._calling_code(4, "Philippines"), "63")
            self.assertIn("+63", bot.country_label(4))
        finally:
            bot._COUNTRY_META.clear()
            bot._COUNTRY_META.update(old)

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
                    if action == "getStatus":
                        return "STATUS_CANCEL" if cancelled else "STATUS_WAIT_CODE"
                    if action == "setStatus" and params.get("status") == bot.HERO_CANCEL:
                        cancelled.append(params.get("id"))
                        return "ACCESS_CANCEL"
                    return "ACCESS_READY"

                async def fake_send(*args, **kwargs):
                    sent.append((args, kwargs))

                bot.hero_buy_number = fake_buy
                bot._hero = fake_hero
                bot._safe_send = fake_send
                bot.asyncio.create_task = lambda coro: (coro.close(), None)[1]
                ok = await bot._swap_number(bot.ADMIN_ID, state)
                self.assertTrue(ok)
                self.assertEqual(cancelled, [])
                self.assertEqual(state.current, bot.RegState.waiting_sms.state)
                self.assertEqual(state.data["activation_id"], 222)
                self.assertEqual(bot.db_get_activation_for_webhook(111)["status"], "cancel_pending")
                self.assertEqual(bot.db_get_activation_for_webhook(222)["status"], "started")
                account = bot.db_get_account(account_id, user["id"])
                self.assertEqual(account["activation_id"], 222)
                self.assertEqual(account["phone"], "77002222222")
                self.assertEqual(account["status"], "sms_waiting")
                action = bot.db_number_action(111)
                self.assertEqual(action["replacement_activation_id"], 222)
                self.assertGreaterEqual(action["due_at"] - action["created_at"], 120)
                await bot._process_number_action(action, state=state)
                self.assertEqual(cancelled, [])
                import sqlite3
                with sqlite3.connect(bot.DB_PATH) as conn:
                    conn.execute("UPDATE pending_number_actions SET due_at = ? WHERE activation_id = ?",
                                 (bot.time.time() - 1, 111))
                await bot._process_number_action(bot.db_number_action(111), state=state)
                self.assertIn(111, cancelled)
                self.assertIsNone(bot.db_number_action(111))
                self.assertEqual(state.data["activation_id"], 222)
                self.assertEqual(state.current, bot.RegState.waiting_sms.state)
                old_row = bot.db_get_activation_for_webhook(111)
                self.assertEqual(old_row["status"], "cancelled")
                self.assertEqual(account["activation_id"], bot.db_get_account(account_id, user["id"])["activation_id"])
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


class DelayedNumberActionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db, self.old_hero, self.old_send = bot.DB_PATH, bot._hero, bot._safe_send
        self.old_purchase, self.old_watch, self.old_dp, self.old_bot = bot._purchase_number, bot._start_sms_watch, bot._dp, bot._bot
        self.old_pending = dict(bot._pending_number_states)
        bot._pending_number_states.clear()
        bot.DB_PATH = str(Path(self.tmp.name) / "test.db")
        bot.db_init()
        self.user = bot.db_get_user(bot.ADMIN_ID)
        self.aid = 45678
        bot.db_add_activation(self.user["id"], self.aid, "77001111111", 0.1, "rr", 2)
        self.calls = []
        self.sent = []
        async def fake_send(*args, **kwargs):
            self.sent.append((args, kwargs))
        bot._safe_send = fake_send

    async def asyncTearDown(self):
        bot.DB_PATH, bot._hero, bot._safe_send = self.old_db, self.old_hero, self.old_send
        bot._purchase_number, bot._start_sms_watch = self.old_purchase, self.old_watch
        bot._dp, bot._bot = self.old_dp, self.old_bot
        bot._pending_number_states.clear()
        bot._pending_number_states.update(self.old_pending)
        self.tmp.cleanup()

    def due(self):
        import sqlite3
        with sqlite3.connect(bot.DB_PATH) as conn:
            conn.execute("UPDATE pending_number_actions SET due_at = ? WHERE activation_id = ?",
                         (bot.time.time() - 1, self.aid))
        return bot.db_number_action(self.aid)

    async def test_cancel_button_queues_without_early_api_and_survives_double_click(self):
        state = _FakeState({"activation_id": self.aid, "db_user_id": self.user["id"]})
        state.current = bot.RegState.waiting_sms.state
        cb = _FakeCallback()
        bot._hero = lambda *args, **kwargs: self.fail("HeroSMS must not be called before due")
        await bot.cb_cancel(cb, state)
        job = bot.db_number_action(self.aid)
        self.assertIsNotNone(job)
        self.assertEqual(job["kind"], "cancel")
        self.assertGreaterEqual(job["due_at"] - job["created_at"], 120)
        self.assertEqual(bot.db_get_activation_for_webhook(self.aid)["status"], "cancel_pending")
        self.assertEqual(state.current, bot.RegState.cancelling.state)
        self.assertFalse(bot.db_claim_activation_sms(self.aid, "12345"))
        await bot.cb_cancel(cb, state)
        self.assertEqual(len(cb.answers), 2)
        self.assertEqual(bot.db_number_action(self.aid)["due_at"], job["due_at"])
        await bot._process_number_action(job, state=state)
        self.assertEqual(bot.db_get_activation_for_webhook(self.aid)["status"], "cancel_pending")

    async def test_denial_and_silent_response_do_not_cancel_or_affect_replacement(self):
        state = _FakeState({"activation_id": self.aid, "db_user_id": self.user["id"], "service_code": "rr", "country": 2})
        state.current = bot.RegState.waiting_sms.state
        purchases = []
        async def buy(*args, **kwargs):
            purchases.append(args)
            return {"activationId": 98765, "phoneNumber": "77002222222", "activationCost": "0.11"}
        async def watch(*args, **kwargs):
            pass
        bot._purchase_number, bot._start_sms_watch = buy, watch
        self.assertTrue(await bot._swap_number(bot.ADMIN_ID, state))
        self.assertEqual(len(purchases), 1)
        async def no_buy(*args, **kwargs):
            self.fail("background cancellation must never buy another number")
        bot._purchase_number = no_buy
        def ignored(action, api_key=None, **kwargs):
            self.calls.append((action, kwargs))
            return "STATUS_WAIT_CODE" if action == "getStatus" else "ACCESS_READY"
        bot._hero = ignored
        await bot._process_number_action(self.due(), state=state)
        self.assertEqual(bot.db_get_activation_for_webhook(self.aid)["status"], "cancel_pending")
        self.assertEqual(bot.db_number_action(self.aid)["attempts"], 1)
        self.assertEqual(len([x for x in self.calls if x[0] == "setStatus"]), 1)
        self.assertEqual(state.current, bot.RegState.waiting_sms.state)
        self.assertEqual(state.data["activation_id"], 98765)
        def early(action, api_key=None, **kwargs):
            if action == "setStatus":
                raise ValueError("EARLY_CANCEL_DENIED")
            return "STATUS_WAIT_CODE"
        bot._hero = early
        await bot._process_number_action(self.due(), state=state)
        self.assertEqual(bot.db_number_action(self.aid)["attempts"], 2)
        self.assertEqual(bot.db_get_activation_for_webhook(self.aid)["status"], "cancel_pending")
        self.assertEqual(bot.db_get_activation_for_webhook(98765)["status"], "started")

    async def test_queue_survives_restart_without_buying_on_confirmed_cancel(self):
        state = _FakeState({"activation_id": self.aid, "db_user_id": self.user["id"],
                            "service_code": "rr", "service_label": "Wolt", "country": 2})
        state.current = bot.RegState.waiting_sms.state
        purchases, watches = [], []
        async def buy(*args, **kwargs):
            purchases.append(args)
            return {"activationId": 98765, "phoneNumber": "77002222222", "activationCost": "0.11"}
        async def watch(chat_id, state):
            watches.append(chat_id)
        bot._purchase_number, bot._start_sms_watch = buy, watch
        self.assertTrue(await bot._swap_number(bot.ADMIN_ID, state))
        self.assertEqual(len(purchases), 1)
        self.assertEqual(watches, [bot.ADMIN_ID])
        bot._pending_number_states.clear()  # simulate process restart
        new_state = _FakeState({})
        new_state.current = None
        class FakeFSM:
            async def get_context(self, **kwargs):
                return new_state
        bot._dp = SimpleNamespace(fsm=FakeFSM())
        bot._bot = SimpleNamespace()
        def cancelled(action, api_key=None, **kwargs):
            self.calls.append((action, kwargs))
            return "STATUS_CANCEL" if action == "getStatus" else "ACCESS_CANCEL"
        bot._hero = cancelled
        async def no_buy(*args, **kwargs):
            self.fail("worker must not purchase a second replacement")
        bot._purchase_number = no_buy
        await bot._process_number_action(self.due())
        self.assertEqual(len(purchases), 1)
        self.assertEqual(watches, [bot.ADMIN_ID])
        self.assertIsNone(new_state.current)
        self.assertIsNone(bot.db_number_action(self.aid))
        self.assertEqual(bot.db_get_activation_for_webhook(self.aid)["status"], "cancelled")
        self.assertEqual(bot.db_get_activation_for_webhook(98765)["status"], "started")

    async def test_failed_job_manual_retry_waits_two_minutes(self):
        bot.db_queue_number_action(self.aid, bot.ADMIN_ID, "cancel", None)
        self.due()
        bot.db_number_action_retry(self.aid, delay=20, failure=True)
        cb = _FakeCallback()
        cb.data = f"number_retry_cancel:{self.aid}"
        await bot.cb_number_retry_cancel(cb)
        action = bot.db_number_action(self.aid)
        self.assertEqual(action["status"], "queued")
        self.assertGreaterEqual(action["due_at"] - bot.time.time(), 119)
        self.assertEqual(bot.db_get_activation_for_webhook(self.aid)["status"], "cancel_pending")

    async def test_cancel_uses_original_purchase_key_even_if_current_key_changed(self):
        bot.db_add_activation(self.user["id"], 56789, "77003333333", 0.1, "rr", 2,
                              api_key="original-key")
        action = bot.db_queue_number_action(56789, bot.ADMIN_ID, "cancel", "new-key")
        self.assertEqual(action["api_key"], "original-key")
        self.assertEqual(bot.db_get_activation_for_webhook(56789)["api_key"], "original-key")

    async def test_failed_remote_cancel_keeps_mailbox_and_explicit_retry(self):
        acc_id = bot.db_add_account(self.user["id"], "test@mailer.test", "pw", "mail-id", "token",
                                    status="sms_waiting")
        bot.db_update_account(acc_id, phone="77001111111", activation_id=self.aid)
        bot.db_queue_number_action(self.aid, bot.ADMIN_ID, "cancel", None, {"account_id": acc_id})
        def denied(action, api_key=None, **kwargs):
            if action == "setStatus":
                return "EARLY_CANCEL_DENIED"
            return "STATUS_WAIT_CODE"
        bot._hero = denied
        import sqlite3
        with sqlite3.connect(bot.DB_PATH) as conn:
            conn.execute("UPDATE pending_number_actions SET attempts = ? WHERE activation_id = ?",
                         (bot.CANCEL_MAX_ATTEMPTS - 1, self.aid))
        await bot._process_number_action(self.due())
        self.assertEqual(bot.db_number_action(self.aid)["status"], "failed")
        self.assertEqual(bot.db_get_activation_for_webhook(self.aid)["status"], "cancel_failed")
        self.assertEqual(bot.db_get_account(acc_id, self.user["id"])["status"], "cancel_failed")
        async def forbidden_mail_delete(*args, **kwargs):
            self.fail("mailbox must not be deleted while remote cancellation failed")
        old_mail = bot.mail_delete
        bot.mail_delete = forbidden_mail_delete
        try:
            deleted, failed = await bot._delete_all_user_mailboxes(self.user["id"], acc_id)
            self.assertEqual((deleted, failed), (0, 1))
        finally:
            bot.mail_delete = old_mail
        action = bot.db_number_action_requeue(self.aid, bot.ADMIN_ID)
        self.assertGreaterEqual(action["due_at"] - bot.time.time(), 119)
        self.assertEqual(bot.db_get_account(acc_id, self.user["id"])["status"], "cancel_pending")

    async def test_unrelated_activation_never_reused_as_replacement(self):
        state = _FakeState({"activation_id": self.aid, "db_user_id": self.user["id"],
                            "service_code": "rr", "country": 2})
        state.current = bot.RegState.waiting_sms.state
        buys = []
        async def buy(*args, **kwargs):
            buys.append(1)
            return {"activationId": 99999, "phoneNumber": "77009999999", "activationCost": "0.11"}
        async def watch(*args):
            pass
        bot._purchase_number, bot._start_sms_watch = buy, watch
        await bot._swap_number(bot.ADMIN_ID, state)
        bot.db_add_activation(self.user["id"], 77777, "77007777777", 0.1, "rr", 2)
        bot._hero = lambda action, **kwargs: "STATUS_CANCEL"
        await bot._process_number_action(self.due(), state=state)
        self.assertEqual(len(buys), 1)
        self.assertEqual(state.data["activation_id"], 99999)
        self.assertEqual(bot.db_get_activation_for_webhook(77777)["status"], "started")

    async def test_three_fast_swaps_queue_each_old_number_and_keep_current_panel(self):
        account_id = bot.db_add_account(self.user["id"], "test@wolt.test", "pw", "mail-id", "token",
                                        status="sms_waiting")
        bot.db_update_account(account_id, phone="77001111111", activation_id=self.aid)
        state = _FakeState({"activation_id": self.aid, "db_user_id": self.user["id"],
                            "service_code": "rr", "country": 2, "account_id": account_id,
                            "phone": "77001111111"})
        state.current = bot.RegState.waiting_sms.state
        purchases = []
        async def buy(*args, **kwargs):
            new_aid = 56789 + len(purchases)
            purchases.append(new_aid)
            return {"activationId": new_aid, "phoneNumber": f"7700{new_aid:07d}",
                    "activationCost": "0.11"}
        async def watch(*args, **kwargs):
            pass
        bot._purchase_number, bot._start_sms_watch = buy, watch
        bot._hero = lambda *args, **kwargs: self.fail("no HeroSMS cancel before due")
        for old_aid, new_aid in [(self.aid, 56789), (56789, 56790), (56790, 56791)]:
            cb = _FakeCallback()
            cb.data = f"change_number:{old_aid}"
            await bot.cb_change_number(cb, state)
            self.assertEqual(len(cb.answers), 1)
            self.assertEqual(state.data["activation_id"], new_aid)
            self.assertEqual(state.current, bot.RegState.waiting_sms.state)
            self.assertEqual(bot.db_number_action(old_aid)["replacement_activation_id"], new_aid)
            self.assertGreaterEqual(bot.db_number_action(old_aid)["due_at"] - bot.time.time(), 119)
        stale = _FakeCallback()
        stale.data = f"change_number:{self.aid}"
        await bot.cb_change_number(stale, state)
        self.assertEqual(len(purchases), 3)
        self.assertTrue(stale.answers[-1][1].get("show_alert"))
        account = bot.db_get_account(account_id, self.user["id"])
        self.assertEqual(account["activation_id"], 56791)
        self.assertEqual(account["status"], "sms_waiting")
        pending = bot.db_user_number_actions(self.user["id"])
        self.assertEqual({row["activation_id"] for row in pending}, {self.aid, 56789, 56790})
        text, kb = bot._number_actions_view(self.user, has_active_number=True)
        self.assertIn("Отмены номеров: 3", text)
        self.assertIn("7000056789", text)
        self.assertTrue(any(button.callback_data == "status" for row in kb.inline_keyboard for button in row))
        bot._hero = lambda action, **kwargs: "STATUS_CANCEL"
        sent_count = len(self.sent)
        await bot._process_number_action(self.due(), state=state)
        self.assertEqual(len(self.sent), sent_count)  # background success must not overwrite 56791
        self.assertEqual(state.data["activation_id"], 56791)
        self.assertEqual(bot.db_get_account(account_id, self.user["id"])["activation_id"], 56791)
        import sqlite3
        with sqlite3.connect(bot.DB_PATH) as conn:
            conn.execute("UPDATE pending_number_actions SET due_at = ?, attempts = ? WHERE activation_id = ?",
                         (bot.time.time() - 1, bot.CANCEL_MAX_ATTEMPTS - 1, 56789))
        bot._hero = lambda action, **kwargs: "STATUS_WAIT_CODE" if action == "getStatus" else "ACCESS_READY"
        notifications = []
        old_raw = bot._safe_send_raw
        async def notify(*args, **kwargs):
            notifications.append(args)
        bot._safe_send_raw = notify
        try:
            await bot._process_number_action(bot.db_number_action(56789), state=state)
        finally:
            bot._safe_send_raw = old_raw
        self.assertEqual(bot.db_number_action(56789)["status"], "failed")
        self.assertEqual(len(notifications), 1)
        self.assertEqual(len(self.sent), sent_count)
        self.assertEqual(state.data["activation_id"], 56791)
        self.assertEqual(bot.db_get_account(account_id, self.user["id"])["status"], "sms_waiting")
        text, kb = bot._number_actions_view(self.user, has_active_number=True)
        self.assertIn("не подтверждена", text)
        self.assertTrue(any(button.callback_data == "number_retry_cancel:56789"
                            for row in kb.inline_keyboard for button in row))

    async def test_cancel_latest_number_while_previous_swaps_are_pending(self):
        state = _FakeState({"activation_id": self.aid, "db_user_id": self.user["id"],
                            "service_code": "rr", "country": 2, "phone": "77001111111"})
        state.current = bot.RegState.waiting_sms.state
        async def buy(*args, **kwargs):
            return {"activationId": 98765, "phoneNumber": "77002222222", "activationCost": "0.11"}
        async def watch(*args, **kwargs):
            pass
        bot._purchase_number, bot._start_sms_watch = buy, watch
        await bot._swap_number(bot.ADMIN_ID, state)
        cb = _FakeCallback()
        await bot.cb_cancel(cb, state)
        self.assertEqual(state.current, bot.RegState.cancelling.state)
        self.assertEqual(bot.db_number_action(98765)["kind"], "cancel")
        self.assertEqual(len(bot.db_user_number_actions(self.user["id"])), 2)
        self.assertIsNotNone(bot.db_number_action(self.aid))

    async def test_old_pending_cancel_does_not_hijack_failing_new_purchase(self):
        bot.db_queue_number_action(self.aid, bot.ADMIN_ID, "cancel", None)
        async def failed_buy(*args, **kwargs):
            raise ValueError("NO_NUMBERS")
        bot._purchase_number = failed_buy
        gmail = _FakeState({"service_code": "go", "service_key": "gmail", "db_user_id": self.user["id"],
                            "country": 16, "cost": 0.1})
        gmail.current = bot.RegState.confirming.state
        cb = _FakeCallback()
        await bot.cb_confirm_buy(cb, gmail)
        self.assertIsNone(gmail.current)
        self.assertTrue(any("NO_NUMBERS" in text for text, _ in cb.message.edits))
        self.assertEqual(bot.db_number_action(self.aid)["kind"], "cancel")
        account_id = bot.db_add_account(self.user["id"], "test@retry.test", "pw", "mail-id", "token",
                                        status="email_received")
        wolt = _FakeState({"account_id": account_id, "db_user_id": self.user["id"],
                           "email": "test@retry.test", "email_link": "https://wolt.com/login"})
        await bot._wolt_buy_number(bot.ADMIN_ID, wolt)
        self.assertIsNone(wolt.current)
        self.assertEqual(bot.db_get_account(account_id, self.user["id"])["status"], "number_error")
        self.assertIn("NO_NUMBERS", self.sent[-1][0][1])
        self.assertIsNotNone(bot.db_number_action(self.aid))

    async def test_slow_hero_request_does_not_block_other_async_handlers(self):
        import threading
        bot.db_queue_number_action(self.aid, bot.ADMIN_ID, "cancel", None)
        started, release = threading.Event(), threading.Event()
        def slow_hero(action, api_key=None, **kwargs):
            started.set()
            release.wait(timeout=2)
            return "STATUS_CANCEL"
        bot._hero = slow_hero
        task = asyncio.create_task(bot._process_number_action(self.due()))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            await asyncio.wait_for(asyncio.sleep(0.01), timeout=0.5)
            self.assertFalse(task.done())
        finally:
            release.set()
            await asyncio.wait_for(task, timeout=2)
        self.assertEqual(bot.db_get_activation_for_webhook(self.aid)["status"], "cancelled")


class UiReplaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_click_cannot_purge_or_invoke_newer_panel(self):
        old_db = bot.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            bot.DB_PATH = str(Path(tmp) / "test.db")
            try:
                bot.db_init()
                bot.db_ui_add(42, 22)
                event = SimpleNamespace(message=SimpleNamespace(
                    chat=SimpleNamespace(id=42), message_id=11))
                calls = []
                async def handler(*args, **kwargs):
                    calls.append(1)
                await bot.UiCleanupMiddleware()(handler, event, {})
                self.assertEqual(calls, [])
                self.assertEqual(bot.db_ui_list(42), [22])
            finally:
                bot.DB_PATH = old_db

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
        self.assertIn("admin_key_add", callbacks)
        self.assertTrue(any(data.startswith("admin_key_view:") for data in callbacks))
        self.assertTrue(any(data.startswith("admin_assign:") for data in callbacks))
        self.assertIn("••••xxxx", text)
        self.assertIn("$1.23", text)
        self.assertIn("Основной", text)
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

    async def test_named_key_is_added_without_replacing_env_default(self):
        extra = "reserve-hero-key-zzzzzzzzzzzzzzzz"
        bot._hero_balance = lambda api_key=None: 9.01 if api_key == extra else 1.23
        state = _FakeState({})
        await state.set_state(bot.AdminState.waiting_admin_key_name)
        await bot.admin_key_name_input(_AdminKeyMessage("Резерв"), state)
        self.assertEqual(state.current, bot.AdminState.waiting_admin_hero_key.state)
        self.assertEqual(state.data.get("pending_key_name"), "Резерв")
        msg = _AdminKeyMessage(extra)
        await bot.admin_hero_key_input(msg, state)
        self.assertTrue(msg.deleted)
        self.assertIsNone(state.current)
        self.assertEqual(bot.HERO_KEY, "old-admin-key-xxxxxxxxxxxxxxxx")
        self.assertIn("HERO_KEY=old-admin-key-xxxxxxxxxxxxxxxx", Path(bot.ENV_PATH).read_text(encoding="utf-8"))
        keys = {row["name"]: row for row in bot.db_list_admin_keys()}
        self.assertIn("Основной", keys)
        self.assertIn("Резерв", keys)
        self.assertEqual(keys["Резерв"]["api_key"], extra)
        self.assertFalse(keys["Резерв"]["is_default"])
        answers = " ".join(text for text, _, _ in msg.answers)
        self.assertIn("Резерв", answers)
        self.assertNotIn(extra, answers)
        self.assertNotIn(extra, self._logged_blob())

    async def test_assign_named_key_changes_resolved_secret(self):
        extra = "reserve-hero-key-zzzzzzzzzzzzzzzz"
        conn = __import__("sqlite3").connect(bot.DB_PATH)
        try:
            conn.execute(
                "INSERT INTO users (telegram_id, first_name, is_admin, use_admin_hero) VALUES (?, ?, 0, 0)",
                (4242, "Guest"),
            )
            conn.commit()
        finally:
            conn.close()
        extra_id = bot.db_add_admin_key("Резерв", extra)
        bot.db_assign_user_admin_key(4242, extra_id)
        guest = bot.db_get_user(4242)
        admin = bot.db_get_user(bot.ADMIN_ID)
        self.assertEqual(bot.get_user_hero_key(guest), extra)
        self.assertEqual(bot.get_user_hero_key(admin), "old-admin-key-xxxxxxxxxxxxxxxx")
        self.assertIn("Резерв", bot.user_assigned_key_label(guest))
        bot.db_assign_user_admin_key(4242, None)
        guest = bot.db_get_user(4242)
        self.assertIsNone(bot.get_user_hero_key(guest))
        self.assertIn("нет ключа", bot.user_assigned_key_label(guest))

    async def test_cannot_delete_default_key(self):
        default = bot.db_default_admin_key()
        with self.assertRaises(ValueError):
            bot.db_delete_admin_key(default["id"])
        extra_id = bot.db_add_admin_key("Резерв", "reserve-hero-key-zzzzzzzzzzzzzzzz")
        bot.db_assign_user_admin_key(bot.ADMIN_ID, extra_id)
        with self.assertRaises(ValueError):
            bot.db_delete_admin_key(extra_id)
        bot.db_assign_user_admin_key(bot.ADMIN_ID, default["id"])
        self.assertEqual(bot.db_delete_admin_key(extra_id), "Резерв")
        names = [row["name"] for row in bot.db_list_admin_keys()]
        self.assertNotIn("Резерв", names)

    async def test_set_default_updates_env(self):
        extra = "reserve-hero-key-zzzzzzzzzzzzzzzz"
        extra_id = bot.db_add_admin_key("Резерв", extra)
        bot.db_set_default_admin_key(extra_id)
        self.assertEqual(bot.HERO_KEY, extra)
        self.assertIn(f"HERO_KEY={extra}", Path(bot.ENV_PATH).read_text(encoding="utf-8"))
        default = bot.db_default_admin_key()
        self.assertEqual(default["id"], extra_id)
        self.assertEqual(default["name"], "Резерв")


class AdminUserDeleteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = bot.DB_PATH
        bot.DB_PATH = str(Path(self.tmp.name) / "test.db")
        bot.db_init()
        import sqlite3
        with sqlite3.connect(bot.DB_PATH) as conn:
            conn.execute("INSERT INTO users (telegram_id, first_name, is_active) VALUES (4242, 'Guest', 1)")
        self.guest = bot.db_get_user(4242)

    async def asyncTearDown(self):
        bot.DB_PATH = self.old_db
        self.tmp.cleanup()

    async def test_deletion_needs_confirmation_and_revokes_access_without_losing_history(self):
        import sqlite3
        bot.db_add_activation(self.guest["id"], 12345, "77001234567", 0.04, "rr", 2)
        cb = _FakeCallback()
        cb.data = f"admin_user_remove:{self.guest['id']}"
        await bot.cb_admin_user_remove(cb)
        self.assertIsNotNone(bot.db_get_user(4242))
        kb = cb.message.edits[-1][1]["reply_markup"]
        self.assertIn(f"admin_user_remove_do:{self.guest['id']}", [b.callback_data for row in kb.inline_keyboard for b in row])
        cb.data = f"admin_user_remove_do:{self.guest['id']}"
        await bot.cb_admin_user_remove_do(cb)
        self.assertIsNone(bot.db_get_user(4242))
        self.assertTrue(bot.db_user_is_disabled(4242))
        with sqlite3.connect(bot.DB_PATH) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM activations WHERE user_id=?", (self.guest["id"],)).fetchone()[0], 1)
        state = _FakeState({})
        msg = _AdminKeyMessage("/start", user_id=4242)
        await bot.cmd_start(msg, state)
        self.assertTrue(bot.db_user_is_disabled(4242))
        self.assertIn("отключён", msg.answers[-1][0])

    async def test_cannot_delete_owner_and_non_admin_cannot_use_buttons(self):
        with self.assertRaises(ValueError):
            bot.db_deactivate_user(bot.db_get_user(bot.ADMIN_ID)["id"], bot.ADMIN_ID)
        cb = _FakeCallback()
        cb.from_user = SimpleNamespace(id=4242)
        cb.data = f"admin_user_remove_do:{bot.db_get_user(bot.ADMIN_ID)['id']}"
        await bot.cb_admin_user_remove_do(cb)
        self.assertTrue(any("запрещ" in str(item).lower() for item in cb.answers))
        self.assertIsNotNone(bot.db_get_user(bot.ADMIN_ID))


class MailBulkDeleteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db, self.old_delete, self.old_login = bot.DB_PATH, bot.mail_delete, bot.mail_login
        bot.DB_PATH = str(Path(self.tmp.name) / "test.db")
        bot.db_init()
        import sqlite3
        with sqlite3.connect(bot.DB_PATH) as conn:
            conn.execute("INSERT INTO users (telegram_id, first_name, is_active) VALUES (4242, 'Other', 1)")
        self.uid = bot.db_get_user(bot.ADMIN_ID)["id"]
        self.other_uid = bot.db_get_user(4242)["id"]

    async def asyncTearDown(self):
        bot.DB_PATH, bot.mail_delete, bot.mail_login = self.old_db, self.old_delete, self.old_login
        self.tmp.cleanup()

    def add(self, uid, suffix, status="completed"):
        return bot.db_add_account(uid, f"{suffix}@test.invalid", "pass", f"tm-{suffix}", "token", status=status)

    async def test_bulk_only_owned_snapshot_and_keeps_remote_failures(self):
        first = self.add(self.uid, "first")
        second = self.add(self.uid, "second")
        self.add(self.other_uid, "other")
        calls = []
        def fake_delete(mid, token):
            calls.append(mid)
            if mid == "tm-second":
                raise RuntimeError("network down")
        bot.mail_delete = fake_delete
        deleted, failed = await bot._delete_all_user_mailboxes(self.uid, second)
        self.assertEqual((deleted, failed), (1, 1))
        self.assertEqual(set(calls), {"tm-first", "tm-second"})
        self.assertIsNone(bot.db_get_account(first, self.uid))
        self.assertIsNotNone(bot.db_get_account(second, self.uid))
        self.assertEqual(len(bot.db_get_user_accounts(self.other_uid, limit=None)), 1)
        self.add(self.uid, "newer")
        bot.mail_delete = lambda mid, token: calls.append(mid)
        deleted, failed = await bot._delete_all_user_mailboxes(self.uid, second)
        self.assertEqual((deleted, failed), (1, 0))
        self.assertNotIn("tm-newer", calls)

    async def test_pending_mail_warns_then_cancels_on_confirmed_bulk(self):
        import sqlite3
        self.add(self.uid, "done")
        pending_id = self.add(self.uid, "pending", status="sms_waiting")
        with sqlite3.connect(bot.DB_PATH) as conn:
            conn.execute("UPDATE accounts SET activation_id = 12345 WHERE id = ?", (pending_id,))
        bot.db_add_activation(self.uid, 12345, "77001234567", 0.05, "rr", 2)
        calls = []
        old_hero = bot._hero
        def fake_hero(action, api_key=None, **kwargs):
            if action == "getStatus":
                return "STATUS_CANCEL" if 12345 in calls else "STATUS_WAIT_CODE"
            calls.append(kwargs["id"])
            return "ACCESS_CANCEL"
        bot._hero = fake_hero
        bot.mail_delete = lambda mid, token: calls.append(mid)
        try:
            cb = _FakeCallback()
            await bot.cb_mail_del_all(cb)
            self.assertIn("через 2 минуты", cb.message.edits[-1][0])
            self.assertEqual(len(bot.db_get_user_accounts(self.uid, limit=None)), 2)
            cb.data = f"mail_del_all_do:{pending_id}"
            await bot.cb_mail_del_all_do(cb, _FakeState({"account_id": pending_id}))
            self.assertEqual(len(bot.db_get_user_accounts(self.uid, limit=None)), 1)
            self.assertEqual(calls, ["tm-done"])
            self.assertEqual(bot.db_get_activation_for_webhook(12345)["status"], "cancel_pending")
            with sqlite3.connect(bot.DB_PATH) as conn:
                conn.execute("UPDATE pending_number_actions SET due_at = ? WHERE activation_id = 12345", (bot.time.time() - 1,))
            await bot._process_number_action(bot.db_number_action(12345))
            self.assertEqual(bot.db_get_activation_for_webhook(12345)["status"], "cancelled")
            self.assertIn(12345, calls)
            deleted, failed = await bot._delete_all_user_mailboxes(self.uid, pending_id)
            self.assertEqual((deleted, failed), (1, 0))
            self.assertIn("tm-pending", calls)
        finally:
            bot._hero = old_hero

    async def test_early_cancel_denied_keeps_pending_inbox(self):
        import sqlite3
        pending_id = self.add(self.uid, "pending", status="sms_waiting")
        with sqlite3.connect(bot.DB_PATH) as conn:
            conn.execute("UPDATE accounts SET activation_id = 12345 WHERE id = ?", (pending_id,))
        bot.db_add_activation(self.uid, 12345, "77001234567", 0.05, "rr", 2)
        old_hero = bot._hero
        def denied(*args, **kwargs):
            raise ValueError("EARLY_CANCEL_DENIED")
        bot._hero = denied
        bot.mail_delete = lambda *args: self.fail("must not delete while number is active")
        try:
            deleted, failed = await bot._delete_all_user_mailboxes(self.uid, pending_id)
            self.assertEqual((deleted, failed), (0, 1))
            self.assertIsNotNone(bot.db_get_account(pending_id, self.uid))
            self.assertEqual(bot.db_get_activation_for_webhook(12345)["status"], "cancel_pending")
            self.assertIsNotNone(bot.db_number_action(12345))
        finally:
            bot._hero = old_hero

    async def test_confirmation_is_required(self):
        self.add(self.uid, "done")
        bot.mail_delete = lambda *args: self.fail("confirmation must not delete")
        cb = _FakeCallback()
        await bot.cb_mail_del_all(cb)
        self.assertEqual(len(bot.db_get_user_accounts(self.uid, limit=None)), 1)
        callbacks = [b.callback_data for row in cb.message.edits[-1][1]["reply_markup"].inline_keyboard for b in row]
        self.assertTrue(any(data.startswith("mail_del_all_do:") for data in callbacks))


class SuccessfulActivationStatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = bot.DB_PATH
        bot.DB_PATH = str(Path(self.tmp.name) / "test.db")
        bot.db_init()
        uid = bot.db_get_user(bot.ADMIN_ID)["id"]
        import sqlite3
        with sqlite3.connect(bot.DB_PATH) as conn:
            for status, when, cost in (
                ("completed", "2026-09-01T00:00:00", 0.1),
                ("completed", "2026-09-10T23:59:59", 0.2),
                ("completed", "2026-09-11T00:00:00", 0.3),
                ("cancelled", "2026-09-10T12:00:00", 100),
            ):
                conn.execute("INSERT INTO activations (user_id, status, completed_at, cost) VALUES (?,?,?,?)", (uid, status, when, cost))

    def tearDown(self):
        bot.DB_PATH = self.old_db
        self.tmp.cleanup()

    def test_custom_period_inclusive_dates_excludes_cancelled(self):
        label, start, end = bot.parse_custom_stats_period("01.09.2026 - 10.09.2026")
        self.assertIn("10.09.2026", label)
        count, total = bot.db_successful_activation_summary(start, end)
        self.assertEqual(count, 2)
        self.assertEqual(str(total), "0.3")
        self.assertEqual(bot.db_successful_activation_summary(), (3, bot.Decimal("0.6")))

    def test_user_scope_isolated_from_global_and_other_users(self):
        import sqlite3
        with sqlite3.connect(bot.DB_PATH) as conn:
            conn.execute(
                "INSERT INTO users (telegram_id, first_name, is_active) VALUES (?, ?, 1)",
                (987654321, "Другой"),
            )
            other_id = conn.execute("SELECT id FROM users WHERE telegram_id = 987654321").fetchone()[0]
            conn.execute(
                "INSERT INTO activations (user_id, status, completed_at, cost) VALUES (?,?,?,?)",
                (other_id, "completed", "2026-09-10T12:00:00", 1.25),
            )
        mine = bot.db_get_user(bot.ADMIN_ID)["id"]
        label, start, end = bot.parse_custom_stats_period("01.09.2026 - 10.09.2026")
        self.assertEqual(bot.db_successful_activation_summary(start, end, user_id=mine), (2, bot.Decimal("0.3")))
        self.assertEqual(bot.db_successful_activation_summary(start, end, user_id=other_id), (1, bot.Decimal("1.25")))
        self.assertEqual(bot.db_successful_activation_summary(start, end), (3, bot.Decimal("1.55")))
        self.assertEqual(bot.db_successful_activation_summary(user_id=mine), (3, bot.Decimal("0.6")))

    def test_period_presets_and_invalid_ranges(self):
        now = bot.datetime.datetime(2026, 9, 26, 18)
        self.assertEqual(bot.stats_period_bounds("week", now)[1].day, 20)
        self.assertEqual(bot.stats_period_bounds("month", now)[1].day, 1)
        for raw in ("32.09.2026 - 26.09.2026", "27.09.2026 - 26.09.2026", "bad"):
            with self.assertRaises(ValueError):
                bot.parse_custom_stats_period(raw)

    def test_admin_stats_ui_contains_sum_and_period_controls(self):
        label, start, end = bot.parse_custom_stats_period("01.09.2026 - 10.09.2026")
        text, kb = bot._stats_text_and_kb(label, start, end)
        self.assertIn("Успешных активаций: 2", text)
        self.assertIn("$0.3000", text)
        actions = [b.callback_data for row in kb.inline_keyboard for b in row]
        self.assertEqual(
            {"admin_stats:today", "admin_stats:week", "admin_stats:month", "admin_stats:all", "admin_stats:custom"} - set(actions),
            set(),
        )


class ScopedActivationStatsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import sqlite3
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = bot.DB_PATH
        bot.DB_PATH = str(Path(self.tmp.name) / "test.db")
        bot.db_init()
        self.admin_id = bot.db_get_user(bot.ADMIN_ID)["id"]
        with sqlite3.connect(bot.DB_PATH) as conn:
            conn.execute("INSERT INTO users (telegram_id, first_name, is_active) VALUES (?,?,1)", (987654321, "Пользователь"))
            self.user_id = conn.execute("SELECT id FROM users WHERE telegram_id = 987654321").fetchone()[0]
            for uid, cost in ((self.admin_id, 9.0), (self.user_id, 0.25)):
                conn.execute("INSERT INTO activations (user_id, status, completed_at, cost) VALUES (?,'completed','2026-09-10T12:00:00',?)", (uid, cost))

    def tearDown(self):
        bot.DB_PATH = self.old_db
        self.tmp.cleanup()

    async def test_personal_view_only_shows_own_cost_and_periods(self):
        cb = _FakeCallback()
        cb.from_user.id = 987654321
        await bot.cb_user_stats(cb)
        self.assertEqual(len(cb.answers), 1)
        text, kwargs = cb.message.edits[-1]
        self.assertIn("$0.2500", text)
        self.assertNotIn("$9.0000", text)
        actions = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
        self.assertTrue({"user_stats:today", "user_stats:week", "user_stats:month", "user_stats:all", "user_stats:custom"}.issubset(actions))
        self.assertEqual(kwargs["parse_mode"], None)

    async def test_non_admin_cannot_read_another_users_stats(self):
        cb = _FakeCallback()
        cb.from_user.id = 987654321
        cb.data = f"admin_user_stats:{self.admin_id}:all"
        await bot.cb_admin_user_stats_period(cb, _FakeState({}))
        self.assertFalse(cb.message.edits)
        self.assertTrue(cb.answers[0][1]["show_alert"])

    async def test_admin_user_view_period_and_custom_input(self):
        cb = _FakeCallback()
        cb.data = f"admin_user_stats:{self.user_id}:all"
        state = _FakeState({})
        state.current = None
        await bot.cb_admin_user_stats_period(cb, state)
        self.assertIn("$0.2500", cb.message.edits[-1][0])
        self.assertNotIn("$9.0000", cb.message.edits[-1][0])
        cb.data = f"admin_user_stats:{self.user_id}:custom"
        await bot.cb_admin_user_stats_period(cb, state)
        self.assertEqual(state.current, bot.StatsState.waiting_custom.state)
        self.assertEqual(state.data["stats_target_id"], self.user_id)
        self.assertEqual(len(cb.answers), 2)

    async def test_custom_input_uses_selected_user_and_clears_state(self):
        cb = _FakeCallback()
        cb.data = f"admin_user_stats:{self.user_id}:custom"
        state = _FakeState({})
        state.current = None
        await bot.cb_admin_user_stats_period(cb, state)
        msg = SimpleNamespace(from_user=SimpleNamespace(id=bot.ADMIN_ID), chat=SimpleNamespace(id=bot.ADMIN_ID), text="01.09.2026 - 10.09.2026")
        replies = []
        async def answer(text, **kwargs):
            replies.append((text, kwargs))
            return SimpleNamespace(message_id=123)
        msg.answer = answer
        old_purge, old_track = bot._purge_ui, bot._track_ui
        async def fake_purge(chat_id):
            return None
        bot._purge_ui, bot._track_ui = fake_purge, lambda chat_id, message: None
        try:
            await bot.scoped_stats_custom_input(msg, state)
        finally:
            bot._purge_ui, bot._track_ui = old_purge, old_track
        self.assertIsNone(state.current)
        self.assertIn("$0.2500", replies[0][0])
        self.assertNotIn("$9.0000", replies[0][0])
        self.assertEqual(replies[0][1]["parse_mode"], None)

    async def test_deactivated_user_cannot_see_personal_stats(self):
        bot.db_deactivate_user(self.user_id, bot.ADMIN_ID)
        cb = _FakeCallback()
        cb.from_user.id = 987654321
        await bot.cb_user_stats(cb)
        self.assertFalse(cb.message.edits)
        self.assertTrue(cb.answers[0][1]["show_alert"])

    async def test_custom_rejects_active_registration_without_changing_state(self):
        cb = _FakeCallback()
        cb.from_user.id = 987654321
        cb.data = "user_stats:custom"
        state = _FakeState({"activation_id": 11})
        await bot.cb_user_stats_period(cb, state)
        self.assertEqual(state.current, bot.RegState.waiting_wolt_email.state)
        self.assertFalse(cb.message.edits)
        self.assertTrue(cb.answers[-1][1]["show_alert"])


class GmailCountryTests(unittest.TestCase):
    def test_picker_shows_live_price_and_paginates(self):
        offers = []
        for i in range(12):
            offers.append({
                "id": i + 1,
                "rus": f"Страна{i+1}",
                "eng": f"Country{i+1}",
                "cost": 0.02 + i * 0.01,
                "count": 1000 * (i + 1),
                "prefix": "1",
            })
        offers[3] = {
            "id": 16, "rus": "Великобритания", "eng": "United Kingdom",
            "cost": 0.078, "count": 200000, "prefix": "44",
        }
        kb, page, pages, total = bot.gmail_countries_kb(offers, page=0)
        self.assertEqual(total, 12)
        self.assertEqual(pages, 2)
        self.assertEqual(page, 0)
        callbacks = [button.callback_data for row in kb.inline_keyboard for button in row]
        texts = [button.text for row in kb.inline_keyboard for button in row]
        self.assertIn("gmail_c:1", callbacks)
        self.assertIn("gmail_p:1", callbacks)
        self.assertIn("gmail_refresh", callbacks)
        self.assertTrue(any("$" in text for text in texts))
        self.assertTrue(any("к" in text for text in texts))
        self.assertTrue(any(text.startswith("UK ") for text in texts))

        kb2, page2, pages2, _ = bot.gmail_countries_kb(offers, page=1)
        self.assertEqual(page2, 1)
        self.assertEqual(pages2, 2)
        callbacks2 = [button.callback_data for row in kb2.inline_keyboard for button in row]
        self.assertIn("gmail_p:0", callbacks2)
        self.assertIn("gmail_c:12", callbacks2)

    def test_offers_sorted_by_price_and_skip_empty_stock(self):
        original_json = bot._hero_json
        original_cache = dict(bot._OFFERS_CACHE)
        original_meta = dict(bot._COUNTRY_META)
        try:
            bot._OFFERS_CACHE.clear()

            def fake_json(action, api_key=None, **params):
                if action == "getCountries":
                    return {
                        "4": {"id": 4, "rus": "Филиппины", "eng": "Philippines"},
                        "16": {"id": 16, "rus": "Великобритания", "eng": "United Kingdom"},
                        "187": {"id": 187, "rus": "США", "eng": "USA"},
                        "99": {"id": 99, "rus": "Пустыня", "eng": "Nowhere"},
                    }
                if action == "getPrices":
                    return {
                        "4": {"go": {"cost": 0.024, "count": 10}},
                        "16": {"go": {"cost": 0.078, "count": 5}},
                        "187": {"go": {"cost": 0.3, "count": 8}},
                        "99": {"go": {"cost": 0.01, "count": 0}},
                    }
                raise AssertionError(action)

            bot._hero_json = fake_json
            offers = bot.hero_service_offers("go", force=True)
            self.assertEqual([item["id"] for item in offers], [4, 16, 187])
            self.assertEqual(offers[0]["cost"], 0.024)
            self.assertEqual(offers[0]["prefix"], "63")
            cached = bot.hero_service_offers("go", force=False)
            self.assertIs(cached, offers)
        finally:
            bot._hero_json = original_json
            bot._OFFERS_CACHE.clear()
            bot._OFFERS_CACHE.update(original_cache)
            bot._COUNTRY_META.clear()
            bot._COUNTRY_META.update(original_meta)


class GmailConfirmCountryTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirm_uses_selected_country_not_default_uk(self):
        old_user = bot.db_get_user
        old_price = bot.hero_get_price
        old_balance = bot._hero_balance
        old_key = bot.get_user_hero_key
        seen = {}

        class FakeMessage:
            def __init__(self):
                self.edits = []
                self.chat = SimpleNamespace(id=bot.ADMIN_ID)

            async def edit_text(self, text, **kwargs):
                self.edits.append((text, kwargs))

        class FakeCb:
            def __init__(self):
                self.from_user = SimpleNamespace(id=bot.ADMIN_ID)
                self.message = FakeMessage()
                self.answers = []

            async def answer(self, *args, **kwargs):
                self.answers.append((args, kwargs))

        try:
            bot.db_get_user = lambda tid: {
                "id": 1, "is_admin": 1, "use_admin_hero": 1, "hero_key": None, "telegram_id": tid,
            }
            bot.get_user_hero_key = lambda row: None
            bot._hero_balance = lambda api_key=None: 5.0

            def fake_price(service, country, api_key=None):
                seen["country"] = country
                seen["service"] = service
                return (0.024, 1234)

            bot.hero_get_price = fake_price
            state = _FakeState({})
            state.current = bot.RegState.confirming.state
            cb = FakeCb()
            await bot._show_confirm(cb, state, "gmail", country=4)
            self.assertEqual(seen["country"], 4)
            self.assertEqual(seen["service"], "go")
            self.assertEqual(state.data["country"], 4)
            confirm_text = " ".join(text for text, _ in cb.message.edits)
            self.assertIn("$0.024", confirm_text)
            markup = cb.message.edits[-1][1]["reply_markup"]
            callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
            self.assertIn("gmail_c:4", callbacks)
            self.assertIn("start_gmail", callbacks)
            self.assertIn("confirm_buy", callbacks)
        finally:
            bot.db_get_user = old_user
            bot.hero_get_price = old_price
            bot._hero_balance = old_balance
            bot.get_user_hero_key = old_key


class ComboFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirm_combo_mentions_wolt_followup(self):
        old_user = bot.db_get_user
        old_price = bot.hero_get_price
        old_balance = bot._hero_balance
        old_key = bot.get_user_hero_key
        old_countries = bot.hero_countries
        prices = {}

        class FakeMessage:
            def __init__(self):
                self.edits = []
                self.chat = SimpleNamespace(id=bot.ADMIN_ID)

            async def edit_text(self, text, **kwargs):
                self.edits.append((text, kwargs))

        class FakeCb:
            def __init__(self):
                self.from_user = SimpleNamespace(id=bot.ADMIN_ID)
                self.message = FakeMessage()
                self.answers = []

            async def answer(self, *args, **kwargs):
                self.answers.append((args, kwargs))

        try:
            bot.db_get_user = lambda tid: {
                "id": 1, "is_admin": 1, "use_admin_hero": 1, "hero_key": None, "telegram_id": tid,
            }
            bot.get_user_hero_key = lambda row: None
            bot._hero_balance = lambda api_key=None: 5.0
            bot.hero_countries = lambda api_key=None: {}

            def fake_price(service, country, api_key=None):
                prices[service] = country
                if service == "rr":
                    return (0.09, 4000)
                return (0.024, 1234)

            bot.hero_get_price = fake_price
            state = _FakeState({"combo": "gmail_then_wolt"})
            state.current = bot.RegState.confirming.state
            cb = FakeCb()
            await bot._show_confirm(cb, state, "gmail", country=4)
            self.assertEqual(prices.get("go"), 4)
            self.assertEqual(prices.get("rr"), 2)
            confirm_text = " ".join(text for text, _ in cb.message.edits)
            self.assertIn("Wolt", confirm_text)
            self.assertIn("Дальше: Wolt", confirm_text)
            markup = cb.message.edits[-1][1]["reply_markup"]
            callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
            self.assertIn("start_combo", callbacks)
        finally:
            bot.db_get_user = old_user
            bot.hero_get_price = old_price
            bot._hero_balance = old_balance
            bot.get_user_hero_key = old_key
            bot.hero_countries = old_countries

    async def test_combo_wolt_opens_sms_confirm_not_mailbox(self):
        old_db_path = bot.DB_PATH
        old_balance = bot._hero_balance
        old_price = bot.hero_get_price
        old_mail_create = bot.mail_create
        old_hero = bot._hero
        mail_called = {"n": 0}
        with tempfile.TemporaryDirectory() as tmp:
            bot.DB_PATH = str(Path(tmp) / "test.db")
            try:
                bot.db_init()
                state = _FakeState({
                    "combo": "gmail_then_wolt",
                    "service_key": "gmail",
                    "activation_id": 555,
                    "hero_key": None,
                })
                state.current = bot.RegState.completed.state
                cb = _FakeCallback()
                completed = []
                bot._hero_balance = lambda api_key=None: 2.0
                bot.hero_get_price = lambda service, country, api_key=None: (0.09, 10)

                def fake_mail():
                    mail_called["n"] += 1
                    raise AssertionError("Mail.tm must not run for Wolt SMS-only combo")

                bot.mail_create = fake_mail

                def fake_hero(action, api_key=None, **params):
                    if action == "setStatus":
                        completed.append(params)
                    return "ACCESS_ACTIVATION"

                bot._hero = fake_hero
                await bot.cb_combo_wolt(cb, state)
                self.assertEqual(state.current, bot.RegState.confirming.state)
                self.assertIsNone(state.data.get("combo"))
                self.assertEqual(state.data.get("service_key"), "wolt")
                self.assertEqual(state.data.get("service_code"), "rr")
                self.assertEqual(state.data.get("country"), 2)
                self.assertEqual(mail_called["n"], 0)
                self.assertTrue(any(p.get("id") == 555 and p.get("status") == bot.HERO_COMPLETE for p in completed))
                confirm_text = " ".join(text for text, _ in cb.message.edits)
                self.assertIn("Подтверждение", confirm_text)
                self.assertIn("Wolt", confirm_text)
                self.assertNotIn("Почта Wolt готова", confirm_text)
            finally:
                bot._hero_balance = old_balance
                bot.hero_get_price = old_price
                bot.mail_create = old_mail_create
                bot._hero = old_hero
                bot.DB_PATH = old_db_path


if __name__ == "__main__":
    unittest.main()
