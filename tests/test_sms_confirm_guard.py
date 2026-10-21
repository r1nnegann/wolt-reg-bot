"""SMS consent regressions; real quote/FSM/key resolution, synthetic providers."""
import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import ANY, AsyncMock, patch

import bot
from test_outlook_flow import Callback, State


KEY_A = "synthetic-sms-key-A-0000000000000000"
KEY_B = "synthetic-sms-key-B-0000000000000000"
NUMBER = {"activationId": 987, "phoneNumber": "77001234567", "activationCost": "0.09"}


class SmsConfirmGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(bot, "DB_PATH", str(Path(self.tmp.name) / "bot.db")))
        bot.db_init()
        self.user = bot.db_get_user(bot.ADMIN_ID)
        self.key_a = bot.db_add_admin_key("SMS A", KEY_A)
        self.key_b = bot.db_add_admin_key("SMS B", KEY_B)
        bot.db_assign_user_admin_key(bot.ADMIN_ID, self.key_a)
        self.price = self.stack.enter_context(patch.object(bot, "hero_get_price", return_value=(0.09, 20)))
        self.stack.enter_context(patch.object(bot, "_hero_balance", return_value=2))
        self.stack.enter_context(patch.object(bot, "hero_email_offers", return_value=[
            {"name": "outlook.com", "cost": 0.051, "count": 10}]))
        self.stack.enter_context(patch.object(bot, "hero_email_offer", return_value=(0.051, 10)))
        self.real_purchase = bot._purchase_number
        self.number = self.stack.enter_context(patch.object(bot, "_purchase_number", new=AsyncMock(return_value=NUMBER)))
        self.mail = self.stack.enter_context(patch.object(bot, "hero_email_buy"))
        self.stack.enter_context(patch.object(bot, "_start_sms_watch", new=AsyncMock()))
        self.stack.enter_context(patch.object(bot, "_safe_send_raw", new=AsyncMock()))
        bot._chat_locks.clear()

    async def asyncTearDown(self):
        self.stack.close()
        self.tmp.cleanup()

    async def sms_quote(self, service="wolt", country=None, combo=None):
        cb, state = Callback("start_wolt_sms"), State(combo=combo)
        await bot._show_confirm(cb, state, service, country=country)
        self.assertEqual(state.data["hero_key"], KEY_A)
        markup = cb.message.edit_text.call_args.kwargs["reply_markup"]
        self.assertIn("confirm_buy", [button.callback_data for row in markup.inline_keyboard for button in row])
        self.number.assert_not_awaited()
        return cb, state

    async def test_old_sms_confirm_cannot_spend_in_email_picker_after_key_reassignment(self):
        cb, state = await self.sms_quote()
        cb.data = "choose_service"
        await bot.cb_choose_service(cb, state)
        bot.db_assign_user_admin_key(bot.ADMIN_ID, self.key_b)
        cb.data = "start_wolt_outlook"
        await bot.cb_start_wolt_outlook(cb, state)
        self.assertEqual(state.data["mail_api_key"], KEY_B)
        self.assertTrue(state.data["hero_domains_token"])
        before = (state.current, dict(state.data))
        cb.data = "confirm_buy"
        await bot.cb_confirm_buy(cb, state)
        self.number.assert_not_awaited()
        self.mail.assert_not_called()
        self.assertEqual((state.current, state.data), before)

    async def assert_rejected_preserving_state(self, cb, state):
        before = (state.current, dict(state.data))
        cb.data = "confirm_buy"
        await bot.cb_confirm_buy(cb, state)
        self.number.assert_not_awaited()
        self.mail.assert_not_called()
        self.assertEqual((state.current, state.data), before)

    async def test_sms_quote_to_email_picker_and_quote_with_same_or_new_key(self):
        for change_key in (False, True):
            for select_domain in (False, True):
                with self.subTest(change_key=change_key, select_domain=select_domain):
                    bot.db_assign_user_admin_key(bot.ADMIN_ID, self.key_a)
                    cb, state = await self.sms_quote()
                    await bot.cb_choose_service(cb, state)
                    if change_key:
                        bot.db_assign_user_admin_key(bot.ADMIN_ID, self.key_b)
                    await bot.cb_start_wolt_outlook(cb, state)
                    self.assertIsNone(state.data.get("service_code"))
                    self.assertIsNone(state.data.get("hero_key"))
                    self.assertIsNone(state.data.get("max_price"))
                    if select_domain:
                        cb.data = f"hero_domain:{state.data['hero_domains_token']}:0"
                        await bot.cb_hero_domain(cb, state)
                        self.assertTrue(state.data["outlook_confirm"])
                    expected_key = KEY_B if change_key else KEY_A
                    self.assertEqual(state.data["mail_api_key"], expected_key)
                    await self.assert_rejected_preserving_state(cb, state)

    async def test_current_sms_confirmation_buys_selected_service_country_key_once(self):
        for index, (service, country, combo) in enumerate((
                ("wolt", 2, None), ("gmail", 16, None), ("gmail", 2, "gmail_then_wolt"),
                ("wolt", 2, "gmail_then_wolt"))):
            with self.subTest(service=service, country=country, combo=combo):
                self.number.reset_mock()
                self.number.return_value = dict(NUMBER, activationId=987 + index)
                cb, state = await self.sms_quote(service, country, combo)
                self.price.assert_any_call(bot.SERVICES[service]["code"], country, api_key=KEY_A)
                cb.data = "confirm_buy"
                await bot.cb_confirm_buy(cb, state)
                self.number.assert_awaited_once_with(bot.SERVICES[service]["code"], country, KEY_A,
                                                     owner_chat=bot.ADMIN_ID, can_buy=ANY)
                self.assertEqual(state.current, bot.RegState.waiting_sms.state)
                row = bot.db_get_activation_for_webhook(987 + index)
                self.assertEqual((row["user_id"], row["country"], row["api_key"]),
                                 (self.user["id"], country, KEY_A))
                await bot.cb_confirm_buy(cb, state)
                self.assertEqual(self.number.await_count, 1)

    async def test_key_reassigned_or_revoked_requires_fresh_sms_quote(self):
        for changed_key in (self.key_b, None):
            with self.subTest(changed_key=changed_key):
                bot.db_assign_user_admin_key(bot.ADMIN_ID, self.key_a)
                cb, state = await self.sms_quote()
                bot.db_assign_user_admin_key(bot.ADMIN_ID, changed_key)
                await self.assert_rejected_preserving_state(cb, state)
        bot.db_assign_user_admin_key(bot.ADMIN_ID, self.key_b)
        await bot._show_confirm(cb, state, "wolt")
        self.assertEqual(state.data["hero_key"], KEY_B)
        await bot.cb_confirm_buy(cb, state)
        self.number.assert_awaited_once_with("rr", 2, KEY_B, owner_chat=bot.ADMIN_ID, can_buy=ANY)

    async def test_no_sms_consent_on_service_or_gmail_country_picker(self):
        for picker in ("service", "gmail"):
            with self.subTest(picker=picker):
                self.number.reset_mock()
                cb, state = await self.sms_quote()
                await bot.cb_choose_service(cb, state)
                if picker == "gmail":
                    with patch.object(bot, "hero_service_offers", return_value=[
                            {"id": 16, "cost": 0.09, "count": 20, "eng": "UK", "rus": "UK"}]):
                        await bot._show_gmail_countries(cb, state, refresh=True)
                await self.assert_rejected_preserving_state(cb, state)

    async def test_cancel_active_wrong_owner_or_service_reject_preserving_fsm(self):
        cases = (None, bot.RegState.waiting_sms.state, bot.RegState.waiting_wolt_email.state,
                 bot.RegState.cancelling.state, bot.RegState.completed.state, "owner", "service")
        for case in cases:
            with self.subTest(case=case):
                cb, state = await self.sms_quote()
                if case == "owner":
                    state.data["db_user_id"] = self.user["id"] + 100
                elif case == "service":
                    state.data["service_code"] = "go"
                else:
                    await state.set_state(case)
                await self.assert_rejected_preserving_state(cb, state)
        cb, state = await self.sms_quote()
        await bot.cb_cancel(cb, state)
        await self.assert_rejected_preserving_state(cb, state)

    async def test_key_change_during_acknowledgement_cannot_spend(self):
        cb, state = await self.sms_quote()
        async def reassign(*args, **kwargs):
            bot.db_assign_user_admin_key(bot.ADMIN_ID, self.key_b)
        cb.answer.side_effect = reassign
        await self.assert_rejected_preserving_state(cb, state)

    async def test_concurrent_confirmations_purchase_at_most_once(self):
        cb, state = await self.sms_quote()
        entered, release = asyncio.Event(), asyncio.Event()
        async def purchase(*args, **kwargs):
            entered.set()
            await release.wait()
            return NUMBER
        self.number.side_effect = purchase
        cb.data = "confirm_buy"
        first = asyncio.create_task(bot.cb_confirm_buy(cb, state))
        second = None
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            second = asyncio.create_task(bot.cb_confirm_buy(Callback("confirm_buy"), state))
            await asyncio.sleep(0)
        finally:
            release.set()
            await asyncio.gather(first, *([second] if second else []))
        self.assertEqual(self.number.await_count, 1)
        await bot.cb_confirm_buy(cb, state)
        self.assertEqual(self.number.await_count, 1)

    async def test_key_revoked_before_confirm_is_not_replaced_by_default_key(self):
        cb, state = await self.sms_quote()
        with sqlite3.connect(bot.DB_PATH) as conn:
            conn.execute("UPDATE users SET admin_key_id=NULL, use_admin_hero=0, hero_key=NULL WHERE id=?",
                         (self.user["id"],))
        await self.assert_rejected_preserving_state(cb, state)

    async def test_key_reassigned_during_real_purchase_preflight_cannot_debit(self):
        cb, state = await self.sms_quote()
        def price(*args, **kwargs):
            bot.db_assign_user_admin_key(bot.ADMIN_ID, self.key_b)
            return 0.09, 20
        with patch.object(bot, "_purchase_number", new=self.real_purchase), \
                patch.object(bot, "hero_get_price", side_effect=price), \
                patch.object(bot, "hero_buy_number", return_value=NUMBER) as buy, \
                patch.object(bot, "_hero", return_value="ACCESS_READY"):
            await bot.cb_confirm_buy(cb, state)
        buy.assert_not_called()

    async def test_real_purchase_preflight_accepts_current_sms_consent(self):
        cb, state = await self.sms_quote("gmail", country=2, combo="gmail_then_wolt")
        with patch.object(bot, "_purchase_number", new=self.real_purchase), \
                patch.object(bot, "hero_buy_number", return_value=NUMBER) as buy, \
                patch.object(bot, "_hero", return_value="ACCESS_READY"):
            await bot.cb_confirm_buy(cb, state)
        buy.assert_called_once_with("go", 2, round(0.09 + bot.PRICE_MARGIN, 4), KEY_A)
        self.assertEqual(state.current, bot.RegState.waiting_sms.state)
