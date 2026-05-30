"""Tests for UserbotManager state machine + WrongAccount archive flow.

Uses a hand-rolled fake TelegramClient (no real telethon network calls).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from telethon.errors import SessionPasswordNeededError

from lcloud.config import Settings
from lcloud.userbot.client import (
    FlowAlreadyActiveError,
    LoginAlreadyAuthorizedError,
    LoginFlowState,
    NoActiveFlowError,
    UserbotManager,
    WrongAccountError,
)


class FakeUser:
    def __init__(
        self, *, id: int, first_name: str = "Tester", username: str | None = None
    ) -> None:
        self.id = id
        self.first_name = first_name
        self.username = username


class FakeSentCode:
    def __init__(self, phone: str) -> None:
        self.phone_code_hash = f"hash_{phone}"


class FakeTelegramClient:
    """Minimal stand-in for telethon.TelegramClient covering what UserbotManager uses."""

    def __init__(
        self,
        *,
        me_id: int | None = None,
        first_name: str = "Tester",
        require_2fa: bool = False,
    ) -> None:
        self._connected = False
        self._authorized = False
        self._me: FakeUser | None = (
            FakeUser(id=me_id, first_name=first_name) if me_id is not None else None
        )
        self._require_2fa = require_2fa
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def connect(self) -> None:
        self._connected = True
        self.calls.append(("connect", {}))

    async def disconnect(self) -> None:
        self._connected = False
        self.calls.append(("disconnect", {}))

    async def is_user_authorized(self) -> bool:
        return self._authorized

    async def get_me(self) -> FakeUser | None:
        return self._me

    async def send_code_request(self, phone: str) -> FakeSentCode:
        self.calls.append(("send_code_request", {"phone": phone}))
        return FakeSentCode(phone)

    async def sign_in(
        self,
        phone: str | None = None,
        code: str | None = None,
        phone_code_hash: str | None = None,
        password: str | None = None,
    ) -> None:
        self.calls.append(
            (
                "sign_in",
                {
                    "phone": phone,
                    "code": code,
                    "phone_code_hash": phone_code_hash,
                    "has_password": password is not None,
                },
            )
        )
        if password is not None:
            self._authorized = True
            return
        if self._require_2fa:
            self._require_2fa = False  # only first call raises
            raise SessionPasswordNeededError(request=None)
        self._authorized = True

    async def log_out(self) -> None:
        self._authorized = False
        self._connected = False
        self.calls.append(("log_out", {}))


@pytest.fixture
def admin_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        lc_data_dir=tmp_path,
        tg_api_id=1,
        tg_api_hash="testhash",
        lc_admin_tg_id=42,
    )


def _patch_client_factory(
    monkeypatch: pytest.MonkeyPatch,
    mgr: UserbotManager,
    factory: Callable[[], FakeTelegramClient],
) -> None:
    # cast: UserbotManager._build_client returns TelegramClient, but our fake
    # is structurally compatible with the methods UserbotManager calls.
    monkeypatch.setattr(mgr, "_build_client", factory)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_happy_path_no_2fa(
    admin_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeTelegramClient(me_id=42)
    mgr = UserbotManager(admin_settings)
    _patch_client_factory(monkeypatch, mgr, lambda: fake)

    await mgr.start()
    snap = await mgr.snapshot()
    assert snap.state == LoginFlowState.NO_SESSION

    await mgr.start_login("+1234567")
    snap = await mgr.snapshot()
    assert snap.state == LoginFlowState.CODE_SENT

    snap = await mgr.submit_code("11111")
    assert snap.authorized
    assert snap.state == LoginFlowState.AUTHORIZED
    assert snap.me_id == 42


@pytest.mark.asyncio
async def test_2fa_path(
    admin_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeTelegramClient(me_id=42, require_2fa=True)
    mgr = UserbotManager(admin_settings)
    _patch_client_factory(monkeypatch, mgr, lambda: fake)

    await mgr.start()
    await mgr.start_login("+1234567")
    snap = await mgr.submit_code("11111")
    assert snap.state == LoginFlowState.PWD_NEEDED
    assert not snap.authorized

    snap = await mgr.submit_password("hunter2")
    assert snap.authorized
    assert snap.state == LoginFlowState.AUTHORIZED


@pytest.mark.asyncio
async def test_wrong_account_archives_and_resets(
    admin_settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fakes: list[FakeTelegramClient] = []

    def factory() -> FakeTelegramClient:
        fake = FakeTelegramClient(me_id=999)  # NOT 42 (the admin)
        fakes.append(fake)
        return fake

    mgr = UserbotManager(admin_settings)
    _patch_client_factory(monkeypatch, mgr, factory)

    await mgr.start()
    await mgr.start_login("+9999")
    with pytest.raises(WrongAccountError) as exc:
        await mgr.submit_code("11111")
    assert exc.value.got == 999
    assert exc.value.expected == 42

    # archive sidecar (.json) must exist regardless of session-file presence
    sidecars = list(tmp_path.glob("session.rejected.*.json"))
    assert len(sidecars) == 1
    import json

    meta = json.loads(sidecars[0].read_text())
    assert meta["got_user_id"] == 999
    assert meta["expected_user_id"] == 42
    assert meta["note"] == "wrong_account"

    # log_out was called on the rejected client; a fresh client took its place
    assert fakes[0].calls and any(c[0] == "log_out" for c in fakes[0].calls)
    assert len(fakes) >= 2  # at least one new client built after rejection

    # After rejection: a new flow can be started again
    snap = await mgr.snapshot()
    assert snap.state == LoginFlowState.NO_SESSION


@pytest.mark.asyncio
async def test_double_start_rejected(
    admin_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeTelegramClient(me_id=42)
    mgr = UserbotManager(admin_settings)
    _patch_client_factory(monkeypatch, mgr, lambda: fake)

    await mgr.start()
    await mgr.start_login("+1")
    with pytest.raises(FlowAlreadyActiveError):
        await mgr.start_login("+2")


@pytest.mark.asyncio
async def test_start_when_already_authorized(
    admin_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeTelegramClient(me_id=42)
    fake._authorized = True  # pre-authorized
    mgr = UserbotManager(admin_settings)
    _patch_client_factory(monkeypatch, mgr, lambda: fake)

    await mgr.start()
    with pytest.raises(LoginAlreadyAuthorizedError):
        await mgr.start_login("+1")


@pytest.mark.asyncio
async def test_submit_without_flow(
    admin_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeTelegramClient(me_id=42)
    mgr = UserbotManager(admin_settings)
    _patch_client_factory(monkeypatch, mgr, lambda: fake)
    await mgr.start()

    with pytest.raises(NoActiveFlowError):
        await mgr.submit_code("xxx")
    with pytest.raises(NoActiveFlowError):
        await mgr.submit_password("xxx")


@pytest.mark.asyncio
async def test_cancel_resets_flow(
    admin_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeTelegramClient(me_id=42)
    mgr = UserbotManager(admin_settings)
    _patch_client_factory(monkeypatch, mgr, lambda: fake)
    await mgr.start()

    await mgr.start_login("+1")
    snap = await mgr.snapshot()
    assert snap.state == LoginFlowState.CODE_SENT

    await mgr.cancel_flow()
    snap = await mgr.snapshot()
    assert snap.state == LoginFlowState.NO_SESSION


@pytest.mark.asyncio
async def test_bootstrap_mode_claims_first_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When LC_ADMIN_TG_ID=0 the first successful login is auto-claimed and
    the user_id stamped to data/keys/admin.tgid."""
    s = Settings(
        _env_file=None,
        lc_data_dir=tmp_path,
        tg_api_id=1,
        tg_api_hash="testhash",
        lc_admin_tg_id=0,  # bootstrap mode
    )
    fake = FakeTelegramClient(me_id=12345)
    mgr = UserbotManager(s)
    _patch_client_factory(monkeypatch, mgr, lambda: fake)

    await mgr.start()
    await mgr.start_login("+1111111")
    snap = await mgr.submit_code("11111")
    assert snap.authorized
    assert snap.me_id == 12345

    # Stamp file written
    stamp = tmp_path / "keys" / "admin.tgid"
    assert stamp.exists()
    assert stamp.read_text().strip() == "12345"

    # subsequent settings.effective_admin_tg_id() should now return 12345
    assert s.effective_admin_tg_id() == 12345


@pytest.mark.asyncio
async def test_env_admin_id_overrides_stamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even if a stamp file exists, a non-zero env LC_ADMIN_TG_ID wins."""
    # Pre-seed a stamp
    keys = tmp_path / "keys"
    keys.mkdir(parents=True)
    (keys / "admin.tgid").write_text("99999")

    s = Settings(
        _env_file=None,
        lc_data_dir=tmp_path,
        tg_api_id=1,
        tg_api_hash="testhash",
        lc_admin_tg_id=42,  # env wins
    )
    fake = FakeTelegramClient(me_id=99999)  # would match stamp but not env
    mgr = UserbotManager(s)
    _patch_client_factory(monkeypatch, mgr, lambda: fake)

    await mgr.start()
    await mgr.start_login("+1111111")
    with pytest.raises(WrongAccountError) as exc:
        await mgr.submit_code("11111")
    assert exc.value.expected == 42
    assert exc.value.got == 99999


@pytest.mark.asyncio
async def test_degraded_mode_when_creds_missing(tmp_path: Path) -> None:
    s = Settings(
        _env_file=None,
        lc_data_dir=tmp_path,
        tg_api_id=0,
        tg_api_hash="",
        lc_admin_tg_id=42,
    )
    mgr = UserbotManager(s)
    await mgr.start()  # must not raise; just logs and skips
    assert not mgr.is_started
    snap = await mgr.snapshot()
    assert snap.state == LoginFlowState.NO_SESSION
