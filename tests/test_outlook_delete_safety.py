"""Synthetic regressions: never delete an Outlook row during ambiguous SMS buy."""
import asyncio
from unittest.mock import AsyncMock
import bot
from test_outlook_flow import HeroPrepared, Callback, LINK


class OutlookDeleteIntentSafetyTests(HeroPrepared):
    async def test_durable_sms_buying_intent_blocks_remote_mail_deletion_after_restart(self):
        account_id, _ = await self.prepare()
        bot.db_update_account(account_id, mail_session="sms_buying")
        cancel = self.mock("_cancel_hero_email", new=AsyncMock())
        with self.assertRaises(bot.HeroEmailError):
            await bot._delete_remote_mail(bot.db_get_account(account_id))
        self.assertEqual(bot.db_get_account(account_id)["mail_session"], "sms_buying")
        cancel.assert_not_awaited()

    async def test_bulk_delete_preserves_uncertain_sms_purchase_record(self):
        account_id, _ = await self.prepare()
        bot.db_update_account(account_id, mail_session="sms_buying")
        self.mock("_cancel_hero_email", new=AsyncMock())
        deleted, failed = await bot._delete_all_user_mailboxes(self.user["id"], account_id)
        self.assertEqual((deleted, failed), (0, 1))
        self.assertEqual(bot.db_get_account(account_id)["mail_session"], "sms_buying")

    async def test_single_delete_during_sms_network_await_preserves_record_and_intent(self):
        account_id, state = await self.prepare()
        bot.db_update_account(account_id, email_link=LINK, email_link_delivered=1)
        await state.update_data(email_link=LINK)
        entered, release = asyncio.Event(), asyncio.Event()

        async def purchase(*args, **kwargs):
            entered.set()
            await release.wait()
            return {"activationId": 99001, "phoneNumber": "77001234567", "activationCost": 0.09}

        self.mock("_purchase_number", new=purchase)
        cancel = self.mock("_cancel_hero_email", new=AsyncMock())
        task = asyncio.get_running_loop().create_task(bot._wolt_buy_number(bot.ADMIN_ID, state))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            self.assertEqual(bot.db_get_account(account_id)["mail_session"], "sms_buying")
            await asyncio.wait_for(bot.cb_mail_del_do(Callback(data=f"mail_del_do:{account_id}")), timeout=2)
            row = bot.db_get_account(account_id)
            self.assertIsNotNone(row, "Deletion must not discard a pending paid SMS purchase")
            self.assertEqual(row["mail_session"], "sms_buying")
            cancel.assert_not_awaited()
        finally:
            release.set()
            await task
