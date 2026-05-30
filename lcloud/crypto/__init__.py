"""Crypto layer: Ed25519 keypair management, sign/verify, file hashing."""

from lcloud.crypto.keys import admin_key_paths, ensure_admin_keypair
from lcloud.crypto.sign import (
    file_signature_payload,
    sha256_file,
    sign,
    verify,
)

__all__ = [
    "admin_key_paths",
    "ensure_admin_keypair",
    "file_signature_payload",
    "sha256_file",
    "sign",
    "verify",
]
