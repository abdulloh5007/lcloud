#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";

type Flags = Record<string, string | boolean>;
type PackageJson = { name: string; version: string };
type MetaShape = {
  name?: string;
  version?: string;
  access_rules?: {
    publishable_key_prefix?: string;
    publishable_key_path?: string;
    public_read_rate_limit?: { capacity: number; window_seconds: number; key: string };
    public_write_rate_limit?: { capacity: number; window_seconds: number; key: string };
    max_publishable_keys_per_user?: number;
  };
  pagination?: { max_limit?: number; default_limit?: number };
  batch?: { max_writes?: number };
  media?: { max_upload_bytes?: number };
};

type CheckIssue = { file: string; message: string; level: "warn" | "fail" };

const pkg = JSON.parse(
  readFileSync(new URL("../package.json", import.meta.url), "utf8"),
) as PackageJson;
const args = process.argv.slice(2);
const command = !args[0]
  ? "help"
  : args[0] === "--version" || args[0] === "-v"
    ? args[0]
    : args[0].startsWith("-")
      ? "help"
      : args[0];
const commandArgs = command === "help" ? args : args.slice(1);
const flags = parseFlags(commandArgs);
const colors = process.stdout.isTTY && !flags["no-color"];

const paint = {
  dim: (value: string) => color(value, "\u001b[2m"),
  green: (value: string) => color(value, "\u001b[32m"),
  yellow: (value: string) => color(value, "\u001b[33m"),
  red: (value: string) => color(value, "\u001b[31m"),
  cyan: (value: string) => color(value, "\u001b[36m"),
  bold: (value: string) => color(value, "\u001b[1m"),
};

function color(value: string, code: string): string {
  if (!colors) return value;
  return `${code}${value}\u001b[0m`;
}

async function main(): Promise<void> {
  if (flags.help || flags.h) {
    printHelp(command === "help" ? undefined : command);
    return;
  }

  switch (command) {
    case "doctor":
      await doctor(flags);
      return;
    case "init":
      init(flags);
      return;
    case "upgrade":
      await upgrade(flags);
      return;
    case "check":
      check(commandArgs.filter((arg) => !arg.startsWith("-"))[0] ?? ".", flags);
      return;
    case "help":
      printHelp();
      return;
    case "version":
    case "--version":
    case "-v":
      console.log(`${pkg.name} ${pkg.version}`);
      return;
    default:
      console.error(paint.red(`Unknown command: ${command}`));
      printHelp();
      process.exitCode = 1;
  }
}

async function doctor(options: Flags): Promise<void> {
  banner("LCloud DB doctor");
  let failed = false;
  const endpoint = stringFlag(options.endpoint) ?? env("LCLOUD_ENDPOINT") ?? env("VITE_LCLOUD_ENDPOINT");
  const publishableKey =
    stringFlag(options.key) ?? env("LCLOUD_DB_KEY") ?? env("VITE_LCLOUD_DB_KEY");
  const collection =
    stringFlag(options.collection) ?? env("LCLOUD_COLLECTION") ?? env("VITE_LCLOUD_COLLECTION");

  line("SDK", `${pkg.name}@${pkg.version}`);
  const latest = await latestNpmVersion();
  if (latest) {
    const current = compareVersions(pkg.version, latest);
    if (current < 0) {
      warn(`npm has ${latest}. Run: npx @lcloud/db upgrade`);
    } else if (current > 0) {
      warn(`local package is ahead of npm (npm latest is ${latest}); publish this version before telling users to upgrade`);
    } else {
      ok(`npm version is current (${latest})`);
    }
  } else {
    warn("Could not read npm registry latest version");
  }

  if (env("VITE_LCLOUD_API_KEY") || env("NEXT_PUBLIC_LCLOUD_API_KEY")) {
    warn("Frontend env contains an owner API key variable. Use VITE_LCLOUD_DB_KEY=lcpk_... instead.");
  }

  if (!endpoint) {
    warn("No endpoint provided. Use --endpoint or LCLOUD_ENDPOINT/VITE_LCLOUD_ENDPOINT.");
    return;
  }

  const base = endpoint.replace(/\/+$/, "");
  line("Endpoint", base);
  const metaResult = await fetchJson<MetaShape>(`${base}/api/v1/db/_meta`);
  if (!metaResult.ok) {
    fail(`_meta failed: ${metaResult.error}`);
    process.exitCode = 1;
    return;
  }

  const meta = metaResult.data;
  ok(`_meta is reachable (${meta.name ?? "LCloud DB"} ${meta.version ?? "unknown"})`);
  if (meta.pagination?.max_limit) line("Page max", String(meta.pagination.max_limit));
  if (meta.batch?.max_writes) line("Batch max", String(meta.batch.max_writes));
  if (meta.media?.max_upload_bytes) line("Upload max", `${meta.media.max_upload_bytes} bytes`);
  const readLimit = meta.access_rules?.public_read_rate_limit;
  const writeLimit = meta.access_rules?.public_write_rate_limit;
  if (readLimit) line("Public read", `${readLimit.capacity}/${readLimit.window_seconds}s by ${readLimit.key}`);
  if (writeLimit) line("Public write", `${writeLimit.capacity}/${writeLimit.window_seconds}s by ${writeLimit.key}`);

  if (!publishableKey) {
    warn("No publishable key provided. Use --key or VITE_LCLOUD_DB_KEY to test browser mode.");
  } else if (!publishableKey.startsWith("lcpk_")) {
    fail("Publishable key must start with lcpk_. Do not use lc- owner API keys in browser mode.");
    failed = true;
  }

  if (publishableKey && collection) {
    const url = `${base}/api/v1/public/db/key/${encodeURIComponent(publishableKey)}/${encodeURIComponent(collection)}?limit=1`;
    const publicResult = await fetchJson<unknown>(url);
    if (publicResult.ok) {
      ok(`Public collection '${collection}' works with this publishable key`);
    } else {
      fail(`Public collection check failed: ${publicResult.error}`);
      failed = true;
    }
  } else {
    warn("Skip collection check. Provide --key and --collection to verify browser access.");
  }

  if (failed) process.exitCode = 1;
}

function init(options: Flags): void {
  banner("LCloud DB init");
  const endpoint =
    stringFlag(options.endpoint) ?? env("LCLOUD_ENDPOINT") ?? "https://tg-lcloud.duckdns.org";
  const key = stringFlag(options.key) ?? env("LCLOUD_DB_KEY") ?? "lcpk_your_publishable_key";
  const collection = stringFlag(options.collection) ?? env("LCLOUD_COLLECTION") ?? "posts";
  const force = Boolean(options.force || options.f);
  const envFile = stringFlag(options.file) ?? ".env.example";
  const sampleFile = stringFlag(options.sample) ?? "lcloud-db.example.ts";

  writeNewFile(
    envFile,
    [
      `VITE_LCLOUD_ENDPOINT=${endpoint}`,
      `VITE_LCLOUD_DB_KEY=${key}`,
      `VITE_LCLOUD_COLLECTION=${collection}`,
      "",
    ].join("\n"),
    force,
  );
  writeNewFile(
    sampleFile,
    [
      'import { createBrowserClient } from "@lcloud/db";',
      "",
      "const lcloud = createBrowserClient({",
      "  endpoint: import.meta.env.VITE_LCLOUD_ENDPOINT,",
      "  publishableKey: import.meta.env.VITE_LCLOUD_DB_KEY,",
      "});",
      "",
      "export const collection = lcloud.collection(import.meta.env.VITE_LCLOUD_COLLECTION);",
      "",
      "export async function listDocuments() {",
      "  return collection.list({ limit: 20 });",
      "}",
      "",
    ].join("\n"),
    force,
  );
  note("Use a DB Console publishable key (lcpk_...). Never put lc- owner API keys in frontend env files.");
}

async function upgrade(options: Flags): Promise<void> {
  banner("LCloud DB upgrade");
  const latest = await latestNpmVersion();
  if (!latest) {
    fail("Could not read latest npm version from registry.");
    process.exitCode = 1;
    return;
  }

  line("Installed", pkg.version);
  line("Latest", latest);
  const current = compareVersions(pkg.version, latest);
  if (current > 0) {
    warn(`local package is ahead of npm (npm latest is ${latest}); publish this version first`);
    return;
  }
  if (current === 0) {
    ok("Already up to date");
    return;
  }

  const pm = stringFlag(options.pm) ?? detectPackageManager();
  const commandParts = installCommand(pm, latest);
  line("Command", commandParts.join(" "));
  if (!options.run) {
    note("Add --run to execute the upgrade command automatically.");
    return;
  }

  const [bin, ...rest] = commandParts;
  const result = spawnSync(bin, rest, { stdio: "inherit", shell: process.platform === "win32" });
  if (result.status !== 0) process.exitCode = result.status ?? 1;
}

function check(target: string, options: Flags): void {
  banner("LCloud DB check");
  const strict = Boolean(options.strict);
  const root = join(process.cwd(), target);
  const issues: CheckIssue[] = [];
  if (!existsSync(root)) {
    fail(`Path does not exist: ${target}`);
    process.exitCode = 1;
    return;
  }

  for (const file of listFiles(root)) {
    const rel = relative(process.cwd(), file) || file;
    if (rel.endsWith(".md") || rel === "src/cli.ts") continue;
    const text = readFileSafe(file);
    if (!text) continue;
    if (/VITE_LCLOUD_API_KEY|NEXT_PUBLIC_LCLOUD_API_KEY|PUBLIC_LCLOUD_API_KEY/.test(text)) {
      issues.push({ file: rel, level: "fail", message: "frontend env exposes owner API key; use VITE_LCLOUD_DB_KEY=lcpk_..." });
    }
    if (/createClient\s*\([^)]*apiKey\s*:/s.test(text) && /import\.meta\.env|NEXT_PUBLIC_|VITE_/.test(text)) {
      issues.push({ file: rel, level: "fail", message: "browser code appears to use createClient(apiKey); use createBrowserClient({ publishableKey })" });
    }
    if (/lc-[A-Za-z0-9_-]{10,}/.test(text) && !rel.endsWith(".md")) {
      issues.push({ file: rel, level: "fail", message: "possible hard-coded owner API key" });
    }
    if (/local\s+json\s+fallback|json\s+fallback|fallback\s+to\s+json|fallbackDb|localJson/i.test(text)) {
      issues.push({ file: rel, level: "warn", message: "possible local JSON fallback; LCloud should stay the source of truth" });
    }
  }

  if (issues.length === 0) {
    ok("No common LCloud integration problems found");
    return;
  }

  for (const issue of issues) {
    const prefix = issue.level === "fail" ? paint.red("FAIL") : paint.yellow("WARN");
    console.log(`${prefix} ${issue.file}: ${issue.message}`);
  }
  if (strict && issues.some((issue) => issue.level === "fail")) process.exitCode = 1;
}

function printHelp(scope?: string): void {
  if (!scope || scope === "help") {
    console.log(`\n${paint.bold("@lcloud/db CLI")} ${paint.dim(`v${pkg.version}`)}\n\nUsage:\n  npx @lcloud/db doctor --endpoint <url> --key <lcpk_...> --collection <name>\n  npx @lcloud/db init --endpoint <url> --key <lcpk_...> --collection <name>\n  npx @lcloud/db upgrade [--run]\n  npx @lcloud/db check [path] [--strict]\n\nCommands:\n  doctor   Check SDK version, endpoint _meta, limits, rate limits, and publishable key access\n  init     Create .env.example and a browser client sample\n  upgrade  Show or run the package-manager command for the latest SDK\n  check    Scan a project for unsafe frontend keys and local JSON fallbacks\n`);
    return;
  }
  console.log(`Run: npx @lcloud/db ${scope} --help`);
}

function parseFlags(values: string[]): Flags {
  const result: Flags = {};
  for (let i = 0; i < values.length; i += 1) {
    const value = values[i];
    if (!value.startsWith("--")) continue;
    const raw = value.slice(2);
    const [key, inline] = raw.split("=", 2);
    if (inline !== undefined) {
      result[key] = inline;
      continue;
    }
    const next = values[i + 1];
    if (next && !next.startsWith("-")) {
      result[key] = next;
      i += 1;
    } else {
      result[key] = true;
    }
  }
  return result;
}

function stringFlag(value: string | boolean | undefined): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function env(name: string): string | undefined {
  const value = process.env[name];
  return value && value.trim() ? value.trim() : undefined;
}

async function latestNpmVersion(): Promise<string | null> {
  const result = await fetchJson<{ version?: string }>("https://registry.npmjs.org/@lcloud%2fdb/latest");
  return result.ok && result.data.version ? result.data.version : null;
}

async function fetchJson<T>(url: string): Promise<{ ok: true; data: T } | { ok: false; error: string }> {
  if (typeof fetch === "undefined") return { ok: false, error: "global fetch is unavailable; use Node.js 18+" };
  try {
    const response = await fetch(url, { headers: { Accept: "application/json" } });
    const text = await response.text();
    if (!response.ok) return { ok: false, error: `HTTP ${response.status}: ${text.slice(0, 240)}` };
    return { ok: true, data: JSON.parse(text) as T };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}

function detectPackageManager(): string {
  if (existsSync("pnpm-lock.yaml")) return "pnpm";
  if (existsSync("yarn.lock")) return "yarn";
  if (existsSync("bun.lockb") || existsSync("bun.lock")) return "bun";
  return "npm";
}

function installCommand(pm: string, latest: string): string[] {
  const spec = `@lcloud/db@${latest}`;
  if (pm === "pnpm") return ["pnpm", "add", spec];
  if (pm === "yarn") return ["yarn", "add", spec];
  if (pm === "bun") return ["bun", "add", spec];
  return ["npm", "install", spec];
}

function compareVersions(left: string, right: string): number {
  const a = left.split(/[.-]/).map((part) => Number.parseInt(part, 10) || 0);
  const b = right.split(/[.-]/).map((part) => Number.parseInt(part, 10) || 0);
  for (let i = 0; i < Math.max(a.length, b.length); i += 1) {
    const diff = (a[i] ?? 0) - (b[i] ?? 0);
    if (diff !== 0) return diff;
  }
  return 0;
}

function writeNewFile(path: string, content: string, force: boolean): void {
  if (existsSync(path) && !force) {
    warn(`${path} already exists; use --force to overwrite`);
    return;
  }
  writeFileSync(path, content, "utf8");
  ok(`Wrote ${path}`);
}

function listFiles(root: string): string[] {
  const files: string[] = [];
  const ignored = new Set([".git", "node_modules", "dist", "build", "coverage", ".next", ".nuxt", ".turbo", ".vercel"]);
  const allowed = /\.(ts|tsx|js|jsx|mjs|cjs|vue|svelte|env|env\.example|json)$/;
  function walk(path: string): void {
    const st = statSync(path);
    if (st.isDirectory()) {
      const name = path.split(/[\\/]/).pop() ?? "";
      if (ignored.has(name)) return;
      for (const entry of readdirSync(path)) walk(join(path, entry));
      return;
    }
    if (st.isFile() && st.size <= 1_000_000 && allowed.test(path)) files.push(path);
  }
  walk(root);
  return files;
}

function readFileSafe(path: string): string | null {
  try {
    return readFileSync(path, "utf8");
  } catch {
    return null;
  }
}

function banner(title: string): void {
  console.log(`${paint.cyan("LCloud")} ${paint.bold(title)}`);
}

function line(label: string, value: string): void {
  console.log(`${paint.dim(label.padEnd(14))} ${value}`);
}

function ok(message: string): void {
  console.log(`${paint.green("OK")} ${message}`);
}

function warn(message: string): void {
  console.log(`${paint.yellow("WARN")} ${message}`);
}

function fail(message: string): void {
  console.log(`${paint.red("FAIL")} ${message}`);
}

function note(message: string): void {
  console.log(`${paint.dim("NOTE")} ${message}`);
}

void main();
