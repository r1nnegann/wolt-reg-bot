#!/usr/bin/env python3
"""Run synthetic tests with scratch-only SQLite and no external network."""
import argparse
import json
import logging
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pattern", default="test*.py", help="unittest discovery pattern")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    parent = Path(tempfile.gettempdir())
    with tempfile.TemporaryDirectory(prefix="sms-bot-tests-", dir=parent) as directory:
        scratch = Path(directory).resolve()
        os.environ.update(
            BOT_TOKEN="123456:UNIT_TEST_ONLY_NOT_A_REAL_TOKEN",
            HERO_KEY="unit-test-only-hero-key-00000000",
            HERO_WEBHOOK_TOKEN="unit-test-only-webhook",
            HERO_WEBHOOK_HOST="127.0.0.1",
            HERO_WEBHOOK_PORT="0",
            ADMIN_ID="1",
            ADMIN_NAME="TestAdmin",
            SMS_BOT_ENV=str(scratch / "isolated.env"),
            SMS_BOT_DB=str(scratch / "isolated.db"),
            TMPDIR=str(scratch),
        )
        Path(os.environ["SMS_BOT_ENV"]).write_text("", encoding="utf-8")
        tempfile.tempdir = str(scratch)
        logging.disable(logging.CRITICAL)
        listeners = {}
        original_bind = socket.socket.bind

        def scratch_audit(event, arguments):
            if event == "sqlite3.connect":
                path = os.fsdecode(arguments[0])
                if path != ":memory:" and (
                    path.startswith("file:") or not Path(path).resolve().is_relative_to(scratch)
                ):
                    raise RuntimeError("TEST_BLOCKED_NON_SCRATCH_SQLITE")
            elif event in ("socket.connect", "socket.bind", "socket.sendto"):
                sock, address = arguments[0], arguments[-1]
                if sock.family not in (socket.AF_INET, socket.AF_INET6):
                    return
                if event == "socket.bind":
                    if address[0] in ("127.0.0.1", "::1") and address[1] == 0:
                        return
                elif event == "socket.connect" and address[0] in ("127.0.0.1", "::1"):
                    listener = listeners.get((sock.family, address[1]))
                    if listener is not None and listener.fileno() >= 0:
                        return
                raise RuntimeError("TEST_BLOCKED_EXTERNAL_NETWORK")
            elif event == "socket.getaddrinfo" and arguments[0] not in (
                "127.0.0.1", "::1", "localhost", None,
            ):
                raise RuntimeError("TEST_BLOCKED_EXTERNAL_NETWORK")

        def ephemeral_bind(sock, address):
            result = original_bind(sock, address)
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                listeners[(sock.family, sock.getsockname()[1])] = sock
            return result

        # Audit hooks also cover sqlite3.dbapi2, connect_ex and cached connects.
        sys.addaudithook(scratch_audit)
        socket.socket.bind = ephemeral_bind
        sys.path.insert(0, str(root))
        import bot

        bot.db_init()
        suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern=args.pattern)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        print(json.dumps({
            "tests": result.testsRun,
            "failures": len(result.failures),
            "errors": len(result.errors),
            "skipped": len(result.skipped),
            "success": result.wasSuccessful(),
            "isolation": "synthetic config; initialized scratch DB/env; own loopback ephemeral servers only",
        }))
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
