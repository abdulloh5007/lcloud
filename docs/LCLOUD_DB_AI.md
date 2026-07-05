# LCloud DB AI Usage Guide

Use this file when an AI agent needs to write code against LCloud DB.

## What LCloud DB is

LCloud DB is a JSON document database with collections and documents.
It is similar to a small Firestore-style API, not SQL. Do not generate SQL for
app code. Use the REST API or the `@lcloud/db` SDK. For files, photos, videos,
and other binary media, use the SDK media helpers and store the returned file
ID/URL inside JSON documents.

## Always use these primitives

```ts
import { createClient } from "@lcloud/db";

const db = createClient({
  endpoint: process.env.LCLOUD_ENDPOINT!,
  apiKey: process.env.LCLOUD_API_KEY!,
});
```

Then:

```ts
await db.ensureCollection("users");

const users = db.collection("users");
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
```

## Never do these

- Do not expose `LCLOUD_API_KEY` in public browser bundles.
- Do not write directly to SQLite tables from app code.
- Do not store large files/base64 blobs in JSON documents.
- Do not use raw `fetch` for media unless the SDK cannot be used.
- Do not assume joins, SQL transactions, or realtime listeners exist.
- Do not create collection names with spaces, slashes, or leading numbers.
- Do not manually concatenate unescaped document IDs into URLs; use the SDK.

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
GET    /api/v1/db/collections
POST   /api/v1/db/collections
DELETE /api/v1/db/collections/{collection}

GET    /api/v1/db/{collection}?limit=50&offset=0
POST   /api/v1/db/{collection}
POST   /api/v1/db/{collection}/query
GET    /api/v1/db/{collection}/{doc_id}
PUT    /api/v1/db/{collection}/{doc_id}
PATCH  /api/v1/db/{collection}/{doc_id}
DELETE /api/v1/db/{collection}/{doc_id}
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
