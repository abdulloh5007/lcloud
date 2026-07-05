# @lcloud/db

TypeScript client for LCloud DB, a JSON document database API built into
LCloud. The SDK also includes LCloud media storage helpers, so apps can store
JSON documents and upload files/media through one client.

```bash
npm install @lcloud/db
```

## CLI

The package includes a small terminal helper for setup, health checks, upgrade
checks, and AI-agent audits:

```bash
npx @lcloud/db doctor --endpoint https://tg-lcloud.duckdns.org --key lcpk_... --collection posts
npx @lcloud/db init --endpoint https://tg-lcloud.duckdns.org --key lcpk_... --collection posts
npx @lcloud/db upgrade
npx @lcloud/db check . --strict
```

Commands:

| Command | Use |
| --- | --- |
| `doctor` | Checks installed/latest SDK version, `_meta`, live limits, rate limits, and optional publishable-key collection access. |
| `init` | Writes `.env.example` and `lcloud-db.example.ts` for browser-only usage. |
| `upgrade` | Shows the correct `npm`/`pnpm`/`yarn`/`bun` command for the newest SDK; add `--run` to execute it. |
| `check` | Scans a project for unsafe frontend owner keys and local JSON fallback patterns. |

`doctor` reads `LCLOUD_ENDPOINT`, `VITE_LCLOUD_ENDPOINT`, `LCLOUD_DB_KEY`,
`VITE_LCLOUD_DB_KEY`, `LCLOUD_COLLECTION`, and `VITE_LCLOUD_COLLECTION` when
flags are omitted.

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

## Two modes: server/admin and browser-only

LCloud works like Supabase/Firebase in this important way:

- trusted setup code uses a secret API key to create collections, rules, and
  publishable DB keys;
- public frontend code uses a publishable DB key plus collection rules, not a
  secret key.

Do **not** put `LCLOUD_API_KEY` in a frontend `.env` file. Frontend env vars are
compiled into the browser bundle by tools like Vite and Next.js. LCloud API keys
are owner secrets, unlike a Supabase anon key.

### Server/admin setup

Run this from a server, CLI, CI job, or local admin script:

```ts
import { createClient } from "@lcloud/db";

const admin = createClient({
  endpoint: process.env.LCLOUD_ENDPOINT!,
  apiKey: process.env.LCLOUD_API_KEY!,
});

await admin.ensureCollection("posts");
const publicKey = await admin.createPublicKey("website");
const rules = await admin.collection("posts").setRules({
  read: "public",
  write: "owner",
});

console.log("Publishable key:", publicKey.key);
console.log("Public collection id:", rules.collection_id);
```

For anonymous browser writes, open writes deliberately and add a validator:

```ts
await admin.collection("contact_forms").setRules({
  read: "owner",
  write: "public",
});
await admin.collection("contact_forms").setValidator({
  max_bytes: 2048,
  max_fields: 3,
  required_fields: ["email", "message"],
  allowed_fields: ["email", "message", "source"],
});
```

### Browser-only / static website

Use this in a plain HTML/Vite/Next/static frontend with no backend:

```ts
import { createBrowserClient } from "@lcloud/db";

const lcloud = createBrowserClient({
  endpoint: import.meta.env.VITE_LCLOUD_ENDPOINT,
  publishableKey: import.meta.env.VITE_LCLOUD_DB_KEY,
});

const posts = lcloud.collection("posts");
const page = await posts.list({ limit: 20 });

await lcloud.collection("contact_forms").insert({
  email,
  message,
  source: "landing-page",
});
```

The LCloud server must allow your site origin with `LC_CORS_ALLOW_ORIGINS`, for
example:

```env
LC_CORS_ALLOW_ORIGINS=https://my-site.com,https://www.my-site.com
```

For public read-only content set `{ read: "public", write: "owner" }`.
For public forms set `{ read: "owner", write: "public" }` plus a validator.
Do not create a local JSON fallback unless the user explicitly asks for offline
mock data; the remote LCloud public collection is the source of truth.

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
- `credentials`: optional fetch credentials mode. Defaults to `"include"`.

### `createBrowserClient(options)`

```ts
const lcloud = createBrowserClient({
  endpoint: "https://your-lcloud-host",
  publishableKey: "lcpk_...",
});
```

Use this for static frontend/serverless apps. It sends no cookies and no API
key, so it only works with collections whose rules allow public read/write.

### `createPublicClient(options)`

Lower-level public client. Use it with `publicCollection(collectionId)` when
you already know the numeric collection ID.

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
await posts.setValidator({
  max_bytes: 2048,
  required_fields: ["email"],
  allowed_fields: ["email", "message"],
});
const rules = await posts.getRules();
const validator = await posts.getValidator();

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

Public write validators apply to `db.publicCollection(id).insert/set/update()`:

| Validator field | Meaning |
| --- | --- |
| `max_bytes` | Max serialized JSON document size |
| `max_fields` | Max number of top-level fields |
| `required_fields` | Top-level fields that must exist |
| `allowed_fields` | Reject top-level fields outside this list |

Public routes are rate-limited per IP. Read limit and write limit are exposed
by `await db.meta()`.

### Realtime

Watch owner/cookie-access collections:

```ts
const source = db.collection("posts").watch((event) => {
  console.log(event.op, event.doc_id, event.payload);
});

source.close();
```

Watch public collections:

```ts
const source = db.publicCollection(rules.collection_id).watch((event) => {
  console.log(event.op, event.doc_id);
});
```

Realtime uses Server-Sent Events. Browser `EventSource` cannot send custom
Authorization headers, so API-key clients should use it from same-origin cookie
sessions or public collections.

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
LCloud web session, a public collection via `createPublicClient()`, or your own
backend.
