# @lcloud/db

TypeScript client for LCloud DB, a JSON document database API built into
LCloud. The SDK also includes LCloud media storage helpers, so apps can store
JSON documents and upload files/media through one client.

```bash
npm install @lcloud/db
```

```ts
import { createClient } from "@lcloud/db";

const db = createClient({
  endpoint: "https://your-lcloud-host",
  apiKey: process.env.LCLOUD_API_KEY,
});

const meta = await db.meta();
console.log(meta.pagination.max_limit, meta.batch.max_writes);

await db.ensureCollection("users");

const users = db.collection("users");
const rules = await users.setRules({ read: "public", write: "owner" });

await users.insert({ name: "Alice", role: "admin" }, "alice");
await users.update("alice", { online: true });
await users.batch([
  { op: "set", id: "bob", data: { name: "Bob", role: "user" } },
  { op: "update", id: "alice", data: { online: false } },
]);

const admins = await users.query({
  where: [{ field: "role", op: "==", value: "admin" }],
  limit: 20,
});

console.log(admins.items);

const media = await db.ensureCloud("app-media");
const uploaded = await db.cloud(media.id).upload(
  new Blob(["hello"], { type: "text/plain" }),
  { name: "hello.txt" },
);

await users.update("alice", {
  avatar_file_id: uploaded.id,
  avatar_url: db.file(uploaded.id).downloadUrl(),
});
```

## API

### `createClient(options)`

```ts
createClient({
  endpoint: "https://your-lcloud-host",
  apiKey: "lc-...",
});
```

Options:

- `endpoint`: LCloud server origin, without `/api/v1/db`.
- `apiKey`: optional Bearer API key. If omitted, browser cookies are used.
- `fetch`: optional custom fetch implementation.

### Collections

```ts
await db.meta();
await db.createCollection("posts");
await db.ensureCollection("posts");
await db.listCollections();
await db.deleteCollection("posts");
```

Access rules:

```ts
const posts = db.collection("posts");

await posts.setRules({ read: "public", write: "owner" });
const rules = await posts.getRules();

const publicPosts = db.publicCollection(rules.collection_id);
const page = await publicPosts.list({ limit: 20 });
const post = await publicPosts.get("hello");
```

Rules:

| Rule | Meaning |
| --- | --- |
| `owner` | Only collection owner can access |
| `authenticated` | Any logged-in LCloud user/API key can access |
| `public` | No credentials required |

Default is `{ read: "owner", write: "owner" }`.

### Documents

```ts
const posts = db.collection("posts");

await posts.insert({ title: "Hello" });
await posts.insert({ title: "Pinned" }, "pinned");
await posts.get("pinned");
await posts.set("pinned", { title: "Pinned v2" });
await posts.update("pinned", { edited: true });
await posts.delete("pinned");
```

Atomic batch writes:

```ts
await posts.batch([
  { op: "create", id: "draft", data: { title: "Draft" } },
  { op: "set", id: "published", data: { title: "Published" } },
  { op: "update", id: "pinned", data: { edited: true } },
  { op: "delete", id: "old-post" },
]);
```

If one write fails, none of the writes are committed.

Document refs:

```ts
const pinned = db.collection("posts").doc("pinned");
await pinned.get();
await pinned.update({ edited: true });
```

### Media storage

List or create media clouds:

```ts
const clouds = await db.listClouds();
const cloud = await db.createCloud("app-media");
const existingOrNew = await db.ensureCloud("app-media");
```

Upload a file:

```ts
const file = await db.cloud(cloud.id).upload(browserFile, {
  compress: true,
  onProgress(progress) {
    console.log(progress.percent);
  },
});
```

In Node 20+:

```ts
const blob = new Blob([await readFile("avatar.png")], { type: "image/png" });
const file = await db.cloud(cloud.id).upload(blob, {
  name: "avatar.png",
  compress: false,
});
```

Store the uploaded file reference in a document:

```ts
await db.collection("users").update("alice", {
  avatar_file_id: file.id,
  avatar_url: db.file(file.id).downloadUrl(),
});
```

List files:

```ts
const page = await db.cloud(cloud.id).listFiles({ limit: 50 });
```

Delete a file:

```ts
await db.file(file.id).delete();
```

### Limits

Use `db.meta()` for machine-readable server limits:

```ts
const meta = await db.meta();

meta.pagination.max_limit; // 500
meta.query.max_where_filters; // 20
meta.batch.max_writes; // 100
meta.media.max_upload_bytes; // deployment setting
```

Current public contract:

| Area | Limit |
| --- | --- |
| Collection name | `^[A-Za-z][A-Za-z0-9_-]{0,63}$`, max 64 chars |
| Document ID | `^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$`, max 128 chars |
| List/query page size | default 50, max 500 |
| Query filters | max 20 `where` filters |
| Batch writes | max 100 writes, atomic |
| API keys | max 25 active keys per user |
| Upload size | `meta.media.max_upload_bytes` |

There is no explicit per-user DB HTTP rate limit yet. Auth login has an IP
limit of 10 challenge/verify requests per 5 minutes. Storage is also limited by
Telegram MTProto throttling, exposed in `meta.rate_limits.telegram_mtproto`.

### Query

```ts
await posts.query({
  where: [
    { field: "status", op: "==", value: "published" },
    { field: "score", op: ">=", value: 10 },
  ],
  order_by: "score",
  order: "desc",
  limit: 50,
});
```

Supported operators:

- `==`
- `!=`
- `<`
- `<=`
- `>`
- `>=`
- `contains`
- `startsWith`

## Error handling

```ts
import { LCloudDbError } from "@lcloud/db";

try {
  await db.collection("users").get("missing");
} catch (error) {
  if (error instanceof LCloudDbError) {
    console.error(error.status, error.reason, error.detail);
  }
}
```

## Security

Do not put API keys into public frontend bundles. Use API keys from server-side
code, CLIs, workers, or trusted automation. Browser apps should use an existing
LCloud web session or call your own backend.
