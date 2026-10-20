"""Public configuration and database permission regressions (synthetic only)."""
import asyncio
import os
from pathlib import Path
import socket
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import bot


def configured(**environment):
    """Execute the real config adapter without reimporting routes or reading .env."""
    source = Path(bot.__file__).read_text(encoding="utf-8")
    start = source.index("BASE_DIR = ")
    end = source.index("\nCOUNTRY_KZ = ", start)
    scope = {"os": os, "__file__": bot.__file__, "load_dotenv": Mock()}
    with patch.dict(os.environ, environment, clear=True):
        exec(compile(source[start:end], bot.__file__, "exec"), scope)
    return scope


class PublicConfigTests(unittest.TestCase):
    def test_default_paths_identity_and_loopback_host(self):
        config = configured()
        root = str(Path(bot.__file__).resolve().parent)
        self.assertEqual(config["BASE_DIR"], root)
        self.assertEqual(config["ENV_PATH"], os.path.join(root, ".env"))
        self.assertEqual(config["DB_PATH"], os.path.join(root, "data", "bot.db"))
        self.assertEqual(config["ADMIN_ID"], 0)
        self.assertEqual(config["ADMIN_NAME"], "Admin")
        self.assertEqual(config["BOT_TOKEN"], "")
        self.assertEqual(config["HERO_KEY"], "")
        self.assertEqual(config["HERO_WEBHOOK_HOST"], "127.0.0.1")
        config["load_dotenv"].assert_called_once_with(config["ENV_PATH"])

    def test_environment_overrides_remain_portable(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = str(Path(directory) / "custom.env")
            db_path = str(Path(directory) / "nested" / "custom.db")
            config = configured(
                SMS_BOT_ENV=env_path, SMS_BOT_DB=db_path, ADMIN_ID="42",
                ADMIN_NAME=" Example Admin ", BOT_TOKEN=" synthetic-token ",
                HERO_KEY=" synthetic-key ", HERO_WEBHOOK_HOST="0.0.0.0",
            )
        self.assertEqual(config["ENV_PATH"], env_path)
        self.assertEqual(config["DB_PATH"], db_path)
        self.assertEqual(config["ADMIN_ID"], 42)
        self.assertEqual(config["ADMIN_NAME"], "Example Admin")
        self.assertEqual(config["BOT_TOKEN"], "synthetic-token")
        self.assertEqual(config["HERO_KEY"], "synthetic-key")
        self.assertEqual(config["HERO_WEBHOOK_HOST"], "0.0.0.0")

    def test_invalid_admin_id_has_no_personal_fallback(self):
        for value in ("", "not-a-number"):
            with self.subTest(value=value):
                self.assertEqual(configured(ADMIN_ID=value)["ADMIN_ID"], 0)

    def test_missing_required_config_fails_before_database_or_network(self):
        for missing in ("BOT_TOKEN", "HERO_KEY", "ADMIN_ID"):
            with self.subTest(missing=missing), patch.object(bot, missing, 0 if missing == "ADMIN_ID" else ""), patch.object(bot, "db_init") as init, patch.object(bot, "Bot") as factory:
                with self.assertRaisesRegex(SystemExit, "Missing required config: " + missing):
                    asyncio.run(bot.main())
                init.assert_not_called()
                factory.assert_not_called()

    def test_webhook_server_uses_configured_host_and_port(self):
        runner = Mock(setup=AsyncMock())
        site = Mock(start=AsyncMock())
        with patch.object(bot, "HERO_WEBHOOK_HOST", "0.0.0.0"), patch.object(bot, "HERO_WEBHOOK_PORT", 0), patch.object(bot.web, "AppRunner", return_value=runner), patch.object(bot.web, "TCPSite", return_value=site) as factory:
            self.assertIs(asyncio.run(bot._start_hero_webhook_server()), runner)
        factory.assert_called_once_with(runner, "0.0.0.0", 0)
        runner.setup.assert_awaited_once()
        site.start.assert_awaited_once()


class PublicDatabaseTests(unittest.TestCase):
    def test_creates_nested_directory_and_seeds_configured_admin_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "nested" / "data" / "test.db")
            with patch.object(bot, "DB_PATH", path), patch.object(bot, "ADMIN_ID", 42), patch.object(bot, "ADMIN_NAME", "PortableAdmin"):
                bot.db_init()
                bot.db_init()
                with sqlite3.connect(path) as connection:
                    users = connection.execute("SELECT telegram_id, first_name, is_admin, use_admin_hero FROM users").fetchall()
                self.assertEqual(users, [(42, "PortableAdmin", 1, 1)])

    def test_unset_admin_id_does_not_seed_a_user(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bot, "DB_PATH", str(Path(directory) / "test.db")), patch.object(bot, "ADMIN_ID", 0):
            bot.db_init()
            with sqlite3.connect(bot.DB_PATH) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM users").fetchone()[0], 0)

    @unittest.skipUnless(os.name == "posix", "POSIX file permissions")
    def test_new_database_is_private_with_permissive_umask(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bot, "DB_PATH", str(Path(directory) / "test.db")):
            previous = os.umask(0o022)
            try:
                bot.db_init()
            finally:
                os.umask(previous)
            self.assertEqual(stat.S_IMODE(os.stat(bot.DB_PATH).st_mode), 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX file permissions")
    def test_existing_database_permissions_are_tightened(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bot, "DB_PATH", str(Path(directory) / "test.db")):
            Path(bot.DB_PATH).touch(mode=0o644)
            os.chmod(bot.DB_PATH, 0o644)
            bot.db_init()
            self.assertEqual(stat.S_IMODE(os.stat(bot.DB_PATH).st_mode), 0o600)


class RunnerIsolationTests(unittest.TestCase):
    def test_sqlite_outside_scratch_and_uri_are_blocked(self):
        for path in (str(Path(bot.__file__).parent / "must-not-create.db"), "file::memory:?cache=shared"):
            with self.subTest(path=path), self.assertRaisesRegex(RuntimeError, "TEST_BLOCKED_NON_SCRATCH_SQLITE"):
                sqlite3.connect(path, uri=path.startswith("file:"))

    def test_external_tcp_is_blocked(self):
        with socket.socket() as connection:
            with self.assertRaisesRegex(RuntimeError, "TEST_BLOCKED_EXTERNAL_NETWORK"):
                connection.connect(("192.0.2.1", 443))

    def test_arbitrary_loopback_tcp_is_blocked(self):
        with socket.socket() as connection:
            with self.assertRaisesRegex(RuntimeError, "TEST_BLOCKED_EXTERNAL_NETWORK"):
                connection.connect_ex(("127.0.0.1", 18091))
