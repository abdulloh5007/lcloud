# LCloud

Personal cloud file manager backed by a Telegram userbot. Each "cloud"
is a Telegram supergroup; the userbot stores files in it and a web
admin panel manages everything.

> Detailed spec: see [`goal.md`](goal.md). Implementation log:
> [`DECISIONS.md`](DECISIONS.md).

## Status

Phase **P10** — V1 complete. Backend (auth + clouds + files + tags +
FTS5 search + direct-TG-drop ingest), React/Vite admin UI, and a
systemd unit with hardening. 106 backend tests, all green.

## Architecture, in one screen

- **Single Python process** runs FastAPI + Telethon on the same event
  loop. Files travel through a worker pool capped at `LC_MAX_WORKERS`,
  and outgoing MTProto requests pass through a token-bucket rate
  limiter that honours `FloodWaitError` with retry.
- **One Telegram account** = one Telethon session = one admin owner.
  The admin's identity is pinned by `LC_ADMIN_TG_ID`. Logins through
  the wrong account are rejected and archived as
  `data/session.rejected.<ts>.{session,json}` for audit.
- **Each cloud is a Telegram supergroup** with `chat.about` set to
  `LCLOUD1:<pubkey_b64>:<sig_b64>`. On boot the userbot scans
  dialogs, parses + verifies markers, and rebuilds the `clouds` table.
- **Each file is a Telegram message** with caption
  `LC1:{"o":<pubkey>,"s":<sig>,"h":<sha256>,"t":<ts>}`.
  The signature covers `sha256(file) || chat_id || message_id ||
  pubkey || timestamp` (Ed25519 / PyNaCl). Metadata is also indexed
  in SQLite — DB is the fast index, captions are the durable copy.
- **Storage** = Telegram (file bytes) + SQLite WAL (`data/lcloud.db`)
  + Ed25519 keypair in `data/keys/`. No outside services.

```
/root/LCloud/
├── lcloud/                 Python: api/ auth/ crypto/ db/ userbot/ workers/
├── alembic/                migrations (FTS5 trigger included)
├── web/                    React/Vite admin SPA (built into web/dist)
├── data/                   gitignored runtime
│   ├── lcloud.db           sqlite + WAL
│   ├── session.lcloud*     telethon
│   └── keys/               admin.{key,pub} + jwt.secret (mode 600)
├── tests/                  pytest (106 cases)
├── scripts/install-systemd.sh
├── lcloud.service          systemd unit
├── goal.md                 V1 spec + amendments
├── DECISIONS.md            implementation choices, per phase
└── pyproject.toml
```

## API surface

```
GET    /                                  SPA index (or JSON info if web/dist absent)
GET    /health
GET    /auth/state
POST   /auth/telegram/{start,code,password,cancel}
POST   /auth/logout
GET    /clouds
POST   /clouds                            { name }
DELETE /clouds/{cloud_id}
GET    /clouds/{cloud_id}/files
POST   /clouds/{cloud_id}/files           multipart, ≤ LC_MAX_FILE_BYTES
GET    /files/{file_id}/download          streaming
DELETE /files/{file_id}
GET    /files/{file_id}/tags
PUT    /files/{file_id}/tags              { tag_ids: [...] }
GET    /tags
POST   /tags                              { name, color, icon, bg_color }
PATCH  /tags/{tag_id}
DELETE /tags/{tag_id}
GET    /search                            ?q=&cloud_id=&tag=&limit=&offset=
```

All endpoints (except `/auth/*`, `/health`, the SPA, and `/assets/*`)
require the `lc_session` cookie (HS256 JWT, HttpOnly, SameSite=Strict;
set `LC_COOKIE_SECURE=true` once HTTPS is fronted).

## First-time setup

```bash
cd /root/LCloud
cp .env.example .env
# fill in TG_API_ID, TG_API_HASH (https://my.telegram.org)
# and LC_ADMIN_TG_ID (your own Telegram user_id)
chmod 600 .env

# Backend deps
uv sync --all-extras                  # creates .venv, installs Python deps

# Frontend build
cd web && npm install && npm run build && cd ..

# Run once (interactive log) — verifies migrations + Telethon connect
uv run lcloud
# visit http://127.0.0.1:8787 → "Подключить Telegram аккаунт"

# As a service (root)
./scripts/install-systemd.sh
systemctl start lcloud
journalctl -u lcloud -f
```

The first time you open `http://127.0.0.1:8787/`, the SPA shows a
phone-number form. After phone → SMS code → optional 2FA password,
the server checks `me.id == LC_ADMIN_TG_ID` and either issues an
`lc_session` cookie + opens the admin UI, or refuses with 403 +
archives the rejected session.

## Development workflow

```bash
# Backend + tests + lints
uv run pytest                 # 106 tests
uv run ruff check .
uv run mypy lcloud

# Frontend dev (hot-reload, proxies API to 127.0.0.1:8787)
cd web && npm run dev         # http://127.0.0.1:8788

# Frontend build → served by FastAPI at /
cd web && npm run build
```

There is no Docker, no Redis, no Postgres, no Celery. Everything is
SQLite + asyncio.Queue/Semaphore + Telethon, by spec choice.

## Configuration (`.env`)

| Var | Default | Meaning |
|---|---|---|
| `TG_API_ID` / `TG_API_HASH` | — | from https://my.telegram.org |
| `LC_ADMIN_TG_ID` | `0` | the only Telegram user_id allowed to log in |
| `LC_HOST` / `LC_PORT` | `127.0.0.1` / `8787` | bind address |
| `LC_PUBLIC_BASE_URL` | `http://127.0.0.1:8787` | used in cookies / links |
| `LC_MAX_WORKERS` | `10` | concurrent uploads/downloads cap |
| `LC_MAX_FILE_BYTES` | `1073741824` (1 GiB) | per-file hard limit |
| `LC_MTPROTO_RATE_PER_SEC` | `20` | outgoing MTProto token-bucket refill |
| `LC_MTPROTO_BURST` | `20` | bucket capacity |
| `LC_MTPROTO_MAX_FLOODWAIT_SEC` | `300` | give up auto-retry above this |
| `LC_MAGIC_LINK_TTL_SECONDS` | `900` | reserved (legacy) |
| `LC_SESSION_TTL_SECONDS` | `604800` (7d) | cookie lifetime |
| `LC_COOKIE_SECURE` | `false` | flip to true under HTTPS |
| `LC_DATA_DIR` | `data` | runtime files |
| `LC_SESSION_FILE` | `data/session.lcloud` | Telethon base path |
| `LC_DB_URL` | `sqlite+aiosqlite:///data/lcloud.db` | runtime DB |

## What the userbot listens for

- **NewMessage in tracked clouds** (`incoming=True, outgoing=True`) →
  if the message has a `Document` and no `LC1:` caption: hash + sign +
  edit caption + persist a `files` row. Photos are deliberately
  skipped (Telegram compresses them; cloud contract is byte-exact).
  Files larger than `LC_MAX_FILE_BYTES` are deleted from TG with a
  one-line warning into Saved Messages.

## Known limits / non-goals

- V1 is single-admin. The schema (`owners` row + `auth_state` epoch)
  is shaped to extend to multi-owner later, but the surface is gated
  to one user.
- No CSRF token: cookie is `SameSite=Strict` + the entire admin is
  same-origin (built SPA is served by FastAPI). If you ever expose
  the API cross-origin, add CSRF.
- No public sharing / signed download URLs in V1. Per spec — the
  admin's pubkey is the access token; everything else is a future
  iteration.

## Reading the code

If you want to understand what a phase does, jump to `DECISIONS.md` —
each phase (P0..P9) has a section explaining the choices made at
implementation time, including the dead-ends and trade-offs. The
spec itself is `goal.md`; spec amendments are at the bottom.
