"""Tests for upload_file_to_cloud + iter_download_file + delete_file_message
using a hand-rolled fake TelegramClient (no real Telethon network calls)."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from nacl.signing import SigningKey

from lcloud.crypto.sign import file_signature_payload, verify
from lcloud.userbot.files import (
    delete_file_message,
    iter_download_file,
    upload_file_to_cloud,
)
from lcloud.userbot.lc1 import parse_lc1_caption


@dataclass
class FakeMessage:
    id: int
    media: object | None = field(default_factory=object)


@dataclass
class FakeEntity:
    id: int


class FakeTGClient:
    """Just enough Telethon surface for files.py."""

    def __init__(
        self,
        *,
        next_message_id: int = 100,
        download_chunks: list[bytes] | None = None,
    ) -> None:
        self.next_message_id = next_message_id
        self.captions: dict[int, str] = {}  # message_id -> caption
        self.send_calls: list[dict[str, Any]] = []
        self.delete_calls: list[tuple[int, list[int]]] = []
        self._chunks = download_chunks or []
        self._messages: dict[int, FakeMessage] = {}

    async def get_entity(self, chat_id: int) -> FakeEntity:
        return FakeEntity(id=chat_id)

    async def send_file(
        self, entity: FakeEntity, *, file: str, **kwargs: Any
    ) -> FakeMessage:
        self.send_calls.append({"chat_id": entity.id, "file": file, **kwargs})
        msg = FakeMessage(id=self.next_message_id)
        self._messages[msg.id] = msg
        self.next_message_id += 1
        return msg

    async def edit_message(
        self, entity: FakeEntity, message_id: int, caption: str
    ) -> None:
        self.captions[message_id] = caption

    async def get_messages(
        self, entity: FakeEntity, *, ids: int
    ) -> FakeMessage | None:
        return self._messages.get(ids)

    def iter_download(
        self, message: FakeMessage, *, chunk_size: int = 0
    ) -> AsyncIterator[bytes]:
        async def _gen() -> AsyncIterator[bytes]:
            for c in self._chunks:
                yield c

        return _gen()

    async def delete_messages(
        self, entity: FakeEntity, message_ids: list[int]
    ) -> None:
        self.delete_calls.append((entity.id, list(message_ids)))


@pytest.mark.asyncio
async def test_upload_writes_caption_with_valid_signature(
    tmp_path: Path,
) -> None:
    f = tmp_path / "blob.bin"
    data = b"hello-cloud-world\n" * 1000
    f.write_bytes(data)
    sha = hashlib.sha256(data).digest()

    sk = SigningKey.generate()
    pub = bytes(sk.verify_key)
    chat_id = -1_001_111_111_111

    client = FakeTGClient(next_message_id=42)
    result = await upload_file_to_cloud(
        client,  # type: ignore[arg-type]
        chat_id=chat_id,
        file_path=f,
        original_name="blob.bin",
        sha256_digest=sha,
        signing_key=sk,
    )

    assert result.message_id == 42
    assert client.captions[42].startswith("LC1:")
    parsed = parse_lc1_caption(client.captions[42])
    assert parsed is not None
    assert parsed.sha256_digest == sha
    assert parsed.owner_pubkey == pub

    # Signature must verify against the canonical payload
    payload = file_signature_payload(
        sha256_digest=sha,
        chat_id=chat_id,
        message_id=42,
        owner_pubkey=pub,
        uploaded_at_unix=parsed.uploaded_at_unix,
    )
    assert verify(sk.verify_key, parsed.signature, payload)


@pytest.mark.asyncio
async def test_iter_download_yields_chunks() -> None:
    chunks = [b"part1-", b"part2-", b"part3"]
    client = FakeTGClient(download_chunks=chunks)
    # Pre-seed a message so get_messages returns it
    msg = FakeMessage(id=42)
    client._messages[42] = msg

    out = []
    async for c in iter_download_file(
        client,  # type: ignore[arg-type]
        chat_id=-1,
        message_id=42,
    ):
        out.append(c)
    assert out == chunks


@pytest.mark.asyncio
async def test_iter_download_raises_when_message_missing() -> None:
    client = FakeTGClient()
    with pytest.raises(FileNotFoundError):
        async for _ in iter_download_file(
            client,  # type: ignore[arg-type]
            chat_id=-1,
            message_id=99,
        ):
            pass


@pytest.mark.asyncio
async def test_delete_calls_telegram() -> None:
    client = FakeTGClient()
    await delete_file_message(client, chat_id=-1, message_id=42)  # type: ignore[arg-type]
    assert client.delete_calls == [(-1, [42])]
