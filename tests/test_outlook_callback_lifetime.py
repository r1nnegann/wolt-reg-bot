"""Regression for Telegram callback expiry during a slow, paid mail request."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import bot
from test_outlook_flow import Isolated, Callback, State, activation


class OutlookCallbackLifetimeTests(Isolated):
    async def test_slow_paid_purchase_acknowledges_before_io_and_starts_mail_waiter(self):
        self.quotes()
        cb, state = Callback(), State()
        await bot.cb_start_wolt_outlook(cb, state)
        cb.data = "confirm_outlook:" + state.data["outlook_confirm"]
        purchased = {"done": False}
        tasks = []

        def purchase(key):
            purchased["done"] = True
            return activation()

        async def acknowledge(*args, **kwargs):
            if purchased["done"]:
                raise RuntimeError("SYNTHETIC: Telegram query expired during provider I/O")

        def spawn(coro):
            tasks.append(coro.cr_code.co_name)
            coro.close()
            return SimpleNamespace()

        cb.answer = AsyncMock(side_effect=acknowledge)
        self.mock("hero_email_buy", new=purchase)
        self.mock("asyncio.create_task", new=spawn)
        self.mock("_safe_send_raw", new=AsyncMock())
        try:
            await bot.cb_confirm_outlook(cb, state)
        except RuntimeError:
            pass
        self.assertEqual(state.current, bot.RegState.waiting_wolt_email.state,
                         "A paid/saved mailbox must not lose its watcher on callback expiry")
        self.assertIn("_wait_wolt_email", tasks)
        cb.answer.assert_awaited_once()
