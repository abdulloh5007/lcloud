"""LCloud configuration loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """All runtime settings, loaded from environment / `.env` at project root."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Telegram API (https://my.telegram.org)
    tg_api_id: int = Field(default=0)
    tg_api_hash: str = Field(default="")

    # The single Telegram user_id allowed to own the userbot session.
    # After web login, server verifies me.id == lc_admin_tg_id and
    # refuses + wipes the session otherwise. 0 = unset / refuse all.
    lc_admin_tg_id: int = Field(default=0)

    # Server
    lc_host: str = Field(default="127.0.0.1")
    lc_port: int = Field(default=8787)
    lc_public_base_url: str = Field(default="http://127.0.0.1:8787")

    # Workers
    lc_max_workers: int = Field(default=10, ge=1, le=64)

    # MTProto rate limit (token bucket applied around Telethon calls).
    # 20 calls/sec is conservative; adjust if you see persistent FloodWaits.
    lc_mtproto_rate_per_sec: float = Field(default=20.0, gt=0.0, le=1000.0)
    lc_mtproto_burst: int = Field(default=20, ge=1, le=200)
    # Maximum FloodWait we will sleep through automatically; longer waits
    # are surfaced as exceptions to the caller.
    lc_mtproto_max_floodwait_sec: int = Field(default=300, ge=1, le=3600)

    # Files
    lc_max_file_bytes: int = Field(default=1024 * 1024 * 1024)  # 1 GiB

    # Auth
    lc_magic_link_ttl_seconds: int = Field(default=900)
    lc_session_ttl_seconds: int = Field(default=7 * 24 * 3600)
    # Set False for plain-http localhost dev (Secure cookies won't be set
    # over http). Flip to True once a real HTTPS deployment exists.
    lc_cookie_secure: bool = Field(default=False)

    # Paths (relative or absolute)
    lc_data_dir: Path = Field(default=Path("data"))
    lc_session_file: Path = Field(default=Path("data/session.lcloud"))
    lc_db_url: str = Field(default="sqlite+aiosqlite:///data/lcloud.db")

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT

    @property
    def data_dir(self) -> Path:
        p = self.lc_data_dir
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def keys_dir(self) -> Path:
        return self.data_dir / "keys"

    @property
    def session_path(self) -> Path:
        p = self.lc_session_file
        return p if p.is_absolute() else PROJECT_ROOT / p

    def ensure_runtime_dirs(self) -> None:
        for d in (self.data_dir, self.keys_dir, self.data_dir / "tmp"):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ admin id

    @property
    def admin_stamp_path(self) -> Path:
        """File where the bootstrap-claimed admin tg_id is stored
        (only used when `lc_admin_tg_id == 0`)."""
        return self.keys_dir / "admin.tgid"

    def effective_admin_tg_id(self) -> int:
        """Resolve the operative admin tg_id.

        Order:
        1. `LC_ADMIN_TG_ID` env value, if non-zero — env always wins.
        2. Stamp file `data/keys/admin.tgid`, written by the first successful
           login when env is zero (bootstrap mode).
        3. Returns 0 if neither is set — meaning "no admin claimed yet, the
           NEXT successful login will become admin".
        """
        if self.lc_admin_tg_id != 0:
            return int(self.lc_admin_tg_id)
        stamp = self.admin_stamp_path
        if not stamp.exists():
            return 0
        try:
            return int(stamp.read_text().strip())
        except (OSError, ValueError):
            return 0

    def claim_admin_tg_id(self, tg_id: int) -> None:
        """Bootstrap: write the admin-id stamp. No-op if env is set."""
        import contextlib

        if self.lc_admin_tg_id != 0 or tg_id == 0:
            return
        self.ensure_runtime_dirs()
        stamp = self.admin_stamp_path
        tmp = stamp.with_suffix(".tgid.tmp")
        tmp.write_text(str(int(tg_id)))
        with contextlib.suppress(OSError):
            tmp.chmod(0o600)
        tmp.replace(stamp)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
