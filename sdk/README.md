# @lcloud/db

TypeScript client for LCloud DB, a JSON document database API built into
LCloud.

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

