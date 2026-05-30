"""LC1 caption builder/parser for Telegram messages that hold cloud files.

Per goal.md §5, every cloud-file message has a single-line caption:

    LC1:{"o":"<pubkey_b64url>","s":"<sig_b64url>","h":"<sha256_b64url>","t":<ts>}

`o`/`s`/`h` use base64-urlsafe without padding (consistent with the chat
marker in §6); `t` is unix seconds (int).
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any

LC1_PREFIX = "LC1:"


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def build_lc1_caption(
    *,
    sha256_digest: bytes,
    signature: bytes,
    owner_pubkey: bytes,
    uploaded_at_unix: int,
) -> str:
    if len(sha256_digest) != 32:
        raise ValueError("sha256_digest must be 32 bytes")
    if len(signature) != 64:
        raise ValueError("signature must be 64 bytes")
    if len(owner_pubkey) != 32:
        raise ValueError("owner_pubkey must be 32 bytes")
    payload: dict[str, Any] = {
        "o": _b64url_encode(owner_pubkey),
        "s": _b64url_encode(signature),
        "h": _b64url_encode(sha256_digest),
        "t": int(uploaded_at_unix),
    }
    return LC1_PREFIX + json.dumps(payload, separators=(",", ":"))


@dataclass(frozen=True)
class ParsedLC1:
    sha256_digest: bytes
    signature: bytes
    owner_pubkey: bytes
    uploaded_at_unix: int


def parse_lc1_caption(caption: str | None) -> ParsedLC1 | None:
    """Parse a caption; return None on any malformed input. Tolerant to a
    trailing newline / whitespace from Telegram clients."""
    if not caption:
        return None
    s = caption.strip()
    if not s.startswith(LC1_PREFIX):
        return None
    body = s[len(LC1_PREFIX) :]
    try:
        obj = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    try:
        h = _b64url_decode(obj["h"])
        sig = _b64url_decode(obj["s"])
        pub = _b64url_decode(obj["o"])
        ts = int(obj["t"])
    except (KeyError, TypeError, ValueError, binascii.Error):
        return None
    if len(h) != 32 or len(sig) != 64 or len(pub) != 32:
        return None
    return ParsedLC1(
        sha256_digest=h,
        signature=sig,
        owner_pubkey=pub,
        uploaded_at_unix=ts,
    )
