"""One-shot CLI for first-time Telethon login (fallback path).

Per goal.md §A2 the primary login flow lives in the web UI. This CLI is
kept for headless / emergency setup. Both paths share the same admin-id
check and 'archive on wrong account' policy via
`lcloud.userbot.session.archive_rejected_session`.

Usage:
    LC_ADMIN_TG_ID=... TG_API_ID=... TG_API_HASH=... python -m lcloud.login
"""

from __future__ import annotations

import asyncio
import contextlib
import getpass
import logging
import sys

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from lcloud.config import Settings, get_settings
from lcloud.userbot.session import archive_rejected_session, session_files

logger = logging.getLogger(__name__)


def _validate_settings(settings: Settings) -> int | None:
    if settings.tg_api_id == 0 or not settings.tg_api_hash:
        print("ERROR: TG_API_ID / TG_API_HASH not configured in .env", file=sys.stderr)
        return 2
    if settings.lc_admin_tg_id == 0:
        print("ERROR: LC_ADMIN_TG_ID not configured in .env", file=sys.stderr)
        return 2
    return None


async def _run() -> int:
    settings = get_settings()
    settings.ensure_runtime_dirs()

    bad = _validate_settings(settings)
    if bad is not None:
        return bad

    client = TelegramClient(
        str(settings.session_path), settings.tg_api_id, settings.tg_api_hash
    )
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            if me is None:
                print("ERROR: get_me() returned None on authorized client", file=sys.stderr)
                return 4
            if me.id != settings.lc_admin_tg_id:
                print(
                    f"REJECTED: existing session id={me.id} != "
                    f"LC_ADMIN_TG_ID={settings.lc_admin_tg_id}",
                    file=sys.stderr,
                )
                with contextlib.suppress(Exception):
                    await client.log_out()
                with contextlib.suppress(Exception):
                    await client.disconnect()
                arch = archive_rejected_session(
                    settings,
                    got_user_id=me.id,
                    expected_user_id=settings.lc_admin_tg_id,
                    note="cli_existing_session_wrong_account",
                )
                print(f"Archived rejected session: {arch}", file=sys.stderr)
                return 3
            print(f"Already authorized as {me.first_name} (id={me.id})")
            return 0

        phone = input("Phone (international format, e.g. +123...): ").strip()
        sent = await client.send_code_request(phone)
        code = input("Code: ").strip()
        try:
            await client.sign_in(
                phone=phone, code=code, phone_code_hash=sent.phone_code_hash
            )
        except SessionPasswordNeededError:
            password = getpass.getpass("2FA password: ")
            await client.sign_in(password=password)

        me = await client.get_me()
        if me is None:
            print("ERROR: get_me() returned None after sign_in", file=sys.stderr)
            return 4
        if me.id != settings.lc_admin_tg_id:
            got = me.id
            print(
                f"REJECTED: logged in id={got} != "
                f"LC_ADMIN_TG_ID={settings.lc_admin_tg_id}",
                file=sys.stderr,
            )
            with contextlib.suppress(Exception):
                await client.log_out()
            with contextlib.suppress(Exception):
                await client.disconnect()
            arch = archive_rejected_session(
                settings,
                got_user_id=got,
                expected_user_id=settings.lc_admin_tg_id,
                note="cli_login_wrong_account",
            )
            print(f"Archived rejected session: {arch}", file=sys.stderr)
            return 3

        # Tighten perms on the freshly-written session file
        for f in session_files(settings):
            if f.exists():
                with contextlib.suppress(OSError):
                    f.chmod(0o600)

        print(f"OK: signed in as {me.first_name} (id={me.id})")
        return 0
    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rc = asyncio.run(_run())
    sys.exit(rc)


if __name__ == "__main__":
    main()
