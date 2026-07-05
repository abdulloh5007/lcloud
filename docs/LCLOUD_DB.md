# LCloud DB

LCloud DB is a JSON document database API built into LCloud. It gives apps a
Firebase/Supabase-like developer experience while storing the materialized
index in LCloud's database and writing every change to an append-only JSON
operation log designed for future Telegram snapshot/segment persistence. The
JavaScript SDK also includes media storage helpers for uploading files/photos/
videos to LCloud's Telegram-backed file storage.

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

LCloud DB uses three tables:

| Table | Purpose |
| --- | --- |
| `json_collections` | Per-user collection namespace |
| `json_documents` | Current materialized document state |
| `json_operations` | Append-only oplog for replay, audit, and future Telegram JSONL segments |

The API surface should remain stable when Telegram-backed snapshot flushing is
added later. Apps should treat the HTTP API and SDK as the contract, not the SQL
tables.

## Authentication

Use one of:

| Method | Use case |
| --- | --- |
| `lc_user_session` cookie | Browser UI already logged into LCloud |
| `Authorization: Bearer lc-...` | Server-side apps, scripts, CLIs, AI agents, external sites |

Create API keys in the web UI: Settings -> API keys -> Create key.

Never expose an API key in frontend code shipped to users. Browser-only apps
should use the LCloud web session or a backend proxy.

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
| Access rules | `owner`, `authenticated`, `public`; default read/write is `owner` |
| Public read rate limit | 120 requests/minute/IP |
| Public write rate limit | 30 requests/minute/IP |
| Public write validator | `max_bytes`, `max_fields`, `required_fields`, `allowed_fields` |
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
| `authenticated` | Any valid LCloud user session or API key can access |
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

The same SDK can upload media/files through the existing LCloud file API:

```ts
const mediaCloud = await db.ensureCloud("app-media");

const uploaded = await db.cloud(mediaCloud.id).upload(fileOrBlob, {
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
await db.listClouds();
await db.createCloud("app-media");
await db.ensureCloud("app-media");
await db.deleteCloud(cloudId);

await db.cloud(cloudId).listFiles({ limit: 50, offset: 0 });
await db.cloud(cloudId).upload(fileOrBlob, { name: "photo.jpg", compress: true });

db.file(fileId).downloadUrl();
await db.file(fileId).delete();
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

- No realtime subscriptions yet.
- Query filtering is currently in the API process over materialized JSON rows.
- No compound indexes exposed to users yet.
- `PATCH` is shallow: it merges top-level fields only.
- Access rules and write validators are collection-level only.
- Telegram snapshot/segment flushing is planned but not active in the MVP.
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
