# Publishing `@lcloud/db`

This guide prepares the JavaScript SDK for npm publishing.

## Package

Package directory:

```bash
cd sdk
```

Package name:

```text
@lcloud/db
```

If the `@lcloud` npm scope is not available to this project, rename the package
to `lcloud-db` before publishing:

```json
{
  "name": "lcloud-db"
}
```

## Pre-publish checks

```bash
cd sdk
npm install
npm run build
npm run pack:check
```

`npm run pack:check` must show only intended package files, including:

```text
README.md
dist/cli.d.ts
dist/cli.js
dist/index.d.ts
dist/index.js
package.json
```

## Publish

For a public scoped package:

```bash
npm login
npm publish --access public
```

For an unscoped package:

```bash
npm login
npm publish
```

## Versioning

Patch release:

```bash
cd sdk
npm version patch
npm publish --access public
```

Minor release:

```bash
cd sdk
npm version minor
npm publish --access public
```

Major release:

```bash
cd sdk
npm version major
npm publish --access public
```

## Release checklist

- `npm run build` passes.
- `npm run pack:check` includes only intended files.
- `docs/LCLOUD_DB.md` matches SDK methods.
- `docs/LCLOUD_DB_AI.md` has current examples.
- `node dist/cli.js doctor --endpoint https://tg-lcloud.duckdns.org` works.
- `node dist/cli.js check .` works.
- No API keys, `.env`, `node_modules`, or local build trash are committed.
- Tag the repo after publishing:

```bash
git tag sdk-v0.1.0
git push origin sdk-v0.1.0
```

