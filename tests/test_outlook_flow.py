"""Outlook contract tests. All vendor fixtures are SYNTHETIC, no live calls."""
import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
import httpx
import bot

KEY = "synthetic-selected-key-000000000000"
LINK = "https://wolt.com/en/email-login?token=" + "synthetic" * 40

def activation(**changes):
    data = dict(id=123, site="wolt.com", email="random@outlook.com", status="WAIT",
                value=None, cost=0.051, currency=840, date="2026-10-14T00:00:00Z", message="")
    data.update(changes)
    return data

class State:
    def __init__(self, current=None, **data):
        self.current = current
        self.data = data
    async def get_state(self): return self.current
    async def get_data(self): return dict(self.data)
    async def set_state(self, value): self.current = value.state if hasattr(value, "state") else value
    async def update_data(self, **data): self.data.update(data)
    async def clear(self): self.current = None; self.data.clear()

class Callback:
    def __init__(self, data="start_wolt_outlook", tid=bot.ADMIN_ID):
        self.data = data
        self.from_user = SimpleNamespace(id=tid)
        self.message = SimpleNamespace(chat=SimpleNamespace(id=tid), edit_text=AsyncMock())
        self.answer = AsyncMock()

class Isolated(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = bot.DB_PATH
        bot.DB_PATH = str(Path(self.tmp.name) / "bot.db")
        bot.db_init()
        self.user = bot.db_get_user(bot.ADMIN_ID)
        self.stack = []
        self.mock("get_user_hero_key", return_value=KEY)
        self.mock("_safe_send", new=AsyncMock(return_value=SimpleNamespace(message_id=1)))
        self.mock("_replace_ui", new=AsyncMock(return_value=SimpleNamespace(message_id=1)))
        self.mock("_start_sms_watch", new=AsyncMock())
        self.mock("asyncio.create_task", new=lambda coro: coro.close())
        bot._chat_locks.clear()
    def mock(self, name, **kwargs):
        p = patch("bot." + name, create=True, **kwargs)
        self.stack.append(p)
        return p.start()
    async def asyncTearDown(self):
        for p in reversed(self.stack): p.stop()
        bot.DB_PATH = self.old_db
        self.tmp.cleanup()
    def quotes(self, mail=0.051, stock=10, sms=0.09, balance=2):
        self.mock("hero_email_offers", return_value=[dict(name="outlook.com", cost=mail, count=stock)] if stock else [])
        self.mock("hero_email_offer", return_value=(mail, stock))
        self.mock("hero_get_price", return_value=(sms, 20))
        self.mock("_hero_balance", return_value=balance)
    async def open_quote(self, cb, state, domain="outlook.com"):
        await bot.cb_start_wolt_outlook(cb, state)
        data = await state.get_data()
        if data.get("hero_domains_token"):
            index = next(i for i, offer in enumerate(data["hero_domains_offers"]) if offer["name"] == domain)
            cb.data = f"hero_domain:{data['hero_domains_token']}:{index}"
            await bot.cb_hero_domain(cb, state)

class OutlookEntryTests(Isolated):
    async def test_quote_button_and_registered_handler_without_spending(self):
        buttons = [b for row in bot.service_choice_kb().inline_keyboard for b in row]
        self.assertIn("📧 Wolt: почта + SMS (HeroSMS)", [b.text for b in buttons])
        self.assertIn("start_wolt_outlook", [b.callback_data for b in buttons])
        self.assertTrue(any(h.callback is bot.cb_start_wolt_outlook for h in bot.router.callback_query.handlers))
        self.quotes()
        buy = self.mock("hero_email_buy")
        cb, state = Callback(), State()
        await self.open_quote(cb, state)
        text = cb.message.edit_text.call_args.args[0]
        for value in ("outlook.com", "0.051", "0.090", "0.146", "2.00", "10"):
            self.assertIn(value, text)
        markup = cb.message.edit_text.call_args.kwargs["reply_markup"]
        confirm = [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data.startswith("confirm_outlook:")]
        self.assertEqual(len(confirm), 1)
        self.assertEqual(state.current, bot.RegState.confirming.state)
        buy.assert_not_called()

class OutlookApiTests(unittest.TestCase):
    def test_purchases_exact_outlook_site_with_selected_key_and_validates_response(self):
        self.assertTrue(callable(getattr(bot, "hero_email_buy", None)), "Outlook purchase helper missing")
        with patch.object(bot.httpx, "get", return_value=httpx.Response(200, json={"data": [{"name": "outlook.com", "cost": 0.062, "count": 8}]})) as get:
            self.assertEqual(bot.hero_email_offer(KEY), (0.062, 8))
            self.assertEqual(get.call_args.kwargs["params"], {"site": "wolt.com"})
            self.assertEqual(get.call_args.kwargs["headers"], {"Authorization": "ApiKey " + KEY})
        with patch.object(bot.httpx, "post", return_value=httpx.Response(201, json={"data": activation()})) as post:
            self.assertEqual(bot.hero_email_buy("ApiKey " + KEY), activation())
            self.assertEqual(post.call_count, 1)
            self.assertEqual(post.call_args.args[0], "https://hero-sms.com/api/v1/emails")
            self.assertEqual(post.call_args.kwargs["json"], {"site": "wolt.com", "domain": "outlook.com"})
            self.assertEqual(post.call_args.kwargs["headers"], {"Authorization": "ApiKey " + KEY})
        for data in (None, {}, activation(id=True), activation(id=0), activation(site="other.com"),
                     activation(email="a@hotmail.com"), activation(email="<bad>@outlook.com"),
                     activation(cost="NaN"), activation(currency=978), activation(status="unknown")):
            with self.subTest(data=data), patch.object(bot.httpx, "post", return_value=httpx.Response(201, json={"data": data})):
                with self.assertRaises(bot.HeroEmailError): bot.hero_email_buy(KEY)
        for response in (httpx.Response(401, text=KEY), httpx.Response(422, json={"data": KEY}), httpx.Response(200, text="<html>" + KEY)):
            with patch.object(bot.httpx, "post", return_value=response):
                with self.assertRaises(bot.HeroEmailError) as caught: bot.hero_email_buy(KEY)
                self.assertNotIn(KEY, str(caught.exception))
        with patch.object(bot.httpx, "post", side_effect=httpx.ReadTimeout(KEY)) as post:
            with self.assertRaises(bot.HeroEmailError) as caught: bot.hero_email_buy(KEY)
            self.assertNotIn(KEY, str(caught.exception))
            self.assertEqual(post.call_count, 1)

class OutlookPurchaseTests(Isolated):
    async def test_confirmation_persists_provider_cost_snapshot_and_migrates_idempotently(self):
        self.assertTrue(callable(getattr(bot, "cb_confirm_outlook", None)), "paid confirmation missing")
        legacy = bot.db_add_account(self.user["id"], "legacy@mail.test", "password", "mt-id", "mt-token", cost=0.1)
        bot.db_init(); bot.db_init()
        row = bot.db_get_account(legacy)
        self.assertEqual(row["mail_provider"], "mailtm")
        self.assertEqual((row["password"], row["mail_tm_id"], row["cost"]), ("password", "mt-id", 0.1))
        self.quotes()
        buy = self.mock("hero_email_buy", return_value=activation())
        cb, state = Callback(), State()
        await self.open_quote(cb, state)
        cb.data = "confirm_outlook:" + state.data["outlook_confirm"]
        await bot.cb_confirm_outlook(cb, state)
        row = bot.db_get_latest_account(self.user["id"])
        self.assertEqual(row["mail_provider"], "herosms")
        self.assertEqual(row["mail_activation_id"], 123)
        self.assertEqual(row["mail_api_key"], KEY)
        self.assertEqual(row["mail_cost"], 0.051)
        self.assertIsNone(row["cost"])
        self.assertEqual(row["password"], "")
        self.assertIsNone(row["mail_tm_id"])
        self.assertIsNone(row["mail_token"])
        self.assertEqual(state.current, bot.RegState.waiting_wolt_email.state)
        self.assertIn("random@outlook.com", cb.message.edit_text.call_args.args[0])
        await bot.cb_confirm_outlook(cb, state)
        buy.assert_called_once_with(KEY, domain="outlook.com")

class OutlookRefreshTests(Isolated):
    async def test_price_rise_requires_new_confirmation_and_insufficient_budget_never_buys(self):
        self.quotes()
        buy = self.mock("hero_email_buy", return_value=activation())
        cb, state = Callback(), State()
        await self.open_quote(cb, state)
        old = state.data["outlook_confirm"]
        cb.data = "confirm_outlook:" + old
        self.mock("hero_email_offer", return_value=(0.07, 10))
        await bot.cb_confirm_outlook(cb, state)
        buy.assert_not_called()
        self.assertNotEqual(state.data.get("outlook_confirm"), old)
        await bot.cb_confirm_outlook(cb, state)
        buy.assert_not_called()
        for mailstock, balance in ((0, 2), (10, 0.08)):
            self.mock("hero_email_offer", return_value=(0.07, mailstock))
            self.mock("_hero_balance", return_value=balance)
            cb.data = "confirm_outlook:" + state.data["outlook_confirm"]
            await bot.cb_confirm_outlook(cb, state)
            buy.assert_not_called()
    async def test_unknown_inactive_active_and_stale_confirm_do_not_spend(self):
        self.quotes()
        buy = self.mock("hero_email_buy", return_value=activation())
        for tid, current in ((999, None), (bot.ADMIN_ID, bot.RegState.waiting_sms.state)):
            state = State(current, original="preserved")
            await bot.cb_start_wolt_outlook(Callback(tid=tid), state)
            self.assertEqual(state.data, {"original": "preserved"})
        cb, state = Callback(), State()
        await self.open_quote(cb, state)
        cb.data = "confirm_outlook:stale"
        await bot.cb_confirm_outlook(cb, state)
        with sqlite3.connect(bot.DB_PATH) as conn:
            conn.execute("UPDATE users SET is_active=0 WHERE id=?", (self.user["id"],))
        cb.data = "confirm_outlook:" + state.data["outlook_confirm"]
        await bot.cb_confirm_outlook(cb, state)
        buy.assert_not_called()

class OutlookUncertaintyTests(Isolated):
    async def test_ambiguous_purchase_survives_state_clear_restart_and_never_rebuys(self):
        self.quotes()
        buy = self.mock("hero_email_buy", side_effect=httpx.ReadTimeout("SECRET_PROVIDER_BODY"))
        cb, state = Callback(), State()
        await self.open_quote(cb, state)
        cb.data = "confirm_outlook:" + state.data["outlook_confirm"]
        await bot.cb_confirm_outlook(cb, state)
        await state.clear(); bot.db_init()
        await self.open_quote(cb, state)
        self.assertIsNone(state.data.get("outlook_confirm"), "uncertain purchase must block renewed spending")
        buy.assert_called_once_with(KEY, domain="outlook.com")
        with sqlite3.connect(bot.DB_PATH) as conn:
            row = conn.execute("SELECT user_id,api_key,status FROM email_purchase_intents WHERE user_id=?", (self.user["id"],)).fetchone()
        self.assertEqual(row, (self.user["id"], KEY, "uncertain"))
        public = str(cb.answer.call_args_list) + str(cb.message.edit_text.call_args_list)
        self.assertIn("вручную", public)
        self.assertNotIn("SECRET_PROVIDER_BODY", public)

class HeroPrepared(Isolated):
    async def prepare(self):
        account = bot.db_add_hero_account(self.user["id"], activation(), KEY)
        state = State()
        await bot._load_wolt_state(state, bot.db_get_account(account), self.user)
        await state.set_state(bot.RegState.waiting_wolt_email)
        return account, state

class OutlookLetterTests(HeroPrepared):
    async def test_success_null_value_html_message_delivers_long_link_before_sms(self):
        account, state = await self.prepare()
        calls = []
        def fetch(aid, key):
            calls.append(("email", aid, key))
            return activation(status="SUCCESS", value=None, message='<a href="' + LINK + '">Wolt</a>')
        self.mock("hero_email_get", new=fetch)
        async def delivery(chat, text, reply_markup=None):
            calls.append(("link", text))
            self.assertIn('<a href="', text)
            buttons = [b for row in reply_markup.inline_keyboard for b in row]
            self.assertTrue(any(b.url == LINK for b in buttons))
            self.assertTrue(all(b.copy_text is None for b in buttons))
            return SimpleNamespace(message_id=5)
        self.mock("_replace_ui", new=delivery)
        async def sms(chat, state):
            calls.append(("sms",))
        self.mock("_wolt_buy_number", new=sms)
        mt = self.mock("mail_fetch", side_effect=AssertionError("must not use Mail.tm"))
        await bot._wait_wolt_email(bot.ADMIN_ID, state)
        self.assertEqual([c[0] for c in calls], ["email", "link", "sms"])
        self.assertEqual(calls[0][1:], (123, KEY))
        self.assertEqual(bot.db_get_account(account)["email_link"], LINK)
        self.assertEqual(bot.db_get_account(account)["email_link_delivered"], 1)
        mt.assert_not_called()

class OutlookDeliverySafetyTests(HeroPrepared):
    async def test_link_delivery_failure_saves_retryable_email_without_sms(self):
        account, state = await self.prepare()
        self.mock("hero_email_get", return_value=activation(status="SUCCESS", message='<a href="' + LINK + '">Wolt</a>'))
        self.mock("_replace_ui", new=AsyncMock(return_value=None))
        sms = self.mock("_wolt_buy_number", new=AsyncMock())
        await bot._wait_wolt_email(bot.ADMIN_ID, state)
        sms.assert_not_called()
        row = bot.db_get_account(account)
        self.assertEqual(row["email_link"], LINK)
        self.assertEqual(row["email_link_delivered"], 0)
        self.assertEqual(row["status"], "email_error")

class OutlookAwaitSafetyTests(HeroPrepared):
    async def test_cancel_stale_watcher_and_deactivation_during_email_await_exit(self):
        for event in ("cancel", "stale", "disabled"):
            with self.subTest(event=event):
                with sqlite3.connect(bot.DB_PATH) as conn:
                    conn.execute("DELETE FROM accounts")
                account, state = await self.prepare()
                async def threaded(fn, *args, **kwargs):
                    if event == "cancel":
                        await state.clear()
                        bot.db_update_account(account, status="cancelled")
                    elif event == "stale":
                        await state.update_data(mail_watch_id="replacement")
                    else:
                        with sqlite3.connect(bot.DB_PATH) as conn:
                            conn.execute("UPDATE users SET is_active=0 WHERE id=?", (self.user["id"],))
                    return activation(status="SUCCESS", message='<a href="' + LINK + '">Wolt</a>')
                self.mock("asyncio.to_thread", new=threaded)
                self.mock("hero_email_get")
                sms = self.mock("_wolt_buy_number", new=AsyncMock())
                send = self.mock("_send_wolt_link", new=AsyncMock(return_value=object()))
                await bot._wait_wolt_email(bot.ADMIN_ID, state)
                sms.assert_not_called(); send.assert_not_called()
                self.assertIsNone(bot.db_get_account(account)["email_link"])
                with sqlite3.connect(bot.DB_PATH) as conn:
                    conn.execute("UPDATE users SET is_active=1 WHERE id=?", (self.user["id"],))

class OutlookUnusableLetterTests(HeroPrepared):
    async def test_network_and_success_without_safe_link_preserve_retryable_email(self):
        for fixture in (httpx.ReadTimeout("SECRET"), activation(status="SUCCESS", message="no usable link"),
                        activation(status="SUCCESS", message='<a href="https://evil.test/wolt.com/login">Wolt</a>')):
            with self.subTest(fixture=type(fixture).__name__):
                with sqlite3.connect(bot.DB_PATH) as conn: conn.execute("DELETE FROM accounts")
                account, state = await self.prepare()
                self.mock("hero_email_get", **({"side_effect": fixture} if isinstance(fixture, Exception) else {"return_value": fixture}))
                sms = self.mock("_wolt_buy_number", new=AsyncMock())
                # Prevent a broken implementation from waiting 5 real minutes.
                async def stop_sleep(seconds):
                    self.assertEqual(seconds, 5)
                    await state.clear()
                self.mock("asyncio.sleep", new=stop_sleep)
                try:
                    await bot._wait_wolt_email(bot.ADMIN_ID, state)
                except Exception as exc:
                    self.fail("email watcher leaked provider exception: " + type(exc).__name__)
                sms.assert_not_called()
                row = bot.db_get_account(account)
                self.assertEqual(row["status"], "email_error")
                self.assertEqual(row["mail_activation_id"], 123)
        for url in ("http://wolt.com/login", "https://wolt.com.evil.test/login", "https://evil.test/?wolt", "https://wolt.com@evil.test/login", "wolt-app://login", "https://wolt.com:444/login"):
            self.assertIsNone(bot._hero_wolt_link(activation(status="SUCCESS", message='<a href="' + url + '">Wolt</a>')))

class OutlookNumberSafetyTests(HeroPrepared):
    async def test_number_requires_delivered_link_and_issued_activation_never_rebuys(self):
        account, state = await self.prepare()
        self.quotes()
        number = self.mock("_purchase_number", new=AsyncMock(return_value={"activationId": 987, "phoneNumber": "77001234567", "activationCost": "0.09"}))
        await bot._wolt_buy_number(bot.ADMIN_ID, state)
        number.assert_not_called()
        bot.db_update_account(account, email_link=LINK, email_link_delivered=1, status="email_received")
        await state.update_data(email_link=LINK)
        await bot._wolt_buy_number(bot.ADMIN_ID, state)
        self.assertEqual(number.await_count, 1)
        self.assertEqual(number.call_args.args, ("rr", 2, KEY))
        self.assertEqual(number.call_args.kwargs["owner_chat"], bot.ADMIN_ID)
        self.assertEqual(number.call_args.kwargs["account_id"], account)
        row = bot.db_get_account(account)
        self.assertEqual(row["activation_id"], 987)
        self.assertEqual(row["mail_cost"], 0.051)
        self.assertEqual(row["cost"], 0.09)
        await bot._wolt_buy_number(bot.ADMIN_ID, state)
        self.assertEqual(number.await_count, 1)

class OutlookResumeTests(HeroPrepared):
    async def test_resume_saved_link_and_sms_uses_original_snapshot_without_extra_purchase(self):
        account, state = await self.prepare()
        bot.db_update_account(account, email_link=LINK, status="email_received", mail_status="SUCCESS")
        self.mock("get_user_hero_key", return_value="synthetic-reassigned-key")
        buy = self.mock("hero_email_buy")
        mt = self.mock("mail_login", side_effect=AssertionError("no Mail.tm"))
        number = self.mock("_purchase_number", new=AsyncMock(return_value={"activationId": 987, "phoneNumber": "77001234567", "activationCost": "0.09"}))
        fetch = self.mock("hero_email_get", return_value=activation(status="SUCCESS", message='<a href="' + LINK + '">Wolt</a>'))
        await state.clear()
        cb = Callback("resume_wolt:" + str(account))
        await bot.cb_resume_wolt(cb, state)
        self.assertEqual(state.current, bot.RegState.waiting_wolt_email.state)
        await bot._wait_wolt_email(bot.ADMIN_ID, state)
        self.assertEqual(number.await_count, 1)
        self.assertEqual((await state.get_data())["mail_api_key"], KEY)
        await state.clear()
        await bot.cb_resume_wolt(cb, state)
        self.assertEqual(state.current, bot.RegState.waiting_sms.state)
        await bot.cb_retry_wolt_email(Callback("retry_wolt_email:" + str(account)), state)
        await bot.cb_retry_wolt_number(Callback("retry_wolt_number:" + str(account)), state)
        self.assertEqual(number.await_count, 1)
        buy.assert_not_called(); mt.assert_not_called()

class OutlookSavedMailTests(HeroPrepared):
    async def test_saved_mail_checks_existing_id_only_and_labels_site_bound_access(self):
        account, state = await self.prepare()
        cb = Callback("mail_view:" + str(account))
        await bot.cb_mail_view(cb)
        text = cb.message.edit_text.call_args.args[0]
        self.assertIn("HeroSMS", text)
        self.assertIn("не постоянный", text)
        self.assertIn("wolt.com", text)
        fetch = self.mock("hero_email_get", return_value=activation(status="SUCCESS", message='<a href="' + LINK + '">Wolt</a>'))
        mt = self.mock("mail_fetch", side_effect=AssertionError("no Mail.tm"))
        buy = self.mock("hero_email_buy")
        post = self.mock("httpx.post", side_effect=AssertionError("no paid reorder"))
        cb.data = "mail_link:" + str(account)
        await bot.cb_mail_link(cb)
        fetch.assert_called_once_with(123, KEY)
        self.assertEqual(bot.db_get_account(account)["email_link"], LINK)
        self.assertIn('<a href="', cb.message.edit_text.call_args.args[0])
        mt.assert_not_called(); buy.assert_not_called(); post.assert_not_called()

class OutlookDeleteTests(HeroPrepared):
    async def test_pending_email_delete_requires_exact_readback_and_consumed_only_local_delete(self):
        account, state = await self.prepare()
        calls = []
        get = self.mock("hero_email_get", side_effect=[activation(), activation(status="CANCELLED")])
        delete = self.mock("httpx.delete", return_value=httpx.Response(204))
        mt = self.mock("mail_delete", side_effect=AssertionError("Hero ID must not go to Mail.tm"))
        cb = Callback("mail_del_do:" + str(account))
        await bot.cb_mail_del_do(cb)
        self.assertIsNone(bot.db_get_account(account))
        self.assertEqual(get.call_args_list[0].args, (123, KEY))
        self.assertEqual(get.call_args_list[1].args, (123, KEY))
        self.assertEqual(delete.call_args.args[0], "https://hero-sms.com/api/v1/emails/123")
        self.assertEqual(delete.call_args.kwargs["headers"], {"Authorization": "ApiKey " + KEY})
        mt.assert_not_called()
        consumed = bot.db_add_hero_account(self.user["id"], activation(status="SUCCESS"), KEY)
        get.reset_mock(); delete.reset_mock()
        await bot.cb_mail_del_do(Callback("mail_del_do:" + str(consumed)))
        self.assertIsNone(bot.db_get_account(consumed))
        delete.assert_not_called(); get.assert_not_called()

class OutlookMixedDeleteTests(HeroPrepared):
    async def test_bulk_mixed_provider_owner_sms_and_ambiguous_mail_guards(self):
        account, state = await self.prepare()
        consumed = bot.db_add_hero_account(self.user["id"], activation(id=124, email="consumed@outlook.com", status="SUCCESS"), KEY)
        legacy = bot.db_add_account(self.user["id"], "old@mail.test", "pass", "mailtm-id", "token", status="completed")
        pending_sms = bot.db_add_hero_account(self.user["id"], activation(id=125, email="sms@outlook.com", status="SUCCESS"), KEY)
        bot.db_add_activation(self.user["id"], 321, "77001234567", 0.09, "rr", 2, api_key=KEY)
        bot.db_update_account(pending_sms, activation_id=321, status="sms_waiting")
        with sqlite3.connect(bot.DB_PATH) as conn:
            other = conn.execute("INSERT INTO users(telegram_id,hero_key) VALUES(42,?)", (KEY,)).lastrowid
        foreign = bot.db_add_hero_account(other, activation(id=126, email="foreign@outlook.com"), KEY)
        delete = self.mock("hero_email_delete")
        get = self.mock("hero_email_get", return_value=activation())  # ignored DELETE, remains WAIT
        mt = self.mock("mail_delete")
        deleted, failed = await bot._delete_all_user_mailboxes(self.user["id"], pending_sms, KEY)
        self.assertEqual((deleted, failed), (2, 2))
        mt.assert_called_once_with("mailtm-id", "token")
        delete.assert_called_once_with(123, KEY)
        self.assertIsNotNone(bot.db_get_account(account))
        self.assertIsNotNone(bot.db_get_account(foreign))
        self.assertIsNotNone(bot.db_get_account(pending_sms))
        self.assertEqual(bot.db_number_action(321)["status"], "queued")
        self.assertIsNone(bot.db_get_account(consumed)); self.assertIsNone(bot.db_get_account(legacy))
        await bot.cb_mail_del_do(Callback("mail_del_do:" + str(foreign)))
        self.assertIsNotNone(bot.db_get_account(foreign))

class OutlookCancelTests(HeroPrepared):
    async def test_explicit_cancel_pending_mail_confirms_readback_without_refund_claim(self):
        account, state = await self.prepare()
        get = self.mock("hero_email_get", side_effect=[activation(), activation(status="CANCELLED")])
        delete = self.mock("hero_email_delete")
        await bot.cb_cancel(Callback("cancel"), state)
        delete.assert_called_once_with(123, KEY)
        self.assertEqual(get.call_count, 2)
        row = bot.db_get_account(account)
        self.assertEqual(row["status"], "cancelled")
        self.assertEqual(row["mail_status"], "CANCELLED")
        self.assertEqual(row["mail_api_key"], KEY)
        self.assertIsNone(state.current)
    async def test_delete_ui_describes_provider_not_permanent_outlook_inbox(self):
        account, state = await self.prepare()
        cb = Callback("mail_del:" + str(account))
        await bot.cb_mail_del(cb)
        text = cb.message.edit_text.call_args.args[0]
        self.assertIn("HeroSMS", text)
        self.assertIn("локаль", text)
        self.assertNotIn("удалён с Mail.tm", text)
        await bot.cb_mail_del_all(Callback("mail_del_all"))

class OutlookPurchaseAwaitTests(Isolated):
    async def test_state_change_during_quote_refresh_or_purchase_never_revives_session(self):
        self.quotes()
        cb, state = Callback(), State()
        await self.open_quote(cb, state)
        cb.data = "confirm_outlook:" + state.data["outlook_confirm"]
        buy = self.mock("hero_email_buy", return_value=activation())
        real_thread = asyncio.to_thread
        async def change_on_refresh(fn, *args, **kwargs):
            value = await real_thread(fn, *args, **kwargs)
            if fn is bot.hero_email_offer:
                await state.clear()
            return value
        self.mock("asyncio.to_thread", new=change_on_refresh)
        await bot.cb_confirm_outlook(cb, state)
        buy.assert_not_called()
        self.assertIsNone(state.current)
        self.mock("asyncio.to_thread", new=real_thread)
        await self.open_quote(cb, state)
        cb.data = "confirm_outlook:" + state.data["outlook_confirm"]
        async def change_on_purchase(fn, *args, **kwargs):
            value = await real_thread(fn, *args, **kwargs)
            if fn is bot.hero_email_buy:
                await state.clear()
            return value
        self.mock("asyncio.to_thread", new=change_on_purchase)
        await bot.cb_confirm_outlook(cb, state)
        self.assertIsNone(state.current)
        row = bot.db_get_latest_account(self.user["id"])
        self.assertEqual(row["mail_activation_id"], 123)
        self.assertEqual(row["mail_api_key"], KEY)

class OutlookSmsAwaitTests(HeroPrepared):
    async def test_cancel_during_number_network_preserves_purchased_number_for_delayed_cancel(self):
        account, state = await self.prepare()
        bot.db_update_account(account, email_link=LINK, email_link_delivered=1, status="email_received")
        await state.update_data(email_link=LINK)
        async def bought(*args, **kwargs):
            await state.clear()
            bot.db_update_account(account, status="cancelled")
            return {"activationId": 987, "phoneNumber": "77001234567", "activationCost": "0.09"}
        self.mock("_purchase_number", new=bought)
        watch = self.mock("_start_sms_watch", new=AsyncMock())
        await bot._wolt_buy_number(bot.ADMIN_ID, state)
        self.assertIsNone(state.current)
        watch.assert_not_called()
        pending = bot.db_number_action(987)
        self.assertIsNotNone(pending)
        self.assertEqual(pending["status"], "queued")
        self.assertEqual(pending["api_key"], KEY)

class OutlookOtherFlowFenceTests(Isolated):
    async def test_old_number_confirmation_cannot_spend_on_outlook_quote(self):
        self.quotes()
        cb, state = Callback(), State()
        await self.open_quote(cb, state)
        number = self.mock("_purchase_number", new=AsyncMock(return_value={"activationId": 987, "phoneNumber": "77001234567", "activationCost": "0.09"}))
        cb.data = "confirm_buy"
        await bot.cb_confirm_buy(cb, state)
        number.assert_not_called()

class OutlookEndToEndTests(Isolated):
    async def test_quote_to_email_to_link_to_existing_number_to_sms_completion(self):
        self.quotes()
        post = self.mock("httpx.post", return_value=httpx.Response(201, json={"data": activation()}))
        def email_http(url, **kwargs):
            self.assertTrue(url.endswith("/emails/123"))
            self.assertEqual(kwargs["headers"], {"Authorization": "ApiKey " + KEY})
            return httpx.Response(200, json={"data": activation(status="SUCCESS", message='<a href="' + LINK + '">Wolt</a>')})
        self.mock("httpx.get", new=email_http)
        calls = []
        async def delivery(chat, text, reply_markup=None):
            calls.append("link")
            return SimpleNamespace(message_id=11)
        self.mock("_replace_ui", new=delivery)
        def sms_purchase(service, country, max_price, api_key):
            calls.append("sms-purchase")
            self.assertEqual((service, country, api_key), ("rr", 2, KEY))
            return {"activationId": 987, "phoneNumber": "77001234567", "activationCost": "0.09"}
        self.mock("hero_buy_number", new=sms_purchase)
        self.mock("_hero", return_value="ACCESS_READY")
        self.mock("sms_check", return_value=(True, "112233"))
        cb, state = Callback(), State()
        await self.open_quote(cb, state)
        cb.data = "confirm_outlook:" + state.data["outlook_confirm"]
        await bot.cb_confirm_outlook(cb, state)
        await bot._wait_wolt_email(bot.ADMIN_ID, state)
        self.assertEqual(calls, ["link", "sms-purchase"])
        self.assertEqual(state.current, bot.RegState.waiting_sms.state)
        await bot._wait_sms(bot.ADMIN_ID, state)
        row = bot.db_get_latest_account(self.user["id"])
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["sms_code"], "112233")
        self.assertEqual(row["mail_cost"], 0.051)
        self.assertEqual(row["cost"], 0.09)
        post.assert_called_once()

class OutlookDeleteRaceTests(HeroPrepared):
    async def test_explicit_single_delete_fences_watcher_before_remote_await(self):
        account, state = await self.prepare()
        send = self.mock("_send_wolt_link", new=AsyncMock(return_value=object()))
        sms = self.mock("_wolt_buy_number", new=AsyncMock())
        calls = 0
        real_thread = asyncio.to_thread
        async def interleave(fn, *args, **kwargs):
            nonlocal calls
            if fn is bot.hero_email_get:
                calls += 1
                if calls == 1:
                    # Watcher receives SUCCESS while explicit deletion is awaiting its initial GET.
                    self.mock("hero_email_get", return_value=activation(status="SUCCESS", message='<a href="' + LINK + '">Wolt</a>'))
                    await bot._wait_wolt_email(bot.ADMIN_ID, state)
                    return activation()
                if calls == 2:
                    return activation(status="SUCCESS", message='<a href="' + LINK + '">Wolt</a>')
                return activation(status="CANCELLED")
            return await real_thread(fn, *args, **kwargs)
        self.mock("asyncio.to_thread", new=interleave)
        self.mock("hero_email_delete")
        await bot.cb_mail_del_do(Callback("mail_del_do:" + str(account)))
        send.assert_not_called(); sms.assert_not_called()

class OutlookDeliveryAwaitTests(HeroPrepared):
    async def test_cancel_during_link_send_does_not_mark_delivered_or_buy_sms(self):
        account, state = await self.prepare()
        self.mock("hero_email_get", return_value=activation(status="SUCCESS", message='<a href="' + LINK + '">Wolt</a>'))
        async def send(*args, **kwargs):
            await state.clear()
            bot.db_update_account(account, status="cancelled")
            return object()
        self.mock("_send_wolt_link", new=send)
        sms = self.mock("_wolt_buy_number", new=AsyncMock())
        await bot._wait_wolt_email(bot.ADMIN_ID, state)
        sms.assert_not_called()
        self.assertEqual(bot.db_get_account(account)["email_link_delivered"], 0)
        self.assertIsNone(state.current)

class OutlookSmsSnapshotTests(HeroPrepared):
    async def test_resume_issued_sms_uses_sms_snapshot_not_reassigned_key(self):
        account, state = await self.prepare()
        bot.db_add_activation(self.user["id"], 987, "77001234567", 0.09, "rr", 2, api_key=KEY)
        bot.db_update_account(account, activation_id=987, phone="77001234567", cost=0.09, status="sms_waiting")
        self.mock("get_user_hero_key", return_value="synthetic-reassigned-key")
        await state.clear()
        await bot.cb_resume_wolt(Callback("resume_wolt:" + str(account)), state)
        self.assertEqual(state.data["hero_key"], KEY)
        self.assertEqual(state.data["mail_api_key"], KEY)

class OutlookConcurrentTests(Isolated):
    async def test_concurrent_confirmation_and_shared_key_users_keep_exact_ownership(self):
        self.quotes()
        cb, state = Callback(), State()
        await self.open_quote(cb, state)
        cb.data = "confirm_outlook:" + state.data["outlook_confirm"]
        buy = self.mock("hero_email_buy", return_value=activation())
        await asyncio.gather(bot.cb_confirm_outlook(cb, state), bot.cb_confirm_outlook(cb, state))
        self.assertEqual(buy.call_count, 1)
        with sqlite3.connect(bot.DB_PATH) as conn:
            other = conn.execute("INSERT INTO users(telegram_id,hero_key) VALUES(42,?)", (KEY,)).lastrowid
        cb2, state2 = Callback(tid=42), State()
        await self.open_quote(cb2, state2)
        cb2.data = "confirm_outlook:" + state2.data["outlook_confirm"]
        buy.return_value = activation(id=124, email="second@outlook.com")
        await bot.cb_confirm_outlook(cb2, state2)
        self.assertEqual(bot.db_get_latest_account(other)["mail_activation_id"], 124)
        self.assertEqual(bot.db_get_latest_account(self.user["id"])["mail_activation_id"], 123)
        self.assertEqual(bot.db_email_intent(other)["account_id"], state2.data["account_id"])
        self.assertEqual(bot.db_email_intent(self.user["id"])["account_id"], state.data["account_id"])

class OutlookLegacyMigrationTests(Isolated):
    async def test_real_legacy_shape_twice_migration_preserves_rows_and_sms_statistics(self):
        aid = bot.db_add_account(self.user["id"], "old@mail.test", "pw", "mailtm-legacy", "token", cost=0.1)
        bot.db_add_activation(self.user["id"], 77, "77001234567", 0.1, "rr", 2, api_key=KEY)
        bot.db_update_activation(77, status="completed", completed_at="2026-10-14 00:00:00")
        before = dict(bot.db_get_account(aid))
        with sqlite3.connect(bot.DB_PATH) as conn:
            for column in ("mail_provider", "mail_activation_id", "mail_api_key", "mail_cost", "mail_status", "mail_session", "email_link_delivered"):
                conn.execute("ALTER TABLE accounts DROP COLUMN " + column)
            conn.execute("DROP TABLE email_purchase_intents")
        bot.db_init(); bot.db_init()
        after = dict(bot.db_get_account(aid))
        for column, value in before.items():
            self.assertEqual(after[column], value)
        self.assertEqual(bot.db_get_recent_activations(1)[0]["cost"], 0.1)
        self.assertEqual(bot.db_get_recent_activations(1)[0]["status"], "completed")
        with sqlite3.connect(bot.DB_PATH) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

class OutlookHttpSchemaTests(unittest.TestCase):
    def test_purchase_rejects_wrong_success_status_and_catalog_does_not_accept_bool_cost(self):
        # 200/201 with a full validated resource are supported; 202 is not a
        # confirmed purchase and must keep the fail-closed behaviour.
        with patch.object(bot.httpx, "post", return_value=httpx.Response(202, json={"data": activation()})):
            with self.assertRaises(bot.HeroEmailError): bot.hero_email_buy(KEY)
        with patch.object(bot.httpx, "get", return_value=httpx.Response(200, json={"data": [{"name": "outlook.com", "cost": True, "count": 5}]})):
            with self.assertRaises(bot.HeroEmailError): bot.hero_email_offer(KEY)

class OutlookTimeoutTests(HeroPrepared):
    async def test_wait_polls_every_five_seconds_and_timeout_keeps_activation_without_cancel(self):
        account, state = await self.prepare()
        clock = [0.0]
        self.mock("time.time", new=lambda: clock[0])
        async def tick(seconds):
            self.assertEqual(seconds, 5)
            clock[0] += seconds
        self.mock("asyncio.sleep", new=tick)
        get = self.mock("hero_email_get", return_value=activation())
        delete = self.mock("hero_email_delete")
        sms = self.mock("_purchase_number", new=AsyncMock())
        await bot._wait_wolt_email(bot.ADMIN_ID, state)
        self.assertEqual(clock[0], 300)
        self.assertEqual(get.call_count, 60)
        row = bot.db_get_account(account)
        self.assertEqual(row["status"], "email_timeout")
        self.assertEqual(row["mail_status"], "WAIT")
        self.assertEqual(row["mail_activation_id"], 123)
        delete.assert_not_called(); sms.assert_not_called()

class OutlookQuoteAwaitTests(Isolated):
    async def test_quote_state_change_during_initial_network_await_is_not_overwritten(self):
        self.quotes()
        cb, state = Callback(), State()
        real_thread = asyncio.to_thread
        async def changed(fn, *args, **kwargs):
            result = await real_thread(fn, *args, **kwargs)
            if fn is bot.hero_email_offer:
                await state.clear()
                await state.set_state(bot.RegState.waiting_sms)
                await state.update_data(original="new-session")
            return result
        self.mock("asyncio.to_thread", new=changed)
        await self.open_quote(cb, state)
        self.assertEqual(state.current, bot.RegState.waiting_sms.state)
        self.assertEqual(state.data, {"original": "new-session"})

class OutlookSmsPrebuyFenceTests(HeroPrepared):
    async def test_deactivation_during_sms_price_await_prevents_number_purchase(self):
        account, state = await self.prepare()
        bot.db_update_account(account, email_link=LINK, email_link_delivered=1, status="email_received")
        await state.update_data(email_link=LINK)
        self.quotes()
        buy = self.mock("hero_buy_number", return_value={"activationId": 987, "phoneNumber": "77001234567", "activationCost": "0.09"})
        self.mock("_hero", return_value="ACCESS_READY")
        real_thread = asyncio.to_thread
        async def disabled(fn, *args, **kwargs):
            result = await real_thread(fn, *args, **kwargs)
            if fn is bot.hero_get_price:
                with sqlite3.connect(bot.DB_PATH) as conn:
                    conn.execute("UPDATE users SET is_active=0 WHERE id=?", (self.user["id"],))
            return result
        self.mock("asyncio.to_thread", new=disabled)
        await bot._wolt_buy_number(bot.ADMIN_ID, state)
        buy.assert_not_called()

class OutlookSmsSecretSafetyTests(HeroPrepared):
    async def test_sms_errors_do_not_echo_or_log_raw_provider_key_body(self):
        account, state = await self.prepare()
        bot.db_update_account(account, email_link=LINK, email_link_delivered=1, status="email_received")
        await state.update_data(email_link=LINK)
        self.quotes()
        self.mock("hero_buy_number", side_effect=ValueError(KEY + " <html>secret-provider-body</html>"))
        warning = self.mock("log.warning")
        self.mock("asyncio.sleep", new=AsyncMock())
        await bot._wolt_buy_number(bot.ADMIN_ID, state)
        public = str(bot._safe_send.call_args_list) + str(warning.call_args_list)
        self.assertNotIn(KEY, public)
        self.assertNotIn("secret-provider-body", public)

class OutlookSmsPollSecretTests(HeroPrepared):
    async def test_existing_sms_watcher_sanitizes_provider_errors_for_outlook_session(self):
        account, state = await self.prepare()
        bot.db_update_account(account, activation_id=987, status="sms_waiting")
        bot.db_add_activation(self.user["id"], 987, "77001234567", 0.09, "rr", 2, api_key=KEY)
        await state.update_data(activation_id=987, sms_watch_id="watch")
        await state.set_state(bot.RegState.waiting_sms)
        self.mock("sms_check", side_effect=ValueError(KEY + "<html>secret-provider-body</html>"))
        warning = self.mock("log.warning")
        async def stop(seconds): await state.clear()
        self.mock("asyncio.sleep", new=stop)
        await bot._wait_sms(bot.ADMIN_ID, state)
        self.assertNotIn(KEY, str(warning.call_args_list))
        self.assertNotIn("secret-provider-body", str(warning.call_args_list))

class OutlookStartRecoveryTests(HeroPrepared):
    async def test_start_offers_saved_email_received_resume_without_any_purchase(self):
        account, state = await self.prepare()
        bot.db_update_account(account, status="email_received", email_link=LINK, mail_status="SUCCESS")
        await state.clear()
        buy = self.mock("hero_email_buy")
        number = self.mock("hero_buy_number")
        msg = SimpleNamespace(from_user=SimpleNamespace(id=bot.ADMIN_ID), chat=SimpleNamespace(id=bot.ADMIN_ID), answer=AsyncMock())
        await bot.cmd_start(msg, state)
        markup = bot._replace_ui.call_args.kwargs["reply_markup"]
        self.assertIn("resume_wolt:" + str(account), [b.callback_data for row in markup.inline_keyboard for b in row])
        buy.assert_not_called(); number.assert_not_called()
        self.assertIsNone(state.current)

class OutlookAmbiguousDeleteTests(HeroPrepared):
    async def test_cancel_readback_network_error_preserves_id_and_snapshot(self):
        account, state = await self.prepare()
        get = self.mock("hero_email_get", side_effect=[activation(), bot.HeroEmailError("secret-safe unconfirmed")])
        delete = self.mock("hero_email_delete")
        cb = Callback("mail_del_do:" + str(account))
        await bot.cb_mail_del_do(cb)
        row = bot.db_get_account(account)
        self.assertIsNotNone(row)
        self.assertEqual(row["mail_activation_id"], 123)
        self.assertEqual(row["mail_api_key"], KEY)
        delete.assert_called_once_with(123, KEY)
        self.assertIn("сохранена", cb.answer.call_args.args[0])

class OutlookIncompleteSmsIntentTests(HeroPrepared):
    async def test_restart_with_inflight_sms_intent_requires_manual_reconciliation(self):
        account, state = await self.prepare()
        bot.db_update_account(account, status="email_received", email_link=LINK, email_link_delivered=1, mail_session="sms_buying")
        await state.clear()
        purchase = self.mock("_purchase_number", new=AsyncMock())
        cb = Callback("resume_wolt:" + str(account))
        await bot.cb_resume_wolt(cb, state)
        purchase.assert_not_called()
        self.assertIsNone(state.current)
        self.assertIn("ручная", cb.answer.call_args.args[0])

class OutlookInvalidCostTests(unittest.TestCase):
    def test_malformed_cost_shapes_raise_only_secret_safe_email_errors(self):
        for cost in (None, "", "malformed", {"secret": KEY}, [], True):
            with self.subTest(cost_type=type(cost).__name__), patch.object(bot.httpx, "post", return_value=httpx.Response(201, json={"data": activation(cost=cost)})):
                try:
                    bot.hero_email_buy(KEY)
                except bot.HeroEmailError as exc:
                    self.assertNotIn(KEY, str(exc))
                except Exception as exc:
                    self.fail("unsanitized schema exception type: " + type(exc).__name__)
                else:
                    self.fail("invalid price accepted")

class OutlookVersionTests(unittest.TestCase):
    def test_feature_version_matches_startup_banner(self):
        self.assertIn("v2.16", bot.__doc__)
        source = Path(bot.__file__).read_text()
        self.assertIn('log.info("SMS bot v2.16 started")', source)
        readme = Path(bot.__file__).with_name("README.md").read_text()
        self.assertIn("**Текущая линия: v2.16**", readme)
        self.assertIn("domain=<выбранный домен>", readme)

class OutlookSwitchedQuoteTests(Isolated):
    async def test_old_outlook_confirmation_is_invalid_after_service_choice_changes(self):
        self.quotes()
        buy = self.mock("hero_email_buy", return_value=activation())
        cb, state = Callback(), State()
        await self.open_quote(cb, state)
        token = state.data["outlook_confirm"]
        await bot.cb_choose_service(Callback("choose_service"), state)
        await bot.cb_confirm_outlook(Callback("confirm_outlook:" + token), state)
        buy.assert_not_called()
        await self.open_quote(cb, state)
        token = state.data["outlook_confirm"]
        await state.update_data(service_key="gmail")
        await bot.cb_confirm_outlook(Callback("confirm_outlook:" + token), state)
        buy.assert_not_called()

class OutlookServiceEntryInvalidationTests(Isolated):
    async def test_every_other_service_entry_invalidates_outlook_confirmation(self):
        self.quotes()
        buy = self.mock("hero_email_buy", return_value=activation())
        self.mock("_show_gmail_countries", new=AsyncMock())
        self.mock("_begin_wolt_registration", new=AsyncMock())
        cb, state = Callback(), State()
        for name in ("cb_start_gmail", "cb_start_wolt", "cb_start_wolt_sms", "cb_start_combo"):
            with self.subTest(handler=name):
                await state.clear()
                await self.open_quote(cb, state)
                token = state.data["outlook_confirm"]
                await getattr(bot, name)(cb, state)
                await bot.cb_confirm_outlook(Callback("confirm_outlook:" + token), state)
                buy.assert_not_called()

class OutlookPostServiceChangeTests(Isolated):
    async def test_service_change_during_paid_post_saves_email_but_keeps_new_service_state(self):
        self.quotes()
        cb, state = Callback(), State()
        await self.open_quote(cb, state)
        cb.data = "confirm_outlook:" + state.data["outlook_confirm"]
        self.mock("hero_email_buy", return_value=activation())
        real_thread = asyncio.to_thread
        async def changed(fn, *args, **kwargs):
            value = await real_thread(fn, *args, **kwargs)
            if fn is bot.hero_email_buy:
                await state.update_data(service_key="gmail")
            return value
        self.mock("asyncio.to_thread", new=changed)
        await bot.cb_confirm_outlook(cb, state)
        self.assertEqual(state.data["service_key"], "gmail")
        self.assertEqual(state.current, bot.RegState.confirming.state)
        self.assertEqual(bot.db_get_latest_account(self.user["id"])["mail_activation_id"], 123)

class OutlookUnknownSmsDeleteTests(HeroPrepared):
    async def test_unknown_sms_activation_blocks_local_hero_mail_deletion(self):
        account, state = await self.prepare()
        bot.db_update_account(account, mail_status="SUCCESS", activation_id=999999, status="sms_waiting")
        delete = self.mock("hero_email_delete")
        count = await bot._delete_all_user_mailboxes(self.user["id"], account, KEY)
        self.assertEqual(count, (0, 1))
        self.assertIsNotNone(bot.db_get_account(account))
        await bot.cb_mail_del_do(Callback("mail_del_do:" + str(account)))
        self.assertIsNotNone(bot.db_get_account(account))
        delete.assert_not_called()
