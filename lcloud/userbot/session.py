"""Telethon session file management: paths + archive + metadata sidecar."""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path

from lcloud.config import Settings, get_settings


def session_files(settings: Settings | None = None) -> list[Path]:
    """All on-disk files Telethon may create for the configured session base."""
    s = settings or get_settings()
    base = s.session_path
    return [
        Path(str(base) + ".session"),
        Path(str(base) + ".session-journal"),
    ]


def has_session_files(settings: Settings | None = None) -> bool:
    return any(f.exists() for f in session_files(settings))


def archive_rejected_session(
    settings: Settings | None = None,
    *,
    got_user_id: int | None = None,
    expected_user_id: int | None = None,
    note: str | None = None,
) -> list[Path]:
    """Rename Telethon session files to `session.rejected.<ts>...` (mode 600)
    and write a metadata JSON sidecar capturing why the session was rejected.

    Returns the list of resulting on-disk paths (archived files + sidecar).
    """
    s = settings or get_settings()
    ts = int(time.time())
    paths: list[Path] = []
    for f in session_files(s):
        if not f.exists():
            continue
        new_name = f.name.replace("session.lcloud", f"session.rejected.{ts}", 1)
        target = f.with_name(new_name)
        f.rename(target)
        with contextlib.suppress(OSError):
            target.chmod(0o600)
        paths.append(target)

    sidecar = s.data_dir / f"session.rejected.{ts}.json"
    meta = {
        "ts": ts,
        "got_user_id": got_user_id,
        "expected_user_id": expected_user_id,
        "note": note,
    }
    sidecar.write_text(json.dumps(meta, indent=2, sort_keys=True))
    with contextlib.suppress(OSError):
        sidecar.chmod(0o600)
    paths.append(sidecar)
    return paths
