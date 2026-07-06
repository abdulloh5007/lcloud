# LCloud DB

LCloud DB is a JSON document database API built into LCloud. It gives apps a
Firebase/Supabase-like developer experience while storing the materialized
index in LCloud's database and writing every change to an append-only JSON
operation log backed up to Telegram. Every Database owns one Telegram chat;
its JSON backups and uploaded files/photos/videos are stored in that chat.

Current status: MVP document database. Good for small apps, dashboards, bots,
personal tools, CMS-like content, and prototypes that need a simple hosted JSON
store with API-key auth.

It is not a SQL replacement yet. Do not use it for joins, high-frequency writes,
complex analytics, or transactional financial systems.

## Mental model

| If you know... | Think of LCloud DB as... |
| --- | --- |
| Firebase Firestore | collections and JSON documents, but without realtime listeners yet |
| Supabase | REST API + API key auth, but document-oriented instead of table-oriented |
| SQLite/Postgres | LCloud currently uses SQL as the local materialized index; callers use JSON docs |
| S3/object storage | use SDK media helpers for files; keep only file IDs/URLs in JSON docs |

## Storage model

The hierarchy is:

```text
Database -> Telegram chat
  Collections -> JSON documents
  Media -> files/photos/videos
  Keys -> lcpk_ and lstore_
  Backups -> compressed operation segments in the same chat
```

Core tables:

| Table | Purpose |
| --- | --- |
| `json_databases` | Top-level project linked to one Telegram cloud/chat |
| `json_collections` | Collection namespace inside one database |
| `json_documents` | Current materialized document state |
| `json_operations` | Append-only oplog for replay, audit, and Telegram backup segments |

Apps should treat the HTTP API and SDK as the contract, not the SQL tables.

Create and select a database before creating collections:

```ts
const admin = createClient({ endpoint, apiKey });
const project = await admin.createDatabase("website");
const db = admin.database(project.id);
await db.ensureCollection("posts");
```

Creating the database creates its Telegram chat. `project.cloud_id` is used by
server-side media helpers; browser storage keys created through the scoped
client automatically use that same cloud.

## Authentication

Use one of:

| Method | Use case |
| --- | --- |
| `lc_user_session` cookie | Browser UI already logged into LCloud |
| `Authorization: Bearer lc-...` | Server-side apps, scripts, CLIs, AI agents, external sites |
| Publishable DB key `lcpk_...` | Static browser sites with collection rules set to `public` |
| Public collection ID | Lower-level anonymous access by numeric collection ID |

Create API keys in the web UI: Settings -> API keys -> Create key.

Never expose an API key in frontend code shipped to users. Browser-only apps
should use public collection endpoints, the LCloud web session, or a backend
proxy.

### Browser-only / serverless mode

This is the Supabase/Firebase-style mode for a plain static website:

1. In DB Console or a trusted admin script, create/select a Database.
2. Create a collection inside that Database.
3. In DB Console -> Keys, create a publishable DB key (`lcpk_...`).
4. Set access rules:
   - public read site: `{ "read": "public", "write": "owner" }`
   - public form: `{ "read": "owner", "write": "public" }`
   - private per-user data: `{ "read": "document_owner", "write": "document_owner" }`
5. For public writes, set a validator with `max_bytes`, `max_fields`,
   `required_fields`, and `allowed_fields`.
6. In frontend code use `createBrowserClient()` with `publishableKey`.

Frontend `.env` values are not secret. Vite/Next/browser builds expose them in
JavaScript. Do not use `LCLOUD_API_KEY` in frontend `.env`. LCloud API keys are
owner secrets, not public anon keys.

Server CORS must allow the static site origin:

```env
LC_CORS_ALLOW_ORIGINS=https://my-site.com,https://www.my-site.com
```

Use `*` only for public endpoints/collections. Do not combine wildcard CORS
with cookie or API-key browser flows.

Browser-only example:

```ts
import { createBrowserClient } from "@lcloud/db";

const lcloud = createBrowserClient({
  endpoint: import.meta.env.VITE_LCLOUD_ENDPOINT,
  publishableKey: import.meta.env.VITE_LCLOUD_DB_KEY,
});

const posts = lcloud.collection<Post>("posts");
const page = await posts.list({ limit: 20 });

const contact = lcloud.collection<ContactMessage>("contact_forms");
await contact.insert({
  email,
  message,
  source: "landing-page",
});
```

Do not add a local JSON database fallback for production. If LCloud is
configured, it is the source of truth. Local JSON files are acceptable only for
explicit offline mocks/tests.

For private per-user documents, sign in anonymously once. The SDK restores the
session after page reload and refreshes access tokens automatically:

```ts
if (!lcloud.auth) throw new Error("publishableKey is required");
if (!lcloud.auth.currentUser) await lcloud.auth.signInAnonymously();

const notes = lcloud.collection<{ text: string }>("notes");
await notes.insert({ text: "Only this browser user can read this" });
```

The access JWT lasts 15 minutes. The revocable refresh token uses a sliding
365-day lifetime, so an actively used app does not require periodic manual
authentication. Public rules require no auth at all. Anonymous identity cannot
be recovered after browser storage is cleared until account linking providers
are added.

Publishable DB key route shape:

```text
GET    /api/v1/public/db/key/{publishable_key}/{collection}
POST   /api/v1/public/db/key/{publishable_key}/{collection}
POST   /api/v1/public/db/key/{publishable_key}/{collection}/query
GET    /api/v1/public/db/key/{publishable_key}/{collection}/{doc_id}
PUT    /api/v1/public/db/key/{publishable_key}/{collection}/{doc_id}
PATCH  /api/v1/public/db/key/{publishable_key}/{collection}/{doc_id}
DELETE /api/v1/public/db/key/{publishable_key}/{collection}/{doc_id}
GET    /api/v1/public/db/key/{publishable_key}/{collection}/events
```

App auth route shape:

```text
POST   /api/v1/public/auth/key/{publishable_key}/anonymous
POST   /api/v1/public/auth/key/{publishable_key}/refresh
POST   /api/v1/public/auth/key/{publishable_key}/sign-out
GET    /api/v1/public/auth/key/{publishable_key}/me
```

Publishable storage key route shape:

```text
GET    /api/v1/public/storage/key/{storage_key}/files
POST   /api/v1/public/storage/key/{storage_key}/files
GET    /api/v1/public/storage/key/{storage_key}/files/{file_id}/download
DELETE /api/v1/public/storage/key/{storage_key}/files/{file_id}
```

## Naming rules

Collections:

- must match `^[A-Za-z][A-Za-z0-9_-]{0,63}$`
- max 64 chars
- cannot be `collections`
- examples: `users`, `posts`, `orders_2026`, `app-settings`

Document IDs:

- must match `^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$`
- max 128 chars
- examples: `user_123`, `post:hello-world`, `settings.main`
- omit `id` on insert to auto-generate `doc_xxxxxxxxxxxxxxxx`

Documents:

- must be JSON objects, not arrays/primitives
- nested objects are allowed
- store files separately in LCloud Files; store file IDs/URLs in documents

## CLI

`@lcloud/db` ships with a terminal helper for real projects and AI agents:

```bash
npx @lcloud/db doctor --endpoint https://tg-lcloud.duckdns.org --key lcpk_... --collection posts
npx @lcloud/db init --endpoint https://tg-lcloud.duckdns.org --key lcpk_... --collection posts
npx @lcloud/db upgrade
npx @lcloud/db check . --strict
```

`doctor` verifies the package version, server `_meta`, limits, rate limits, and
optional publishable-key collection access. `init` writes a browser-only
`.env.example` and TypeScript sample. `upgrade` prints or runs the package
manager command for the latest SDK. `check` scans app code for frontend owner
API keys and local JSON fallback patterns.


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

Use `--chat-id TELEGRAM_CHAT_ID` when restoring from a specific Database chat,
and `--source-database-id OLD_DATABASE_ID` when that chat contains multiple
backup streams. `--target-user-id` is the local user that will own restored
collections on the new VPS.

## REST API

Base URL:

```text
https://your-lcloud-host/api/v1/db
```

Machine-readable limits and capabilities:

```bash
curl "$BASE/_meta"
```

Use this endpoint from SDKs, CLIs, and AI agents before generating large
requests. It returns the live server limits for pagination, query filters,
batch writes, upload size, auth, and rate limits.

## Limits and rate limits

| Area | Current limit |
| --- | --- |
| Collection name | `^[A-Za-z][A-Za-z0-9_-]{0,63}$`, max 64 chars |
| Reserved collections | `collections` |
| Document ID | `^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$`, max 128 chars |
| Document body | JSON object only; recommended under 100 KB |
| List documents | `limit` default 50, max 500; `offset` min 0 |
| Query filters | max 20 `where` filters |
| Query field path | max 128 chars, dot notation like `profile.city` |
| Query page size | `limit` default 50, max 500 |
| Batch writes | max 100 writes per request |
| Batch operations | `create`, `set`, `update`, `delete` |
| Batch atomicity | all writes commit together or none commit |
| Access rules | `owner`, `document_owner`, `authenticated`, `public`; default is `owner` |
| App access JWT | 15 minutes; refreshed automatically by the SDK |
| App refresh token | Revocable, sliding 365 days; stored by the browser SDK |
| Public read rate limit | 120 requests/minute/IP |
| Public write rate limit | 30 requests/minute/IP |
| Public write validator | `max_bytes`, `max_fields`, `required_fields`, `allowed_fields` |
| Realtime | Server-Sent Events over `json_operations`; cursor is operation `id` |
| File list page size | `limit` default 50, max 500 |
| Upload size | deployment setting `LC_MAX_FILE_BYTES`; read `media.max_upload_bytes` from `_meta` |
| API keys | max 25 active keys per user |
| V2 login rate limit | 10 total `/auth/v2/challenge` + `/auth/v2/verify` requests per 5 minutes per IP |
| DB HTTP rate limit | no explicit per-user DB rate limit yet |
| Storage HTTP rate limit | no explicit HTTP rate limit yet; Telegram MTProto limiter still applies |
| MTProto limiter | read `rate_limits.telegram_mtproto` from `_meta` |

If a request gets `429`, back off and retry later. If a Telegram-backed storage
request fails with flood-wait style errors, use exponential backoff and avoid
parallel upload bursts. For DB writes, prefer batch writes over many individual
requests when the operations must succeed together.

### Collections

Create:

```bash
curl -X POST "$BASE/collections" \
  -H "Authorization: Bearer $LCLOUD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"users"}'
```

List:

```bash
curl "$BASE/collections" \
  -H "Authorization: Bearer $LCLOUD_API_KEY"
```

Delete:

```bash
curl -X DELETE "$BASE/collections/users" \
  -H "Authorization: Bearer $LCLOUD_API_KEY"
```

Access rules:

```bash
curl -X PUT "$BASE/collections/users/rules" \
  -H "Authorization: Bearer $LCLOUD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"read":"public","write":"owner"}'
```

Public write validator:

```bash
curl -X PUT "$BASE/collections/users/validator" \
  -H "Authorization: Bearer $LCLOUD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "max_bytes": 2048,
    "max_fields": 4,
    "required_fields": ["email"],
    "allowed_fields": ["email", "message", "source", "created_at"]
  }'
```

Rules:

| Rule | Meaning |
| --- | --- |
| `owner` | Only the collection owner can access |
| `document_owner` | App user can create documents and access only rows carrying their immutable `owner_id` |
| `authenticated` | Project app user, or an authenticated LCloud owner, can access |
| `public` | No credentials required |

Collections default to `{ "read": "owner", "write": "owner" }`. To use
public frontend reads without exposing an API key, set `read` to `public` and
call the public API with the returned `collection_id`:

```bash
curl "https://your-lcloud-host/api/v1/public/db/123?limit=50&offset=0"
curl "https://your-lcloud-host/api/v1/public/db/123/doc_id"
```

Only set `write` to `public` for intentionally open forms or append-only public
data. Public writes are rate-limited per IP and should use a write validator.

### Documents

Insert with a custom ID:

```bash
curl -X POST "$BASE/users" \
  -H "Authorization: Bearer $LCLOUD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"id":"alice","data":{"name":"Alice","role":"admin","score":10}}'
```

Insert with generated ID:

```bash
curl -X POST "$BASE/users" \
  -H "Authorization: Bearer $LCLOUD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"data":{"name":"Bob","role":"user"}}'
```

List:

```bash
curl "$BASE/users?limit=50&offset=0" \
  -H "Authorization: Bearer $LCLOUD_API_KEY"
```

Get:

```bash
curl "$BASE/users/alice" \
  -H "Authorization: Bearer $LCLOUD_API_KEY"
```

Replace/upsert:

```bash
curl -X PUT "$BASE/users/alice" \
  -H "Authorization: Bearer $LCLOUD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"data":{"name":"Alice","role":"owner","score":12}}'
```

Patch top-level fields:

```bash
curl -X PATCH "$BASE/users/alice" \
  -H "Authorization: Bearer $LCLOUD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"data":{"role":"owner"}}'
```

Delete:

```bash
curl -X DELETE "$BASE/users/alice" \
  -H "Authorization: Bearer $LCLOUD_API_KEY"
```

### Query

```bash
curl -X POST "$BASE/users/query" \
  -H "Authorization: Bearer $LCLOUD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "where": [
      {"field":"role","op":"==","value":"admin"},
      {"field":"profile.city","op":"startsWith","value":"Tash"}
    ],
    "order_by": "score",
    "order": "desc",
    "limit": 20,
    "offset": 0
  }'
```

Supported operators:

| Operator | Works with |
| --- | --- |
| `==` | Any JSON value |
| `!=` | Any JSON value |
| `<`, `<=`, `>`, `>=` | Comparable JSON values, usually numbers/strings |
| `contains` | Strings and arrays |
| `startsWith` | Strings |

Nested fields use dot paths, for example `profile.city`.

### Realtime

Owner stream:

```bash
curl -N "$BASE/users/events?since=0" \
  -H "Authorization: Bearer $LCLOUD_API_KEY"
```

Public stream:

```bash
curl -N "https://your-lcloud-host/api/v1/public/db/123/events?since=0"
```

SSE event name:

```text
lcloud.db.change
```

Event data shape:

```json
{
  "id": 123,
  "collection_id": 1,
  "doc_id": "alice",
  "op": "patch",
  "payload": {"data": {"online": true}},
  "created_at": "2026-07-05T10:00:00+00:00"
}
```

Use `since=<last_event_id>` to resume after reconnect. Add `once=true` for a
finite response that returns currently available events and closes.

## JavaScript/TypeScript SDK

Install from npm after publish:

```bash
npm install @lcloud/db
```

For local development from this repository:

```bash
cd sdk
npm install
npm run build
```

Basic usage:

```ts
import { createClient } from "@lcloud/db";

type UserDoc = {
  name: string;
  role: "admin" | "user";
  score: number;
  profile?: { city?: string };
};

const db = createClient({
  endpoint: "https://your-lcloud-host",
  apiKey: process.env.LCLOUD_API_KEY,
});

const meta = await db.meta();
console.log(meta.pagination.max_limit, meta.batch.max_writes);

await db.ensureCollection("users");

const users = db.collection<UserDoc>("users");
const rules = await users.setRules({ read: "public", write: "owner" });
await users.setValidator({
  max_bytes: 2048,
  required_fields: ["email"],
  allowed_fields: ["email", "message"],
});
const publicUsers = db.publicCollection<UserDoc>(rules.collection_id);
const source = publicUsers.watch((event) => {
  console.log(event.op, event.doc_id);
});

await users.insert(
  { name: "Alice", role: "admin", score: 10, profile: { city: "Tashkent" } },
  "alice",
);

await users.doc("alice").update({ score: 11 });

const page = await users.query({
  where: [{ field: "role", op: "==", value: "admin" }],
  order_by: "score",
  order: "desc",
  limit: 20,
});

console.log(page.items.map((row) => row.data.name));
console.log((await publicUsers.get("alice")).data.name);
```

Atomic batch writes:

```ts
await users.batch([
  { op: "create", id: "bob", data: { name: "Bob", role: "user" } },
  { op: "update", id: "alice", data: { score: 12 } },
  { op: "delete", id: "old_user" },
]);
```

The batch endpoint commits all writes in one transaction. If any write fails,
none of the writes are saved.

### SDK media storage

The same SDK can upload media/files through the existing LCloud file API. Use
the selected Database cloud so JSON data, media, keys, and backups stay in the
same Telegram chat:

```ts
const database = await admin.createDatabase("website");
const db = admin.database(database.id);
if (!database.cloud_id) throw new Error("Database has no Telegram cloud");

const uploaded = await db.cloud(database.cloud_id).upload(fileOrBlob, {
  name: "avatar.png",
  compress: true,
  onProgress(progress) {
    console.log(progress.percent);
  },
});

await db.collection("users").update("alice", {
  avatar_file_id: uploaded.id,
  avatar_url: db.file(uploaded.id).downloadUrl(),
});
```

Available media methods:

```ts
const databases = await db.listDatabases();
const database = await db.createDatabase("app");
const scoped = db.database(database.id);
const cloudId = database.cloud_id;
if (!cloudId) throw new Error("Database has no Telegram cloud");

await scoped.cloud(cloudId).listFiles({ limit: 50, offset: 0 });
await scoped.cloud(cloudId).upload(fileOrBlob, { name: "photo.jpg", compress: true });

scoped.file(fileId).downloadUrl();
await scoped.file(fileId).delete();
```

The REST media/file API remains unchanged. SDK media helpers are a convenience
layer over `/api/v1/clouds`, `/api/v1/clouds/{id}/files`, and `/api/v1/files`.

## Response shapes

Collection:

```ts
type CollectionRow = {
  id: number;
  name: string;
  owner_user_id: number;
  created_at: string | null;
  updated_at: string | null;
};
```

Document:

```ts
type DocumentRow<T> = {
  id: string;
  collection_id: number;
  data: T;
  version: number;
  created_at: string | null;
  updated_at: string | null;
};
```

Page:

```ts
type Page<T> = {
  items: T[];
  total: number;
  limit: number;
  offset: number;
};
```

Errors:

```json
{
  "detail": {
    "reason": "collection_not_found"
  }
}
```

Common reasons:

| Reason | Meaning |
| --- | --- |
| `no_credentials` | Missing/invalid session or Bearer key |
| `collection_exists` | Creating a collection that already exists |
| `collection_not_found` | Collection does not exist for this user |
| `document_exists` | Inserting with an ID that already exists |
| `document_not_found` | Document missing or soft-deleted |
| `invalid_collection_name` | Collection name violates naming rules |
| `invalid_document_id` | Document ID violates naming rules |
| `rate_limited` | Auth/payment/recovery rate limit hit; back off before retry |
| `file_too_large` | Upload exceeds `media.max_upload_bytes` |
| `key_limit_reached` | User already has max active API keys |

## Design guidelines for apps

Use stable document IDs when documents map to known entities:

```ts
await db.collection("users").set(userId, userProfile);
```

Use generated IDs for append-only content:

```ts
const post = await db.collection("posts").insert({ title, body, created_by });
```

Keep documents small. Recommended document size is under 100 KB. Large binary
data belongs in LCloud Files.

Prefer simple query fields. If you need to query by `author_id`, store it as a
top-level field:

```json
{
  "author_id": "user_123",
  "title": "Hello"
}
```

Use `collection.batch()` for multi-document writes that must succeed or fail
together. Keep each batch at or below `meta.batch.max_writes`.

## Current limitations

- Realtime uses SSE rather than WebSocket subscriptions.
- Query filtering is currently in the API process over materialized JSON rows.
- No compound indexes exposed to users yet.
- `PATCH` is shallow: it merges top-level fields only.
- Rules are predefined modes, not arbitrary Firebase-style expressions.
- Anonymous identities cannot yet be linked to email/OAuth accounts.
- SDK media uploads use the existing LCloud file API. Built-in client-side LC2
  signing helper is not bundled yet; advanced callers may pass LC2 fields
  manually.

## Roadmap

1. Telegram persistence worker: write `json_operations` to JSONL segment files.
2. Snapshot compaction: periodic collection snapshots in Telegram.
3. Indexed query definitions for larger collections.
4. Realtime change stream over WebSocket/SSE.
5. Document-level validators and per-public-route rate limits.
6. Admin dashboard for collections/documents.


### Browser media uploads

For files/photos/videos from a static frontend, create a publishable storage key
(`lstore_...`) for one cloud in DB Console -> Keys or trusted server code. Do
not use `LCLOUD_API_KEY` in browser code for media.

```env
VITE_LCLOUD_STORAGE_KEY=lstore_...
```

```ts
const lcloud = createBrowserClient({
  endpoint: import.meta.env.VITE_LCLOUD_ENDPOINT,
  publishableKey: import.meta.env.VITE_LCLOUD_DB_KEY,
  storageKey: import.meta.env.VITE_LCLOUD_STORAGE_KEY,
});

const uploaded = await lcloud.storage().upload(file, { name: file.name });
await lcloud.collection("posts").insert({
  title,
  file_id: uploaded.id,
  file_url: lcloud.storage().downloadUrl(uploaded.id),
});
```

Storage keys are scoped to one cloud and have explicit permissions
(`upload`, `list`, `download`, `delete`) plus `max_file_bytes`. Public storage
writes are rate-limited per IP.
