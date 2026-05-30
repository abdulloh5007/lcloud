"""LC2 caption format — client-signed file ownership marker.

Format (JSON, with `LC2:` prefix to distinguish from V1 LC1):

    LC2:{"o":"<pub_hex>","h":"<sha256_hex>","s":"<sig_hex>","t":<unix_ts>}

The signature is over a canonical payload:

    payload = sha256_bytes (32) || ts (8 bytes, big-endian) || pubkey_bytes (32)

verification:
    Ed25519.verify(pubkey, signature, payload)

Why this layout (instead of message_id like V1 LC1):
- The client doesn't know `message_id` before upload — TG assigns it.
- We bind the signature to the file content (sha256), the moment in time
  it was uploaded (ts), and the owner identity (pubkey). That's enough to
  prove "this user uploaded this exact bytes at this time" without a
  network round-trip post-upload.
- Anyone with the file + the caption + the pubkey can verify integrity
  and ownership offline.

Total caption length: ~330 chars, well under TG's 1024-char caption cap.
"""

from __future__ import annotations

import json
import struct
import time
from dataclasses import dataclass

from lcloud.auth.seed import verify_signature

LC2_PREFIX = "LC2:"

# Allowed clock skew between client `ts` and server time, in seconds.
# Wide enough for chronically wrong clocks; tight enough to thwart replay.
TS_SKEW_SECONDS = 24 * 3600  # 24 h


@dataclass(frozen=True)
class Lc2Payload:
    pubkey: bytes  # 32 B
    sha256: bytes  # 32 B
    signature: bytes  # 64 B
    ts: int

    def to_caption(self) -> str:
        body = {
            "o": self.pubkey.hex(),
            "h": self.sha256.hex(),
            "s": self.signature.hex(),
            "t": int(self.ts),
        }
        return f"{LC2_PREFIX}{json.dumps(body, separators=(',', ':'))}"


def canonical_payload(*, sha256: bytes, ts: int, pubkey: bytes) -> bytes:
    """Bytes that the client signs and the server verifies."""
    if len(sha256) != 32:
        raise ValueError("sha256 must be 32 bytes")
    if len(pubkey) != 32:
        raise ValueError("pubkey must be 32 bytes")
    return sha256 + struct.pack(">Q", int(ts)) + pubkey


def verify_lc2_payload(
    *,
    pubkey: bytes,
    sha256: bytes,
    signature: bytes,
    ts: int,
    server_now: int | None = None,
) -> tuple[bool, str | None]:
    """Verify (sha256, ts, pubkey, signature). Returns (ok, reason_if_bad).

    Checks:
      1. Sizes (32 / 32 / 64 / int)
      2. Timestamp within `±TS_SKEW_SECONDS` of server time
      3. Ed25519 signature is valid for canonical payload
    """
    if len(pubkey) != 32:
        return False, "bad_pubkey_len"
    if len(sha256) != 32:
        return False, "bad_sha256_len"
    if len(signature) != 64:
        return False, "bad_signature_len"
    if ts <= 0:
        return False, "bad_ts"

    now = server_now if server_now is not None else int(time.time())
    if abs(now - int(ts)) > TS_SKEW_SECONDS:
        return False, "ts_skew"

    payload = canonical_payload(sha256=sha256, ts=ts, pubkey=pubkey)
    if not verify_signature(pubkey, payload, signature):
        return False, "bad_signature"

    return True, None


def parse_caption(caption: str) -> Lc2Payload | None:
    """Parse an LC2 caption back into structured form. Returns None if malformed."""
    if not caption or not caption.startswith(LC2_PREFIX):
        return None
    try:
        body = json.loads(caption[len(LC2_PREFIX):])
        return Lc2Payload(
            pubkey=bytes.fromhex(body["o"]),
            sha256=bytes.fromhex(body["h"]),
            signature=bytes.fromhex(body["s"]),
            ts=int(body["t"]),
        )
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return None


__all__ = [
    "LC2_PREFIX",
    "TS_SKEW_SECONDS",
    "Lc2Payload",
    "canonical_payload",
    "parse_caption",
    "verify_lc2_payload",
]
