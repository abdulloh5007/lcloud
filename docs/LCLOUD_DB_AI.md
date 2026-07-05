# LCloud DB AI Usage Guide

Use this file when an AI agent needs to write code against LCloud DB.

## What LCloud DB is

LCloud DB is a JSON document database with collections and documents.
It is similar to a small Firestore-style API, not SQL. Do not generate SQL for
app code. Use the REST API or the `@lcloud/db` SDK. For files, photos, videos,
and other binary media, use the SDK media helpers and store the returned file
ID/URL inside JSON documents.

## Always use these primitives

For trusted server-side code, CLIs, CI jobs, workers, and AI agents with a
secret API key:

```ts
import { createClient } from "@lcloud/db";

const db = createClient({
  endpoint: process.env.LCLOUD_ENDPOINT!,
  apiKey: process.env.LCLOUD_API_KEY!,
});

const meta = await db.meta();
```

Then:

```ts
await db.ensureCollection("users");

const users = db.collection("users");
const rules = await users.setRules({ read: "public", write: "owner" });
await users.insert({ name: "Alice" }, "alice");
const alice = await users.get("alice");
await users.update("alice", { online: true });
await users.batch([
  { op: "set", id: "bob", data: { name: "Bob", online: false } },
  { op: "update", id: "alice", data: { online: false } },
]);
await users.delete("alice");

const media = await db.ensureCloud("app-media");
const uploaded = await db.cloud(media.id).upload(fileOrBlob, {
  name: "avatar.png",
});
await users.update("alice", { avatar_file_id: uploaded.id });

const publicUsers = db.publicCollection(rules.collection_id);
const publicAlice = await publicUsers.get("alice");
```

For a browser-only static site with no backend, use a publishable DB key:

```ts
import { createBrowserClient } from "@lcloud/db";

const lcloud = createBrowserClient({
  endpoint: import.meta.env.VITE_LCLOUD_ENDPOINT,
  publishableKey: import.meta.env.VITE_LCLOUD_DB_KEY,
});

const posts = lcloud.collection<Post>("posts");

const page = await posts.list({ limit: 20 });
```

Use `VITE_LCLOUD_DB_KEY=lcpk_...`, not `LCLOUD_API_KEY`, in frontend env.
Frontend env vars are public after build.

## Before coding

Run the CLI checks when shell access is available:

```bash
npx @lcloud/db doctor --endpoint $LCLOUD_ENDPOINT --key $VITE_LCLOUD_DB_KEY --collection posts
npx @lcloud/db check . --strict
```

Use `doctor` to read the live server contract (`_meta`), SDK version status,
rate limits, page limits, batch limits, upload limit, and whether a publishable
key can access the intended collection. Use `check` to catch the common AI
mistakes: frontend owner API keys and local JSON DB fallbacks.

For a new browser-only integration, scaffold the expected env and sample code:

```bash
npx @lcloud/db init --endpoint https://tg-lcloud.duckdns.org --key lcpk_... --collection posts
```

If the SDK is old, show the user the upgrade command:

```bash
npx @lcloud/db upgrade
```

## Never do these

- Do not expose `LCLOUD_API_KEY` in public browser bundles.
- Do not put owner API keys in `VITE_*`, `NEXT_PUBLIC_*`, or client-side `.env`.
- Do not create a local JSON DB fallback when LCloud endpoint/collection ID is
  configured; use LCloud as the source of truth.
- Do not write directly to SQLite tables from app code.
- Do not store large files/base64 blobs in JSON documents.
- Do not use raw `fetch` for media unless the SDK cannot be used.
- Do not assume joins, SQL transactions, or realtime listeners exist.
- Do not exceed `meta.batch.max_writes`, `meta.pagination.max_limit`, or
  `meta.query.max_where_filters`.
- Do not create collection names with spaces, slashes, or leading numbers.
- Do not manually concatenate unescaped document IDs into URLs; use the SDK.

## Live limits

Always prefer the server-reported limits:

```ts
const meta = await db.meta();
```

Current contract:

| Area | Limit |
| --- | --- |
| Collection name | `^[A-Za-z][A-Za-z0-9_-]{0,63}$`, max 64 chars |
| Reserved collection | `collections` |
| Document ID | `^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$`, max 128 chars |
| Document data | JSON object only; recommended under 100 KB |
| List/query page size | default 50, max 500 |
| Query filters | max 20 `where` filters |
| Query field path | max 128 chars; dot notation |
| Batch writes | max 100 writes; atomic all-or-nothing |
| Access rules | `owner`, `authenticated`, `public`; default read/write is `owner` |
| Public read rate limit | 120 requests/minute/IP |
| Public write rate limit | 30 requests/minute/IP |
| Public write validator | `max_bytes`, `max_fields`, `required_fields`, `allowed_fields` |
| Realtime | Server-Sent Events; cursor is `json_operations.id` |
| API keys | max 25 active keys per user |
| Upload size | read `meta.media.max_upload_bytes` |
| V2 login rate limit | 10 challenge/verify requests per 5 minutes per IP |
| DB HTTP rate limit | no explicit per-user limit yet |
| Storage HTTP rate limit | no explicit HTTP limit yet; Telegram MTProto limiter applies |

If you see `429`, back off. For upload bursts, also respect
`meta.rate_limits.telegram_mtproto`.

## Collection and document naming

Collection names must match:

```regex
^[A-Za-z][A-Za-z0-9_-]{0,63}$
```

Good:

```text
users
posts
orders_2026
app-settings
```

Bad:

```text
123users
user profiles
users/list
collections
```

Document IDs must match:

```regex
^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$
```

Good:

```text
user_123
post:hello
settings.main
```

## Access rules

Use owner endpoints with API key/cookie for private server-side work:

```ts
const posts = db.collection("posts");
await posts.setRules({ read: "owner", write: "owner" });
```

Use public read for frontend pages that must not expose an API key:

```ts
const rules = await db.collection("posts").setRules({
  read: "public",
  write: "owner",
});
await db.collection("posts").setValidator({
  max_bytes: 2048,
  required_fields: ["email"],
  allowed_fields: ["email", "message"],
});

const publicPosts = db.publicCollection(rules.collection_id);
const page = await publicPosts.list({ limit: 20 });
```

For a plain browser site, the setup above is done once by an admin/server
script or in DB Console. The browser uses only:

```ts
const lcloud = createBrowserClient({
  endpoint: "https://your-lcloud-host",
  publishableKey: "lcpk_...",
});
const publicPosts = lcloud.collection("posts");
```

If the browser site is hosted on a different origin, configure:

```env
LC_CORS_ALLOW_ORIGINS=https://my-site.com
```

Rules:

| Rule | Meaning |
| --- | --- |
| `owner` | Only collection owner can access |
| `authenticated` | Any logged-in LCloud user/API key can access |
| `public` | No credentials required |

Do not set `write: "public"` unless the app intentionally accepts anonymous
browser writes. Public writes are rate-limited per IP and should always use a
write validator.

Validator fields:

| Field | Meaning |
| --- | --- |
| `max_bytes` | Max serialized JSON document size |
| `max_fields` | Max number of top-level fields |
| `required_fields` | Top-level fields that must exist |
| `allowed_fields` | Reject top-level fields outside this list |

## Realtime

Use SDK watch helpers when running in a browser with EventSource:

```ts
const source = db.collection("posts").watch((event) => {
  console.log(event.op, event.doc_id, event.payload);
});

source.close();
```

For public collections:

```ts
const publicPosts = db.publicCollection(collectionId);
const source = publicPosts.watch((event) => {
  console.log(event.id, event.op);
});
```

REST/SSE fallback:

```text
GET /api/v1/db/{collection}/events?since=0
GET /api/v1/public/db/{collection_id}/events?since=0
```

SSE event name is `lcloud.db.change`. Resume with `since=<last_event_id>`.
Use `once=true` only for finite polling/tests.

## CRUD snippets

Create or ensure a collection:

```ts
await db.ensureCollection("posts");
```

Insert with generated ID:

```ts
const row = await db.collection("posts").insert({
  title: "Hello",
  published: false,
  created_at: new Date().toISOString(),
});
```

Insert with stable ID:

```ts
await db.collection("users").insert(
  { name: "Alice", role: "admin" },
  "alice",
);
```

Upsert/replace:

```ts
await db.collection("settings").set("main", {
  theme: "dark",
  uploads: { compress: true },
});
```

Patch shallow fields:

```ts
await db.collection("users").update("alice", {
  last_seen_at: new Date().toISOString(),
});
```

Get:

```ts
const row = await db.collection("users").get("alice");
console.log(row.data.name);
```

List:

```ts
const page = await db.collection("posts").list({ limit: 50, offset: 0 });
```

Query:

```ts
const admins = await db.collection("users").query({
  where: [{ field: "role", op: "==", value: "admin" }],
  order_by: "created_at",
  order: "desc",
  limit: 20,
});
```

Nested field query:

```ts
await db.collection("users").query({
  where: [{ field: "profile.city", op: "startsWith", value: "Tash" }],
});
```

Delete:

```ts
await db.collection("posts").delete("post_123");
```

Atomic batch writes:

```ts
await db.collection("posts").batch([
  { op: "create", id: "draft", data: { title: "Draft" } },
  { op: "set", id: "published", data: { title: "Published" } },
  { op: "update", id: "post_123", data: { edited: true } },
  { op: "delete", id: "old_post" },
]);
```

Use batch when multiple writes must succeed or fail together.
Never send more than `meta.batch.max_writes` writes in one batch.

## Media snippets

List clouds:

```ts
const clouds = await db.listClouds();
```

Create a media cloud if needed:

```ts
const cloud = await db.ensureCloud("app-media");
```

Upload media:

```ts
const uploaded = await db.cloud(cloud.id).upload(fileOrBlob, {
  name: "photo.jpg",
  compress: true,
  onProgress(progress) {
    console.log(progress.percent);
  },
});
```

Store media reference in a document:

```ts
await db.collection("posts").update("post_123", {
  cover_file_id: uploaded.id,
  cover_url: db.file(uploaded.id).downloadUrl(),
});
```

List files:

```ts
const files = await db.cloud(cloud.id).listFiles({ limit: 50 });
```

Delete file:

```ts
await db.file(uploaded.id).delete();
```

## REST fallback

If SDK cannot be used, call:

```text
GET    /api/v1/db/_meta
GET    /api/v1/db/collections
POST   /api/v1/db/collections
DELETE /api/v1/db/collections/{collection}

GET    /api/v1/db/{collection}?limit=50&offset=0
POST   /api/v1/db/{collection}
POST   /api/v1/db/{collection}/query
POST   /api/v1/db/{collection}/batch
GET    /api/v1/db/{collection}/{doc_id}
PUT    /api/v1/db/{collection}/{doc_id}
PATCH  /api/v1/db/{collection}/{doc_id}
DELETE /api/v1/db/{collection}/{doc_id}

GET    /api/v1/public/db/{collection_id}?limit=50&offset=0
POST   /api/v1/public/db/{collection_id}
POST   /api/v1/public/db/{collection_id}/query
GET    /api/v1/public/db/{collection_id}/{doc_id}
PUT    /api/v1/public/db/{collection_id}/{doc_id}
PATCH  /api/v1/public/db/{collection_id}/{doc_id}
DELETE /api/v1/public/db/{collection_id}/{doc_id}
```

Headers:

```http
Authorization: Bearer lc-...
Content-Type: application/json
```

## Error handling

The SDK throws `LCloudDbError`.

```ts
try {
  await db.collection("users").get("missing");
} catch (error) {
  if (error instanceof LCloudDbError && error.reason === "document_not_found") {
    // create it or show empty state
  }
}
```

Common reasons:

```text
no_credentials
collection_exists
collection_not_found
document_exists
document_not_found
invalid_collection_name
invalid_document_id
rate_limited
file_too_large
key_limit_reached
```

## Mapping from other databases

Firebase:

```ts
// Firestore style
doc(db, "users/alice")

// LCloud style
db.collection("users").doc("alice")
```

Supabase:

```ts
// Supabase table
supabase.from("users").select().eq("role", "admin")

// LCloud collection
db.collection("users").query({
  where: [{ field: "role", op: "==", value: "admin" }],
});
```

SQLite/Postgres:

```sql
select * from users where role = 'admin';
```

```ts
await db.collection("users").query({
  where: [{ field: "role", op: "==", value: "admin" }],
});
```

## Recommended schema pattern

Use top-level fields for common filters:

```json
{
  "title": "Post title",
  "author_id": "user_123",
  "status": "published",
  "created_at": "2026-07-05T10:00:00.000Z",
  "tags": ["news", "telegram"]
}
```

Avoid hiding filter fields deep inside large nested objects unless needed.


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
