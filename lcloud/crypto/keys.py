"""Ed25519 keypair management for the admin owner.

Per goal.md §5 the admin holds an Ed25519 keypair generated on first run.
- `data/keys/admin.key` — 32-byte raw private seed, mode 600
- `data/keys/admin.pub` — 32-byte raw public key, mode 644

Both files are written atomically with `O_EXCL` and an explicit `fchmod`
to avoid permission-leak windows.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

from nacl.signing import SigningKey, VerifyKey

from lcloud.config import Settings, get_settings


def _atomic_write_bytes(path: Path, data: bytes, mode: int) -> None:
    """Create `path` exclusively, set exact mode, write data."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    success = False
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        success = True
    finally:
        if not success:
            with contextlib.suppress(FileNotFoundError):
                path.unlink()


def admin_key_paths(settings: Settings | None = None) -> tuple[Path, Path]:
    s = settings or get_settings()
    s.ensure_runtime_dirs()
    return (s.keys_dir / "admin.key", s.keys_dir / "admin.pub")


def ensure_admin_keypair(
    settings: Settings | None = None,
) -> tuple[SigningKey, VerifyKey]:
    """Load the admin keypair from disk; create it if absent.

    Returns ``(signing_key, verify_key)``. Raises ``RuntimeError`` if only one
    of the two key files is present (refuses to silently regenerate).
    """
    s = settings or get_settings()
    priv_path, pub_path = admin_key_paths(s)

    if priv_path.exists() and pub_path.exists():
        sk = SigningKey(priv_path.read_bytes())
        vk = VerifyKey(pub_path.read_bytes())
        return sk, vk

    if priv_path.exists() != pub_path.exists():
        raise RuntimeError(
            f"Inconsistent keystore: {priv_path}.exists()={priv_path.exists()} "
            f"{pub_path}.exists()={pub_path.exists()}. "
            "Refusing to fix automatically — investigate manually."
        )

    sk = SigningKey.generate()
    vk = sk.verify_key
    _atomic_write_bytes(priv_path, bytes(sk), mode=0o600)
    _atomic_write_bytes(pub_path, bytes(vk), mode=0o644)
    return sk, vk
