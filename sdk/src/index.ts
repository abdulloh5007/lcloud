import { LCloudBrowserClient, LCloudDbClient } from "./clients/index.js";
import type { LCloudDbOptions, LCloudPublicClientOptions } from "./types.js";

export * from "./clients/index.js";
export * from "./auth.js";
export * from "./errors.js";
export * from "./refs/index.js";
export * from "./types.js";

export function createClient(options: LCloudDbOptions): LCloudDbClient {
  return new LCloudDbClient(options);
}

/**
 * Public browser/serverless client. This is the Supabase/Firebase-style
 * frontend mode for LCloud DB: no API key, no cookies, no server proxy.
 *
 * It can call `meta()` and `publicCollection(collectionId)` only when the
 * collection's rules allow public read/write. Configure those rules once from
 * DB Console or a trusted server-side `createClient({ apiKey })`.
 */
export function createPublicClient(
  options: LCloudPublicClientOptions,
): LCloudBrowserClient {
  return new LCloudBrowserClient(options);
}

export function createBrowserClient(
  options: LCloudPublicClientOptions & { publishableKey?: string; storageKey?: string },
): LCloudBrowserClient {
  return new LCloudBrowserClient(options);
}
