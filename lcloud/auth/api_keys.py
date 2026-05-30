"""API key minting + verification utilities.

Key format:
    lck_<43 base64url chars>     ≈ 32 bytes of CSPRNG entropy

Storage:
    api_keys.prefix    — first 12 chars of the raw key (e.g. "lck_AbCdEfGh"),
                          indexed, used to short-list candidates on lookup.
    api_keys.hash      — argon2id of the full raw key. Slow on purpose.

The raw key is shown to the user **exactly once** on creation; the server
keeps only the hash. There is no recovery path — if the user loses the
key they revoke it and mint a new one.
"""

from __future__ import annotations

import base64
import logging
import secrets
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2 import exceptions as argon2_exc

logger = logging.getLogger(__name__)

KEY_PREFIX = "lck_"
PREFIX_LEN = 12  # KEY_PREFIX + 8 entropy chars
RAW_ENTROPY_BYTES = 32

# argon2id with sane defaults (RFC 9106 second recommended profile).
# These values land at ~30 ms per verify on modern hardware — fast enough
# for an inline auth check, slow enough to make brute force expensive.
_ph = PasswordHasher(
    time_cost=2,
    memory_cost=64 * 1024,  # 64 MiB
    parallelism=2,
    hash_len=32,
)


@dataclass(frozen=True)
class MintedKey:
    raw: str  # full key, shown to the user once
    prefix: str  # for storage (indexed)
    hash: str  # argon2id, for storage


def mint_key() -> MintedKey:
    """Generate a new random API key. Caller must persist `prefix`+`hash`
    and return `raw` to the user immediately (it cannot be recovered)."""
    entropy = secrets.token_bytes(RAW_ENTROPY_BYTES)
    body = base64.urlsafe_b64encode(entropy).decode("ascii").rstrip("=")
    raw = f"{KEY_PREFIX}{body}"
    prefix = raw[:PREFIX_LEN]
    h = _ph.hash(raw)
    return MintedKey(raw=raw, prefix=prefix, hash=h)


def extract_prefix(raw: str) -> str:
    """Return the first `PREFIX_LEN` chars of a candidate key, or '' if too short."""
    if not raw or len(raw) < PREFIX_LEN:
        return ""
    return raw[:PREFIX_LEN]


def verify(raw: str, stored_hash: str) -> bool:
    """argon2 verify. Returns False on any error (mismatch, malformed hash)."""
    try:
        return _ph.verify(stored_hash, raw)
    except (
        argon2_exc.VerifyMismatchError,
        argon2_exc.InvalidHashError,
        argon2_exc.VerificationError,
    ):
        return False


def looks_like_api_key(raw: str | None) -> bool:
    if not raw:
        return False
    return raw.startswith(KEY_PREFIX) and len(raw) >= PREFIX_LEN + 4


__all__ = [
    "KEY_PREFIX",
    "PREFIX_LEN",
    "MintedKey",
    "extract_prefix",
    "looks_like_api_key",
    "mint_key",
    "verify",
]
