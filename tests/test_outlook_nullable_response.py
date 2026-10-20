"""Anonymized observed WAIT shape; HTTP POST envelopes are synthetic replays."""
import unittest
from unittest.mock import patch
import httpx
import bot
from test_outlook_flow import KEY, activation, Isolated, Callback, State


class HeroEmailNullableMessageTests(unittest.TestCase):
    def test_successful_purchase_accepts_null_message_while_waiting_for_first_letter(self):
        # Live GET /emails listed the purchased Outlook as WAIT with message=null.
        # No paid POST is made by this test; it replays that valid resource shape.
        payload = activation(message=None, value=None)
        with patch.object(bot.httpx, "post", return_value=httpx.Response(201, json={"data": payload})) as post:
            try:
                received = bot.hero_email_buy(KEY)
            except bot.HeroEmailError:
                self.fail("A valid paid WAIT activation with message=null was rejected")
        self.assertEqual(received["id"], payload["id"])
        self.assertEqual(received["status"], "WAIT")
        self.assertIsNone(received["message"])
        post.assert_called_once()

    def test_valid_paid_resource_returned_with_http_200_is_not_treated_as_rejection(self):
        # Defensive 2xx compatibility; the original POST status was not captured.
        payload = activation(message=None, value=None)
        with patch.object(bot.httpx, "post", return_value=httpx.Response(200, json={"data": payload})) as post:
            try:
                received = bot.hero_email_buy(KEY)
            except bot.HeroEmailError:
                self.fail("HTTP 200 with a valid activation resource must not be rejected")
        self.assertEqual(received["id"], payload["id"])
        post.assert_called_once()

    def test_post_diagnostic_reports_http_and_field_types_without_payload_values(self):
        payload = activation(message=None, value=None)
        with patch.object(bot.httpx, "post", return_value=httpx.Response(201, json={"data": payload})), patch.object(bot.log, "info") as logged:
            bot.hero_email_buy(KEY)
        output = repr(logged.call_args_list)
        self.assertIn("201", output)
        self.assertIn("NoneType", output)
        self.assertNotIn(KEY, output)
        self.assertNotIn(payload["email"], output)


class OutlookPurchaseDiagnosticTests(Isolated):
    async def test_failure_logs_stage_and_class_but_never_provider_secret(self):
        self.quotes()
        cb, state = Callback(), State()
        await bot.cb_start_wolt_outlook(cb, state)
        cb.data = "confirm_outlook:" + state.data["outlook_confirm"]
        self.mock("hero_email_buy", side_effect=bot.HeroEmailError("PRIVATE_PROVIDER_BODY " + KEY))
        with patch.object(bot.log, "error") as logged:
            await bot.cb_confirm_outlook(cb, state)
        output = repr(logged.call_args_list)
        self.assertIn("purchase_response", output)
        self.assertIn("HeroEmailError", output)
        self.assertNotIn(KEY, output)
        self.assertNotIn("PRIVATE_PROVIDER_BODY", output)
        self.assertEqual(bot.db_email_intent(self.user["id"])["status"], "uncertain")

    async def test_nullable_wait_resource_is_saved_and_mail_waiter_is_started(self):
        self.quotes()
        cb, state = Callback(), State()
        await bot.cb_start_wolt_outlook(cb, state)
        cb.data = "confirm_outlook:" + state.data["outlook_confirm"]
        payload = activation(message=None, value=None)
        with patch.object(bot.httpx, "post", return_value=httpx.Response(201, json={"data": payload})) as post:
            await bot.cb_confirm_outlook(cb, state)
        account = bot.db_get_latest_account(self.user["id"])
        self.assertIsNotNone(account)
        self.assertEqual(account["mail_activation_id"], payload["id"])
        self.assertEqual(account["mail_status"], "WAIT")
        self.assertEqual(bot.db_email_intent(self.user["id"])["status"], "saved")
        self.assertEqual(state.current, bot.RegState.waiting_wolt_email.state)
        post.assert_called_once()


class NullableFieldSchemaSafetyTests(unittest.TestCase):
    def test_wait_get_accepts_null_but_still_rejects_invalid_message_containers(self):
        with patch.object(bot.httpx, "get", return_value=httpx.Response(200, json={"data": activation(message=None)})):
            self.assertIsNone(bot.hero_email_get(123, KEY)["message"])
        for invalid in ([], {}, False, 42):
            with self.subTest(message_type=type(invalid).__name__):
                with self.assertRaises(bot.HeroEmailError):
                    bot._validate_hero_email(activation(message=invalid))
