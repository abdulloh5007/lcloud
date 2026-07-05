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

await db.ensureCollection("users");

const users = db.collection("users");

await users.insert({ name: "Alice", role: "admin" }, "alice");
await users.update("alice", { online: true });

const admins = await users.query({
  where: [{ field: "role", op: "==", value: "admin" }],
  limit: 20,
});

console.log(admins.items);

const clouds = await db.listClouds();
const uploaded = await db.cloud(clouds[0].id).upload(
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
await db.createCollection("posts");
await db.ensureCollection("posts");
await db.listCollections();
await db.deleteCollection("posts");
```

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
