"""V2: bootstrap the admin user and deliver their BIP39 seed via Telegram.

Runs once per LCloud install: when the userbot becomes admin-authorized AND
the `users` table has no row with `role='admin'`, this module:

1. Generates a fresh 12-word BIP39 mnemonic.
2. Derives the corresponding Ed25519 keypair (privkey forgotten immediately).
3. Inserts a `User(role='admin', pubkey=<derived>)` row.
4. Sends the seed phrase to the admin's Saved Messages with a strong warning.

The seed phrase is the **only** way the admin can log into the web UI
afterwards. We never persist it to disk; if the admin loses the message
before saving the words, they must reset by deleting the admin user row.

Note: This auth keypair is **separate** from `data/keys/admin.key`, which
remains in place to verify legacy V1 file signatures.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from lcloud.auth.seed import derive_keypair, generate_mnemonic
from lcloud.db.models import User

logger = logging.getLogger(__name__)


SAVED_MESSAGES_PEER = "me"

_SEED_MESSAGE_TEMPLATE = """🔐 LCloud — ваша seed-фраза администратора

Сохраните эти 12 слов В БЕЗОПАСНОМ МЕСТЕ. Это единственный способ войти в веб-админку LCloud.

```
{mnemonic}
```

⚠️ ВАЖНО:
• Никому не показывайте эти слова. Кто их получит — тот станет администратором.
• Telegram хранит это сообщение пока вы его не удалите.
• Если потеряете — придётся пересоздавать аккаунт админа (старые файлы сохранятся).

Войти можно тут: {public_url}

После того как сохраните — рекомендуем удалить это сообщение."""


class _SavedMessagesClient(Protocol):
    async def send_message(self, peer: str, text: str, **kwargs: object) -> object: ...


async def ensure_admin_seed_delivered(
    *,
    client: _SavedMessagesClient,
    sessionmaker: async_sessionmaker[Any],
    public_base_url: str,
) -> bool:
    """Idempotent: returns True iff a fresh admin seed was generated and sent.

    Returns False if an admin user already exists (no-op).
    """
    async with sessionmaker() as sess:
        existing = (
            await sess.execute(
                sa.select(User).where(User.role == "admin").limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            logger.debug(
                "admin user already exists (id=%d, pubkey=%s...); skipping seed delivery",
                existing.id,
                existing.pubkey.hex()[:16],
            )
            return False

        # Fresh seed → keypair → user row
        mnemonic = generate_mnemonic(words=12)
        ident = derive_keypair(mnemonic)
        user = User(
            pubkey=ident.pubkey,
            role="admin",
            label="admin",
            # Admin gets a much higher quota than the default per-user 5 GiB.
            storage_quota_bytes=1024 * 1024 * 1024 * 1024,  # 1 TiB
        )
        sess.add(user)
        await sess.commit()
        await sess.refresh(user)
        logger.info(
            "created admin user id=%d pubkey=%s...",
            user.id,
            user.pubkey.hex()[:16],
        )

    # Send to Saved Messages OUTSIDE the DB session (Telethon I/O can be slow).
    text = _SEED_MESSAGE_TEMPLATE.format(
        mnemonic=mnemonic, public_url=public_base_url
    )
    try:
        await client.send_message(SAVED_MESSAGES_PEER, text)
        logger.info("delivered admin seed phrase to Saved Messages")
    except Exception:
        logger.exception(
            "failed to deliver admin seed to Saved Messages; admin user "
            "row was already created — they can recover only by removing "
            "that row and re-bootstrapping"
        )
        # We deliberately do NOT rollback the user row: if delivery fails,
        # the admin can manually look up the seed by re-running with the
        # row deleted. Otherwise we'd have a race where the row exists but
        # the human never saw the words.
        raise

    return True


__all__ = ["SAVED_MESSAGES_PEER", "ensure_admin_seed_delivered"]
