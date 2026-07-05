# LCloud

> Personal cloud storage that lives inside your own Telegram account.

LCloud turns a single Telegram userbot account into a multi-user cloud
storage backend. Files are stored as documents in Telegram supergroups
("clouds"); LCloud adds authentication, an HTTP API, a web UI, and
metadata indexing on top.

**Status**: V2 ready. 202 tests passing, deployed to production.

- 🔐 BIP39 seed-phrase auth (12 or 24 words → Ed25519 keypair)
- 🔑 API keys `lc-XXXXXXXXXXXXXX` for programmatic access
- 📁 Per-user clouds & files isolation
- ✍️ Client-side LC2 file signing (server never sees your privkey)
- 📊 Per-user storage quotas
- 🌐 React web UI + FastAPI backend + auto-generated Swagger docs

## Quick Links

- **[Quickstart for end users](docs/QUICKSTART.md)** — sign up, upload files, get an API key
- **[API guide](docs/API.md)** — curl / Python / JS examples for every endpoint
- **[LCloud DB](docs/LCLOUD_DB.md)** — JSON document database API + SDK guide
- **[LCloud DB AI guide](docs/LCLOUD_DB_AI.md)** — concise usage rules for AI agents, including browser-only/serverless usage
- **[SDK publishing](docs/SDK_PUBLISHING.md)** — release checklist for `@lcloud/db`
- **[Architecture](docs/ARCHITECTURE.md)** — how it all fits together
- **[Crypto model](docs/CRYPTO.md)** — what the seed phrase actually does
- **[Operator guide](docs/OPERATOR.md)** — deploy, configure, run
- **Live Swagger UI**: `https://your-host/docs`
- **OpenAPI JSON**: `https://your-host/openapi.json`

## Stack

| Layer | Tech |
|---|---|
| Telegram | [Telethon](https://docs.telethon.dev/) (MTProto) |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, SQLite + FTS5 |
| Crypto | PyNaCl (Ed25519), `mnemonic` (BIP39), argon2-cffi (API keys) |
| Web | React 18, Vite 5, TypeScript, Tailwind 3, TanStack Query |
| Web crypto | `@scure/bip39`, `@noble/ed25519`, `@noble/hashes` |

## Project layout

```
LCloud/
├── lcloud/              # Python backend
│   ├── api/             # FastAPI routers
│   │   ├── auth.py        V1 admin (legacy phone+code)
│   │   ├── auth_v2.py     V2 BIP39 challenge-response
│   │   ├── api_keys.py    /api/v1/keys mint/list/revoke
│   │   ├── v2_clouds.py   /api/v1/clouds (per-user)
│   │   ├── v2_files.py    /api/v1/files (per-user, LC2)
│   │   ├── clouds.py      V1 admin clouds
│   │   ├── files.py       V1 admin files
│   │   ├── tags.py        Tags
│   │   ├── search.py      FTS search
│   │   └── magic.py       Admin magic-link login
│   ├── auth/            # Auth utilities
│   │   ├── seed.py        BIP39 ↔ Ed25519
│   │   ├── api_keys.py    argon2id mint/verify
│   │   ├── v2_deps.py     get_current_user dep
│   │   └── storage_quota.py
│   ├── crypto/          # Cryptographic primitives
│   │   ├── keys.py        Admin keypair on disk
│   │   ├── lc1.py         V1 caption + signature
│   │   └── lc2.py         V2 client-signed caption
│   ├── db/              # SQLAlchemy models + migrations
│   ├── userbot/         # Telethon manager + handlers
│   └── workers/         # Rate limiter + worker pool
├── alembic/             # DB migrations
├── tests/               # 202 tests
├── web/                 # React SPA
│   └── src/
│       ├── api/         # client, v2_client
│       ├── auth/        # seed, lc2 (browser-side)
│       ├── components/  # LoginScreen, FilesPanel, Settings, ...
│       └── hooks/       # useAuth, useAuthV2
└── docs/                # All markdown docs
```

## Run locally

```bash
# Backend
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
# Edit .env: TG_API_ID, TG_API_HASH from https://my.telegram.org/apps
.venv/bin/lcloud  # serves on 127.0.0.1:8787

# Frontend (dev mode with HMR)
cd web
npm install
npm run dev  # 127.0.0.1:8788, proxies API to :8787
```

For production deploy see **[OPERATOR.md](docs/OPERATOR.md)**.

## Run tests

```bash
.venv/bin/pytest                          # 202 tests
.venv/bin/ruff check .                    # linter
.venv/bin/mypy lcloud                     # type check (strict)
cd web && npm run build                   # frontend type check + build
```

## License

MIT. See [LICENSE](LICENSE).


## Telegram DB backup

LCloud DB writes stay fast: API requests commit to SQLite first, then a
background worker copies committed `json_operations` to Telegram as compressed
segments. Each segment is uploaded to the connected Telegram account's Saved
Messages as a document with an `LCDB1:{...}` caption and a SHA-256 checksum.

Live status:

```bash
curl "$LCLOUD_ENDPOINT/api/v1/db/backup/status"   -H "Authorization: Bearer $LCLOUD_API_KEY"
```

Important fields:

| Field | Meaning |
| --- | --- |
| `last_local_operation_id` | newest local JSON DB operation |
| `last_backed_up_operation_id` | newest operation uploaded to Telegram |
| `lag_operations` | operations still waiting for Telegram backup |
| `last_segment.telegram_message_id` | Telegram message that stores the latest segment |

Environment knobs:

```env
LC_JSON_DB_BACKUP_ENABLED=true
LC_JSON_DB_BACKUP_INTERVAL_SECONDS=5
LC_JSON_DB_BACKUP_BATCH_OPERATIONS=250
```

Current backup format is `lcloud-json-db-segment-v1`. Restore downloads
`LCDB1` segments from Telegram, verifies checksums, then replays operations
into SQLite.

Restore after bootstrapping the target user/account. Stop the API service before a real restore so it does not write to SQLite in parallel:

```bash
systemctl stop lcloud.service
python -m lcloud.userbot.db_restore --target-user-id 1 --dry-run
python -m lcloud.userbot.db_restore --target-user-id 1
systemctl start lcloud.service
```

Use `--source-user-id OLD_ID` when restoring only one old owner namespace from
Saved Messages. `--target-user-id` is the local user that will own restored
collections on the new VPS.
