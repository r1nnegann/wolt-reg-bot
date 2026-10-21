"""Selectable HeroSMS domains: synthetic fixtures, never real purchases."""
import asyncio
import sqlite3
import unittest
from unittest.mock import patch, AsyncMock
import httpx
import bot
from test_outlook_flow import KEY, LINK, activation, Isolated, Callback, State


class DomainCatalogTests(unittest.TestCase):
    def test_site_bound_catalog_filters_valid_in_stock_and_sorts_price_then_name(self):
        rows = [dict(name="gmail.com", cost="0.14", count=8),
                dict(name="mail.com", cost=0.045, count=3),
                dict(name="gmx.ie", cost=0.045, count=2),
                dict(name="hotmail.com", cost=0.051, count=0),
                dict(name="evil..test", cost=0.01, count=1),
                dict(name="bad.test", cost=True, count=1),
                dict(name="bad2.test", cost="NaN", count=1),
                dict(name="bad3.test", cost=0.01, count=True),
                dict(name="bad4.test", cost=-1, count=1), None]
        self.assertTrue(callable(getattr(bot, "hero_email_offers", None)), "dynamic catalog missing")
        with patch.object(bot.httpx, "get", return_value=httpx.Response(200, json={"data": rows})) as get:
            offers = bot.hero_email_offers(KEY)
            self.assertEqual(offers, [dict(name="gmx.ie", cost=0.045, count=2),
                                      dict(name="mail.com", cost=0.045, count=3),
                                      dict(name="gmail.com", cost=0.14, count=8)])
            self.assertEqual(get.call_args.kwargs["params"], {"site": "wolt.com"})
            self.assertEqual(get.call_args.kwargs["headers"], {"Authorization": "ApiKey " + KEY})
            self.assertEqual(bot.hero_email_offer(KEY, "gmail.com"), (0.14, 8))
            self.assertEqual(bot.hero_email_offer(KEY), (0, 0), "absent Outlook must not substitute")
        for malformed in ({}, None, "PRIVATE " + KEY):
            with patch.object(bot.httpx, "get", return_value=httpx.Response(200, json={"data": malformed})):
                with self.assertRaises(bot.HeroEmailError) as caught:
                    bot.hero_email_offers(KEY)
                self.assertNotIn(KEY, str(caught.exception))
        duplicate = [dict(name="gmail.com", cost=0.1, count=1), dict(name="gmail.com", cost=0.2, count=2)]
        with patch.object(bot.httpx, "get", return_value=httpx.Response(200, json={"data": duplicate})):
            self.assertEqual(bot.hero_email_offers(KEY), [], "ambiguous duplicate offers cannot be selected")


class DomainPickerTests(Isolated):
    async def test_entry_selection_and_paid_confirmation_use_only_chosen_live_domain(self):
        self.quotes(mail=0.14)
        self.mock("hero_email_offers", return_value=[dict(name="gmail.com", cost=0.14, count=8)])
        offer = self.mock("hero_email_offer", return_value=(0.14, 8))
        post = self.mock("httpx.post", return_value=httpx.Response(201, json={"data": activation(email="random@gmail.com")}))
        cb, state = Callback(), State()
        await bot.cb_start_wolt_outlook(cb, state)
        buttons = [b for row in cb.message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard for b in row]
        self.assertTrue(any("gmail.com" in b.text and "0.1400" in b.text and "8" in b.text for b in buttons), "entry must show domains, not Outlook quote")
        self.assertFalse(any((b.callback_data or "").startswith("confirm_outlook:") for b in buttons))
        post.assert_not_called()
        selected = next(b.callback_data for b in buttons if b.callback_data.startswith("hero_domain:"))
        cb = Callback(selected)
        await bot.cb_hero_domain(cb, state)
        self.assertEqual(state.data["mail_domain"], "gmail.com")
        offer.assert_called_with(KEY, "gmail.com")
        for text in ("gmail.com", "0.1400", "0.0900", "2.00", "0.2350"):
            self.assertIn(text, cb.message.edit_text.call_args.args[0])
        self.assertNotIn("Outlook", cb.message.edit_text.call_args.args[0])
        post.assert_not_called()
        token = state.data["outlook_confirm"]
        await bot.cb_confirm_outlook(Callback("confirm_outlook:" + token), state)
        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs["json"], {"site": "wolt.com", "domain": "gmail.com"})
        self.assertEqual(bot.db_email_intent(self.user["id"])["domain"], "gmail.com")
        self.assertEqual(bot.db_get_latest_account(self.user["id"])["email"], "random@gmail.com")
        self.assertEqual(state.current, bot.RegState.waiting_wolt_email.state)


class DomainPaginationTests(Isolated):
    async def test_pagination_and_refresh_are_free_and_refresh_invalidates_old_selection(self):
        self.quotes()
        offers = [dict(name=f"mail{i:02d}.test", cost=0.045 + i / 1000, count=i + 1) for i in range(19)]
        catalog = self.mock("hero_email_offers", return_value=offers)
        buy = self.mock("hero_email_buy")
        cb, state = Callback(), State()
        await bot.cb_start_wolt_outlook(cb, state)
        def buttons(callback):
            return [b for row in callback.message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard for b in row]
        first = buttons(cb)
        self.assertEqual(len([b for b in first if b.callback_data.startswith("hero_domain:")]), 8, "bounded domain page required")
        old_select = next(b.callback_data for b in first if b.callback_data.startswith("hero_domain:"))
        next_page = next(b.callback_data for b in first if b.callback_data.startswith("hero_domains_page:"))
        cb = Callback(next_page)
        await bot.cb_hero_domains(cb, state)
        self.assertEqual([b.text.split(" · ")[0] for b in buttons(cb) if b.callback_data.startswith("hero_domain:")], [o["name"] for o in offers[8:16]])
        self.assertEqual(catalog.call_count, 1, "pagination uses server snapshot, not new provider I/O")
        refresh = next(b.callback_data for b in buttons(cb) if b.callback_data.startswith("hero_domains_refresh:"))
        old_token = state.data["hero_domains_token"]
        catalog.return_value = [dict(name="new.test", cost=0.08, count=4)]
        cb = Callback(refresh)
        await bot.cb_hero_domains(cb, state)
        self.assertNotEqual(state.data["hero_domains_token"], old_token)
        self.assertEqual(catalog.call_count, 2)
        self.assertEqual(state.data["hero_domains_page"], 0)
        await bot.cb_hero_domain(Callback(old_select), state)
        self.assertIsNone(state.data["mail_domain"])
        cb = Callback(next(b.callback_data for b in buttons(cb) if b.callback_data.startswith("hero_domain:")))
        await bot.cb_hero_domain(cb, state)
        self.assertEqual(state.data["mail_domain"], "new.test")
        buy.assert_not_called()


class DomainQuoteNavigationTests(Isolated):
    async def test_quote_refresh_preserves_domain_and_domain_switch_invalidates_old_confirmation(self):
        self.quotes()
        self.mock("hero_email_offers", return_value=[dict(name="gmail.com", cost=0.14, count=8), dict(name="mail.com", cost=0.045, count=3)])
        offer = self.mock("hero_email_offer", side_effect=lambda key, domain: (0.14, 8) if domain == "gmail.com" else (0.045, 3))
        buy = self.mock("hero_email_buy", return_value=activation(email="random@mail.com"))
        cb, state = Callback(), State()
        await self.open_quote(cb, state, "gmail.com")
        old = state.data["outlook_confirm"]
        buttons = [b for row in cb.message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard for b in row]
        self.assertTrue(any(b.callback_data.startswith("hero_quote_refresh:") for b in buttons), "quote needs domain-preserving refresh")
        refresh = next(b.callback_data for b in buttons if b.callback_data.startswith("hero_quote_refresh:"))
        cb = Callback(refresh)
        await bot.cb_hero_quote(cb, state)
        self.assertEqual(state.data["mail_domain"], "gmail.com")
        self.assertNotEqual(state.data["outlook_confirm"], old)
        offer.assert_called_with(KEY, "gmail.com")
        await bot.cb_confirm_outlook(Callback("confirm_outlook:" + old), state)
        buy.assert_not_called()
        buttons = [b for row in cb.message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard for b in row]
        old = state.data["outlook_confirm"]
        cb = Callback(next(b.callback_data for b in buttons if b.callback_data.startswith("hero_quote_domains:")))
        await bot.cb_hero_quote(cb, state)
        await bot.cb_confirm_outlook(Callback("confirm_outlook:" + old), state)
        token = state.data["hero_domains_token"]
        await bot.cb_hero_domain(Callback(f"hero_domain:{token}:1"), state)
        self.assertEqual(state.data["mail_domain"], "mail.com")
        await bot.cb_confirm_outlook(Callback("confirm_outlook:" + old), state)
        buy.assert_not_called()
        await bot.cb_confirm_outlook(Callback("confirm_outlook:" + state.data["outlook_confirm"]), state)
        buy.assert_called_once_with(KEY, domain="mail.com")


class DomainCallbackFenceTests(Isolated):
    async def test_picker_callbacks_are_server_bound_and_invalidated_by_service_entries(self):
        import sqlite3
        self.quotes()
        buy = self.mock("hero_email_buy")
        self.mock("_show_gmail_countries", new=__import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock())
        self.mock("_begin_wolt_registration", new=__import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock())
        cb, state = Callback(), State()
        await bot.cb_start_wolt_outlook(cb, state)
        token = state.data["hero_domains_token"]
        original = dict(state.data)
        for raw in (None, 42, "hero_domain", f"hero_domain:{token}:gmail.com", f"hero_domain:{token}:-1",
                    f"hero_domain:{token}:True", f"hero_domain:{token}:00", f"hero_domain:{token}:99",
                    f"hero_domain:{token}:0:extra", "hero_domain:stale:0"):
            await bot.cb_hero_domain(Callback(raw), state)
            self.assertEqual(state.data, original)
        for raw in (None, "hero_domains_refresh", f"hero_domains_refresh:{token}:extra", f"hero_domains_page:{token}:-1",
                    f"hero_domains_page:{token}:99", f"hero_domains_page:{token}:false"):
            await bot.cb_hero_domains(Callback(raw), state)
            self.assertEqual(state.data, original)
        for tid in (999, 42):
            if tid == 42:
                with sqlite3.connect(bot.DB_PATH) as conn:
                    conn.execute("INSERT INTO users(telegram_id,hero_key) VALUES(42,?)", (KEY,))
            await bot.cb_hero_domain(Callback(f"hero_domain:{token}:0", tid=tid), state)
            self.assertEqual(state.data, original)
        for handler in ("cb_choose_service", "cb_start_gmail", "cb_start_combo", "cb_start_wolt", "cb_start_wolt_sms", "cb_cancel", "cb_back_menu"):
            await state.clear()
            await bot.cb_start_wolt_outlook(Callback(), state)
            token = state.data["hero_domains_token"]
            await getattr(bot, handler)(Callback("choose_service"), state)
            snapshot, current = dict(state.data), state.current
            await bot.cb_hero_domain(Callback(f"hero_domain:{token}:0"), state)
            self.assertEqual(state.data, snapshot, handler + " must invalidate picker")
            self.assertEqual(state.current, current)
        buy.assert_not_called()


class DomainAcknowledgementFenceTests(Isolated):
    async def test_navigation_never_revives_state_changed_during_acknowledgement(self):
        self.quotes()
        catalog = self.mock("hero_email_offers", return_value=[dict(name="gmail.com", cost=0.14, count=1)])
        offer = self.mock("hero_email_offer", return_value=(0.14, 1))
        for mode in ("entry", "select", "page", "refresh", "quote_refresh", "quote_domains"):
            with self.subTest(mode=mode):
                state = State()
                if mode.startswith("quote_"):
                    await self.open_quote(Callback(), state, "gmail.com")
                    raw = f"hero_{mode}:{state.data['outlook_confirm']}"
                    handler = bot.cb_hero_quote
                elif mode == "entry":
                    raw, handler = "start_wolt_outlook", bot.cb_start_wolt_outlook
                else:
                    await bot.cb_start_wolt_outlook(Callback(), state)
                    token = state.data["hero_domains_token"]
                    if mode == "select":
                        raw, handler = f"hero_domain:{token}:0", bot.cb_hero_domain
                    else:
                        raw = f"hero_domains_page:{token}:0" if mode == "page" else f"hero_domains_refresh:{token}"
                        handler = bot.cb_hero_domains
                counts = catalog.call_count, offer.call_count
                cb = Callback(raw)
                async def changed():
                    await state.clear()
                    await state.set_state(bot.RegState.waiting_sms)
                    await state.update_data(original="new-session")
                cb.answer = AsyncMock(side_effect=changed)
                await handler(cb, state)
                self.assertEqual(state.current, bot.RegState.waiting_sms.state)
                self.assertEqual(state.data, {"original": "new-session"})
                self.assertEqual((catalog.call_count, offer.call_count), counts)
                cb.answer.assert_awaited_once()


class DomainConfirmKeyTests(Isolated):
    async def test_confirm_reloads_key_assignment_after_network_and_requires_fresh_confirmation(self):
        self.quotes(mail=0.14)
        self.mock("hero_email_offers", return_value=[dict(name="gmail.com", cost=0.14, count=8)])
        with sqlite3.connect(bot.DB_PATH) as conn:
            conn.execute("UPDATE users SET hero_key=?,use_admin_hero=0,admin_key_id=NULL WHERE id=?", (KEY, self.user["id"]))
        self.mock("get_user_hero_key", new=lambda user: user["hero_key"])
        buy = self.mock("hero_email_buy", return_value=activation(email="random@gmail.com"))
        for next_key in ("synthetic-reassigned-key", None):
            with self.subTest(next_key=next_key):
                with sqlite3.connect(bot.DB_PATH) as conn:
                    conn.execute("UPDATE users SET hero_key=? WHERE id=?", (KEY, self.user["id"]))
                state = State()
                self.mock("hero_email_offer", return_value=(0.14, 8))
                await self.open_quote(Callback(), state, "gmail.com")
                old = state.data["outlook_confirm"]
                def refresh(key, domain):
                    self.assertEqual(domain, "gmail.com")
                    with sqlite3.connect(bot.DB_PATH) as conn:
                        conn.execute("UPDATE users SET hero_key=? WHERE id=?", (next_key, self.user["id"]))
                    return 0.14, 8
                offer = self.mock("hero_email_offer", new=refresh)
                await bot.cb_confirm_outlook(Callback("confirm_outlook:" + old), state)
                buy.assert_not_called()
                self.assertNotEqual(state.data.get("outlook_confirm"), old)
                if next_key:
                    self.assertEqual(state.data["mail_api_key"], next_key)
                    self.assertEqual(state.data["mail_domain"], "gmail.com")
                else:
                    self.assertIsNone(state.data.get("outlook_confirm"))


class SelectedDomainRecoveryTests(Isolated):
    async def test_non_outlook_purchase_resumes_polls_delivers_sms_and_saved_mail_with_original_key(self):
        self.quotes(mail=0.14)
        self.mock("hero_email_offers", return_value=[dict(name="gmail.com", cost=0.14, count=8)])
        post = self.mock("httpx.post", return_value=httpx.Response(201, json={"data": activation(email="random@gmail.com", cost=0.14)}))
        mailtm = [self.mock(name, side_effect=AssertionError("HeroSMS resource must never touch Mail.tm"))
                  for name in ("mail_create", "mail_login", "mail_fetch", "mail_delete")]
        cb, state = Callback(), State()
        await self.open_quote(cb, state, "gmail.com")
        await bot.cb_confirm_outlook(Callback("confirm_outlook:" + state.data["outlook_confirm"]), state)
        account = state.data["account_id"]
        post.assert_called_once()
        await state.clear()
        await bot.cb_resume_wolt(Callback(f"resume_wolt:{account}"), state)
        calls = []
        def email_get(aid, key):
            calls.append("mail")
            self.assertEqual((aid, key), (123, KEY))
            return activation(email="random@gmail.com", cost=0.14, status="SUCCESS", message=f'<a href="{LINK}">Wolt</a>')
        self.mock("hero_email_get", new=email_get)
        async def delivery(*args, **kwargs):
            calls.append("link")
            return object()
        self.mock("_send_wolt_link", new=delivery)
        def number(service, country, max_price, api_key):
            calls.append("sms")
            self.assertEqual((service, country, api_key), ("rr", 2, KEY))
            return {"activationId": 987, "phoneNumber": "77001234567", "activationCost": "0.09"}
        self.mock("hero_buy_number", new=number)
        self.mock("_hero", return_value="ACCESS_READY")
        await bot._wait_wolt_email(bot.ADMIN_ID, state)
        self.assertEqual(calls, ["mail", "link", "sms"])
        self.mock("get_user_hero_key", return_value="synthetic-new-assignment")
        await state.clear()
        await bot.cb_resume_wolt(Callback(f"resume_wolt:{account}"), state)
        self.assertEqual((state.data["mail_api_key"], state.data["hero_key"], state.data["mail_domain"]), (KEY, KEY, "gmail.com"))
        self.mock("sms_check", return_value=(True, "112233"))
        await bot._wait_sms(bot.ADMIN_ID, state)
        row = bot.db_get_account(account)
        self.assertEqual((row["status"], row["mail_cost"], row["cost"]), ("completed", 0.14, 0.09))
        cb = Callback(f"mail_view:{account}")
        await bot.cb_mail_view(cb)
        self.assertNotIn("Outlook", cb.message.edit_text.call_args.args[0], "saved Gmail must not be called Outlook")
        self.assertIn("gmail.com", bot._mail_provider_label(row))
        await bot.cb_mail_link(Callback(f"mail_link:{account}"))
        post.assert_called_once()
        await bot.cb_mail_del_do(Callback(f"mail_del_do:{account}"))
        self.assertIsNone(bot.db_get_account(account))
        for mock in mailtm:
            mock.assert_not_called()


class DomainConfirmationOwnershipTests(Isolated):
    async def test_paid_confirmation_is_bound_to_owner_and_unchanged_selected_domain(self):
        self.quotes(mail=0.14)
        self.mock("hero_email_offers", return_value=[dict(name="gmail.com", cost=0.14, count=8)])
        buy = self.mock("hero_email_buy", return_value=activation(email="random@gmail.com"))
        with sqlite3.connect(bot.DB_PATH) as conn:
            conn.execute("INSERT INTO users(telegram_id,hero_key) VALUES(42,?)", (KEY,))
        state = State()
        await self.open_quote(Callback(), state, "gmail.com")
        token = state.data["outlook_confirm"]
        await bot.cb_confirm_outlook(Callback("confirm_outlook:" + token, tid=42), state)
        buy.assert_not_called()
        real_thread = asyncio.to_thread
        async def changed(fn, *args, **kwargs):
            result = await real_thread(fn, *args, **kwargs)
            if fn is bot.hero_email_offer:
                await state.update_data(mail_domain="mail.com")
            return result
        self.mock("asyncio.to_thread", new=changed)
        await bot.cb_confirm_outlook(Callback("confirm_outlook:" + token), state)
        buy.assert_not_called()
        self.assertEqual(state.data["mail_domain"], "mail.com")


class DomainRepricingTests(Isolated):
    async def test_selected_domain_repricing_disappearance_and_restock_never_substitute(self):
        live_offer = bot.hero_email_offer
        self.quotes(mail=0.14)
        offers = [dict(name="mail.com", cost=0.045, count=5), dict(name="gmail.com", cost=0.14, count=8)]
        self.mock("hero_email_offers", return_value=offers)
        # Exercise the actual per-domain lookup against the changing catalog.
        self.mock("hero_email_offer", new=live_offer)
        state = State()
        await self.open_quote(Callback(), state, "gmail.com")

        buy = self.mock("hero_email_buy", return_value=activation(email="random@gmail.com", cost=0.18))
        old = state.data["outlook_confirm"]
        offers[1]["cost"] = 0.18
        await bot.cb_confirm_outlook(Callback("confirm_outlook:" + old), state)
        buy.assert_not_called()
        self.assertEqual(state.data["mail_quote"], 0.18)
        self.assertEqual(state.data["mail_domain"], "gmail.com")
        await bot.cb_confirm_outlook(Callback("confirm_outlook:" + old), state)
        buy.assert_not_called()
        offers.pop()
        cb = Callback("confirm_outlook:" + state.data["outlook_confirm"])
        await bot.cb_confirm_outlook(cb, state)
        buy.assert_not_called()
        self.assertEqual(state.data["mail_domain"], "gmail.com")
        buttons = [b for row in cb.message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard for b in row]
        self.assertFalse(any(b.callback_data.startswith("confirm_outlook:") for b in buttons))
        self.assertNotIn("<code>mail.com</code>", cb.message.edit_text.call_args.args[0])
        offers.append(dict(name="gmail.com", cost=0.18, count=2))
        token = state.data["outlook_confirm"]
        await bot.cb_hero_quote(Callback("hero_quote_refresh:" + token), state)
        buy.assert_not_called()
        await bot.cb_confirm_outlook(Callback("confirm_outlook:" + state.data["outlook_confirm"]), state)
        buy.assert_called_once_with(KEY, domain="gmail.com")


class DomainUncertainPurchaseTests(Isolated):
    async def test_chosen_domain_is_durable_before_ambiguous_post_and_restart_cannot_rebuy(self):
        self.quotes(mail=0.15)
        self.mock("hero_email_offers", return_value=[dict(name="icloud.com", cost=0.15, count=4)])
        state = State()
        await self.open_quote(Callback(), state, "icloud.com")
        def uncertain(key, domain):
            intent = bot.db_email_intent(self.user["id"])
            self.assertEqual((intent["domain"], intent["status"], intent["api_key"]), ("icloud.com", "uncertain", KEY))
            self.assertEqual(state.data["mail_domain"], "icloud.com")
            raise httpx.ReadTimeout("SYNTHETIC_PRIVATE_BODY " + KEY)
        buy = self.mock("hero_email_buy", side_effect=uncertain)
        with patch.object(bot, "_safe_send_raw", new=AsyncMock()) as notify:
            await bot.cb_confirm_outlook(Callback("confirm_outlook:" + state.data["outlook_confirm"]), state)
            self.assertNotIn(KEY, str(notify.call_args_list))
        await state.clear(); bot.db_init()
        cb = Callback()
        await bot.cb_start_wolt_outlook(cb, state)
        self.assertIn("вручную", cb.answer.call_args.args[0])
        self.assertEqual(bot.db_email_intent(self.user["id"])["domain"], "icloud.com")
        self.assertIsNone(bot.db_get_latest_account(self.user["id"]))
        self.assertIsNone(state.current)
        buy.assert_called_once_with(KEY, domain="icloud.com")


class DomainIntentTests(Isolated):
    async def test_selected_domain_intent_survives_restart_and_legacy_migration(self):
        bot.db_begin_email_intent(self.user["id"], "synthetic-token", KEY, 0.14, domain="gmail.com")
        bot.db_init(); bot.db_init()
        intent = bot.db_email_intent(self.user["id"])
        self.assertEqual((intent["domain"], intent["status"]), ("gmail.com", "uncertain"))
        with self.assertRaises(bot.HeroEmailError):
            bot.db_begin_email_intent(self.user["id"], "another", KEY, 0.045, domain="mail.com")
        self.assertEqual(bot.db_email_intent(self.user["id"])["domain"], "gmail.com")
        import sqlite3
        with sqlite3.connect(bot.DB_PATH) as conn:
            conn.execute("ALTER TABLE email_purchase_intents DROP COLUMN domain")
        bot.db_init(); bot.db_init()
        intent = bot.db_email_intent(self.user["id"])
        self.assertEqual((intent["domain"], intent["status"], intent["api_key"]), ("outlook.com", "uncertain", KEY))
        account = bot.db_add_hero_account(self.user["id"], activation(email="old@gmail.com"), KEY)
        state = State()
        await bot._load_wolt_state(state, bot.db_get_account(account), self.user)
        self.assertEqual(state.data["mail_domain"], "gmail.com")


class SelectedDomainApiTests(unittest.TestCase):
    def test_exact_selected_domain_purchase_and_catalog_independent_read(self):
        payload = activation(email="random@gmail.com", message=None)
        with patch.object(bot.httpx, "post", return_value=httpx.Response(201, json={"data": payload})) as post:
            self.assertEqual(bot.hero_email_buy(KEY, domain="gmail.com"), payload)
            self.assertEqual(post.call_count, 1)
            self.assertEqual(post.call_args.kwargs["json"], {"site": "wolt.com", "domain": "gmail.com"})
        for wrong in ("random@outlook.com", "random@sub.gmail.com", "random@gmail.com.evil.test"):
            with self.subTest(wrong=wrong), patch.object(bot.httpx, "post", return_value=httpx.Response(201, json={"data": activation(email=wrong)})) as post:
                with self.assertRaises(bot.HeroEmailError):
                    bot.hero_email_buy(KEY, domain="gmail.com")
                post.assert_called_once()
        for domain in ("retired.example", "gmx.ie", "yandex.kz"):
            data = activation(email="old@" + domain)
            with patch.object(bot.httpx, "get", return_value=httpx.Response(200, json={"data": data})) as get:
                self.assertEqual(bot.hero_email_get(123, KEY), data)
                get.assert_called_once()
            self.assertEqual(bot._validate_hero_email(data, 123, data["email"]), data)
        for domain in ("evil..test", "-bad.test", "bad_.test", "gmail.com/evil", "почта.рф", "gmaıl.com", "K.com", "single", "a." + "b" * 64):
            with self.subTest(domain=domain), patch.object(bot.httpx, "post") as post:
                with self.assertRaises(bot.HeroEmailError):
                    bot.hero_email_buy(KEY, domain=domain)
                post.assert_not_called()
