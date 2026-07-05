"""Telethon userbot lifecycle + web login state machine.

Single Telethon `TelegramClient` per app. `UserbotManager` owns:
- the client
- the in-progress login flow (`_Flow`), if any
- transitions between NO_SESSION ↔ CODE_SENT ↔ PWD_NEEDED ↔ AUTHORIZED

Wrong-account rejection (`me.id != LC_ADMIN_TG_ID`) calls
`archive_rejected_session(...)` per goal.md §A2 step 5a, log-out happens
before archive so the auth_key is invalidated at Telegram first.

All public coroutines that touch state are serialized by `_lock`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from lcloud.config import Settings, get_settings
from lcloud.userbot.session import archive_rejected_session, session_files

if TYPE_CHECKING:
    from telethon.tl.custom import Message  # noqa: F401

logger = logging.getLogger(__name__)


class WrongAccountError(Exception):
    """Logged-in user_id does not match LC_ADMIN_TG_ID."""

    def __init__(self, got: int, expected: int) -> None:
        super().__init__(f"logged in id={got} but LC_ADMIN_TG_ID={expected}")
        self.got = got
        self.expected = expected


class LoginAlreadyAuthorizedError(Exception):
    pass


class NoActiveFlowError(Exception):
    pass


class FlowAlreadyActiveError(Exception):
    pass


class UserbotNotConfiguredError(Exception):
    pass


class LoginFlowState(str, Enum):
    NO_SESSION = "no_session"
    CODE_SENT = "code_sent"
    PWD_NEEDED = "pwd_needed"
    AUTHORIZED = "authorized"


@dataclass
class _Flow:
    phone: str
    phone_code_hash: str
    started_at: float
    needs_password: bool = False


@dataclass(frozen=True)
class AuthSnapshot:
    authorized: bool
    state: LoginFlowState
    me_id: int | None = None
    me_first_name: str | None = None
    me_username: str | None = None


class UserbotManager:
    """Owns the Telethon TelegramClient + in-progress login flow."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: TelegramClient | None = None
        self._flow: _Flow | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ lifecycle

    def _build_client(self) -> TelegramClient:
        return TelegramClient(
            str(self._settings.session_path),
            self._settings.tg_api_id,
            self._settings.tg_api_hash,
        )

    def _persist_session(self) -> None:
        """Force Telethon's SQLite session to disk after successful auth."""
        if self._client is None:
            return
        with contextlib.suppress(Exception):
            self._client.session.save()
        for path in session_files(self._settings):
            if path.exists():
                with contextlib.suppress(OSError):
                    path.chmod(0o600)

    async def start(self) -> None:
        """Connect Telethon. Skips if API creds are unset (degraded mode)."""
        async with self._lock:
            if self._client is not None:
                return
            if self._settings.tg_api_id == 0 or not self._settings.tg_api_hash:
                logger.warning(
                    "TG_API_ID / TG_API_HASH not configured; userbot disabled"
                )
                return
            client = self._build_client()
            await client.connect()
            self._client = client
            authorized = await client.is_user_authorized()
            logger.info("Telethon connected; authorized=%s", authorized)

    async def stop(self) -> None:
        async with self._lock:
            if self._client is None:
                return
            with contextlib.suppress(Exception):
                await self._client.disconnect()
            self._client = None
            self._flow = None

    @property
    def is_started(self) -> bool:
        return self._client is not None

    @property
    def client(self) -> TelegramClient:
        """Return the Telethon client; raises if `start()` was not called."""
        if self._client is None:
            raise UserbotNotConfiguredError("userbot manager not started")
        return self._client

    # ------------------------------------------------------------------ snapshot

    async def snapshot(self) -> AuthSnapshot:
        if self._client is None:
            return AuthSnapshot(authorized=False, state=LoginFlowState.NO_SESSION)
        if await self._client.is_user_authorized():
            me = await self._client.get_me()
            if me is None:
                return AuthSnapshot(authorized=False, state=LoginFlowState.NO_SESSION)
            return AuthSnapshot(
                authorized=True,
                state=LoginFlowState.AUTHORIZED,
                me_id=me.id,
                me_first_name=getattr(me, "first_name", None),
                me_username=getattr(me, "username", None),
            )
        if self._flow is None:
            return AuthSnapshot(authorized=False, state=LoginFlowState.NO_SESSION)
        if self._flow.needs_password:
            return AuthSnapshot(authorized=False, state=LoginFlowState.PWD_NEEDED)
        return AuthSnapshot(authorized=False, state=LoginFlowState.CODE_SENT)

    async def is_admin_authorized(self) -> bool:
        snap = await self.snapshot()
        return (
            snap.authorized
            and snap.me_id is not None
            and snap.me_id == self._settings.effective_admin_tg_id()
        )

    # ------------------------------------------------------------------ flow ops

    async def start_login(self, phone: str) -> None:
        async with self._lock:
            if self._client is None:
                raise UserbotNotConfiguredError()
            if await self._client.is_user_authorized():
                raise LoginAlreadyAuthorizedError()
            if self._flow is not None:
                raise FlowAlreadyActiveError()
            sent = await self._client.send_code_request(phone)
            self._flow = _Flow(
                phone=phone,
                phone_code_hash=sent.phone_code_hash,
                started_at=time.time(),
            )
            logger.info("login flow started for phone=%s***", phone[:4])

    async def submit_code(self, code: str) -> AuthSnapshot:
        async with self._lock:
            if self._flow is None:
                raise NoActiveFlowError()
            assert self._client is not None
            try:
                await self._client.sign_in(
                    phone=self._flow.phone,
                    code=code,
                    phone_code_hash=self._flow.phone_code_hash,
                )
            except SessionPasswordNeededError:
                self._flow.needs_password = True
                return AuthSnapshot(
                    authorized=False, state=LoginFlowState.PWD_NEEDED
                )
            return await self._finalize_login()

    async def submit_password(self, password: str) -> AuthSnapshot:
        async with self._lock:
            if self._flow is None or not self._flow.needs_password:
                raise NoActiveFlowError()
            assert self._client is not None
            await self._client.sign_in(password=password)
            return await self._finalize_login()

    async def cancel_flow(self) -> None:
        async with self._lock:
            self._flow = None

    # ------------------------------------------------------------------ helpers

    async def _finalize_login(self) -> AuthSnapshot:
        """Inside _lock, sign_in succeeded — verify admin id; archive if mismatch.

        Bootstrap rule: if `effective_admin_tg_id()` is 0 (env unset AND no
        stamp file yet), the FIRST login wins — we claim that user as admin
        and persist it. Subsequent logins must match.
        """
        assert self._client is not None
        me = await self._client.get_me()
        got_id = me.id if me is not None else 0
        expected = self._settings.effective_admin_tg_id()

        if me is None or got_id == 0:
            await self._reject_session(got_user_id=got_id)
            raise WrongAccountError(got=got_id, expected=expected)

        if expected == 0:
            # Bootstrap: claim this user as admin
            self._settings.claim_admin_tg_id(got_id)
            expected = got_id
            logger.info("bootstrap: claimed admin tg_id=%s", got_id)

        if got_id != expected:
            await self._reject_session(got_user_id=got_id)
            raise WrongAccountError(got=got_id, expected=expected)

        self._persist_session()
        self._flow = None
        return AuthSnapshot(
            authorized=True,
            state=LoginFlowState.AUTHORIZED,
            me_id=me.id,
            me_first_name=getattr(me, "first_name", None),
            me_username=getattr(me, "username", None),
        )

    async def _reject_session(self, *, got_user_id: int) -> None:
        """Log out at Telegram, archive local session files + metadata, reconnect."""
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.log_out()
            with contextlib.suppress(Exception):
                await self._client.disconnect()
            self._client = None
        archived = archive_rejected_session(
            self._settings,
            got_user_id=got_user_id,
            expected_user_id=self._settings.effective_admin_tg_id(),
            note="wrong_account",
        )
        logger.warning(
            "wrong-account login rejected; archived %d files: %s",
            len(archived),
            [str(p) for p in archived],
        )
        # Reconnect with a fresh empty session for the next attempt
        client = self._build_client()
        await client.connect()
        self._client = client
        self._flow = None


# Module-level singleton; init in lifespan.
_manager: UserbotManager | None = None


def get_userbot_manager() -> UserbotManager:
    global _manager
    if _manager is None:
        _manager = UserbotManager()
    return _manager


def set_userbot_manager(manager: UserbotManager | None) -> None:
    """Tests / fixtures: replace the singleton with a custom (or mock) manager."""
    global _manager
    _manager = manager
