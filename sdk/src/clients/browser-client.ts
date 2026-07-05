import { LCloudDbClient } from "./db-client.js";
import { PublicKeyCollectionRef } from "../refs/collections.js";
import { PublicStorageRef } from "../refs/storage.js";
import type {
  JsonObject,
  LCloudDbMeta,
  LCloudPublicClientOptions,
} from "../types.js";

export class LCloudBrowserClient {
  private readonly client: LCloudDbClient;
  private readonly publishableKey?: string;
  private readonly storageKey?: string;

  constructor(options: LCloudPublicClientOptions) {
    this.client = new LCloudDbClient({ ...options, credentials: "omit" });
    this.publishableKey = options.publishableKey;
    this.storageKey = options.storageKey;
  }

  collection<T extends JsonObject = JsonObject>(name: string): PublicKeyCollectionRef<T> {
    if (!this.publishableKey) {
      throw new Error("publishableKey is required for collection(name)");
    }
    return new PublicKeyCollectionRef<T>(this.client, this.publishableKey, name);
  }

  publicCollection<T extends JsonObject = JsonObject>(collectionId: number) {
    return this.client.publicCollection<T>(collectionId);
  }

  storage(storageKey = this.storageKey): PublicStorageRef {
    if (!storageKey) {
      throw new Error("storageKey is required for storage()");
    }
    return new PublicStorageRef(this.client, storageKey);
  }

  async meta(): Promise<LCloudDbMeta> {
    return this.client.meta();
  }

  url(path: string): string {
    return this.client.url(path);
  }
}
