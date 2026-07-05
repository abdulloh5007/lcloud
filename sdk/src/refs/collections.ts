import type { LCloudDbClient } from "../clients/db-client.js";
import { watchEvents } from "../realtime.js";
import type {
  AccessRule,
  BatchResult,
  BatchWrite,
  CollectionRules,
  CollectionValidator,
  DbChangeEvent,
  DocumentRow,
  JsonObject,
  Page,
  QueryInput,
  WatchOptions,
  WriteValidator,
} from "../types.js";
import { encodePath, listQuery } from "../utils.js";

export class CollectionRef<T extends JsonObject = JsonObject> {
  constructor(
    private readonly client: LCloudDbClient,
    private readonly name: string,
  ) {}

  private get path(): string {
    return `/api/v1/db/${encodePath(this.name)}`;
  }

  doc(id: string): DocumentRef<T> {
    return new DocumentRef<T>(this.client, this.name, id);
  }

  async insert(data: T, id?: string): Promise<DocumentRow<T>> {
    return this.client.request<DocumentRow<T>>(this.path, {
      method: "POST",
      body: JSON.stringify({ id, data }),
    });
  }

  async list(input: { limit?: number; offset?: number } = {}): Promise<Page<DocumentRow<T>>> {
    return this.client.request<Page<DocumentRow<T>>>(`${this.path}${listQuery(input)}`);
  }

  async get(id: string): Promise<DocumentRow<T>> {
    return this.doc(id).get();
  }

  async set(id: string, data: T): Promise<DocumentRow<T>> {
    return this.doc(id).set(data);
  }

  async update(id: string, data: Partial<T>): Promise<DocumentRow<T>> {
    return this.doc(id).update(data);
  }

  async delete(id: string): Promise<void> {
    await this.doc(id).delete();
  }

  async query(input: QueryInput): Promise<Page<DocumentRow<T>>> {
    return this.client.request<Page<DocumentRow<T>>>(`${this.path}/query`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  async batch(writes: BatchWrite<T>[]): Promise<BatchResult<T>> {
    return this.client.request<BatchResult<T>>(`${this.path}/batch`, {
      method: "POST",
      body: JSON.stringify({ writes }),
    });
  }

  async getRules(): Promise<CollectionRules> {
    return this.client.getCollectionRules(this.name);
  }

  async setRules(rules: { read: AccessRule; write: AccessRule }): Promise<CollectionRules> {
    return this.client.setCollectionRules(this.name, rules);
  }

  async getValidator(): Promise<CollectionValidator> {
    return this.client.getCollectionValidator(this.name);
  }

  async setValidator(validator: WriteValidator): Promise<CollectionValidator> {
    return this.client.setCollectionValidator(this.name, validator);
  }

  async deleteValidator(): Promise<void> {
    await this.client.deleteCollectionValidator(this.name);
  }

  watch(onChange: (event: DbChangeEvent) => void, options: WatchOptions = {}): EventSource {
    return watchCollectionEvents(this.client, `${this.path}/events`, onChange, options);
  }
}

export class DocumentRef<T extends JsonObject = JsonObject> {
  constructor(
    private readonly client: LCloudDbClient,
    private readonly collectionName: string,
    private readonly id: string,
  ) {}

  private get path(): string {
    return `/api/v1/db/${encodePath(this.collectionName)}/${encodePath(this.id)}`;
  }

  async get(): Promise<DocumentRow<T>> {
    return this.client.request<DocumentRow<T>>(this.path);
  }

  async set(data: T): Promise<DocumentRow<T>> {
    return this.client.request<DocumentRow<T>>(this.path, {
      method: "PUT",
      body: JSON.stringify({ data }),
    });
  }

  async update(data: Partial<T>): Promise<DocumentRow<T>> {
    return this.client.request<DocumentRow<T>>(this.path, {
      method: "PATCH",
      body: JSON.stringify({ data }),
    });
  }

  async delete(): Promise<void> {
    await this.client.request<void>(this.path, { method: "DELETE" });
  }
}

export class PublicCollectionRef<T extends JsonObject = JsonObject> {
  constructor(
    private readonly client: LCloudDbClient,
    private readonly collectionId: number,
  ) {}

  private get path(): string {
    return `/api/v1/public/db/${this.collectionId}`;
  }

  async insert(data: T, id?: string): Promise<DocumentRow<T>> {
    return this.client.request<DocumentRow<T>>(this.path, {
      method: "POST",
      body: JSON.stringify({ id, data }),
    });
  }

  async list(input: { limit?: number; offset?: number } = {}): Promise<Page<DocumentRow<T>>> {
    return this.client.request<Page<DocumentRow<T>>>(`${this.path}${listQuery(input)}`);
  }

  async get(id: string): Promise<DocumentRow<T>> {
    return this.client.request<DocumentRow<T>>(`${this.path}/${encodePath(id)}`);
  }

  async set(id: string, data: T): Promise<DocumentRow<T>> {
    return this.client.request<DocumentRow<T>>(`${this.path}/${encodePath(id)}`, {
      method: "PUT",
      body: JSON.stringify({ data }),
    });
  }

  async update(id: string, data: Partial<T>): Promise<DocumentRow<T>> {
    return this.client.request<DocumentRow<T>>(`${this.path}/${encodePath(id)}`, {
      method: "PATCH",
      body: JSON.stringify({ data }),
    });
  }

  async delete(id: string): Promise<void> {
    await this.client.request<void>(`${this.path}/${encodePath(id)}`, {
      method: "DELETE",
    });
  }

  async query(input: QueryInput): Promise<Page<DocumentRow<T>>> {
    return this.client.request<Page<DocumentRow<T>>>(`${this.path}/query`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  watch(onChange: (event: DbChangeEvent) => void, options: WatchOptions = {}): EventSource {
    return watchCollectionEvents(this.client, `${this.path}/events`, onChange, options);
  }
}

export class PublicKeyCollectionRef<T extends JsonObject = JsonObject> {
  constructor(
    private readonly client: LCloudDbClient,
    private readonly publishableKey: string,
    private readonly name: string,
  ) {}

  private get path(): string {
    return `/api/v1/public/db/key/${encodePath(this.publishableKey)}/${encodePath(
      this.name,
    )}`;
  }

  async insert(data: T, id?: string): Promise<DocumentRow<T>> {
    return this.client.request<DocumentRow<T>>(this.path, {
      method: "POST",
      body: JSON.stringify({ id, data }),
    });
  }

  async list(input: { limit?: number; offset?: number } = {}): Promise<Page<DocumentRow<T>>> {
    return this.client.request<Page<DocumentRow<T>>>(`${this.path}${listQuery(input)}`);
  }

  async get(id: string): Promise<DocumentRow<T>> {
    return this.client.request<DocumentRow<T>>(`${this.path}/${encodePath(id)}`);
  }

  async set(id: string, data: T): Promise<DocumentRow<T>> {
    return this.client.request<DocumentRow<T>>(`${this.path}/${encodePath(id)}`, {
      method: "PUT",
      body: JSON.stringify({ data }),
    });
  }

  async update(id: string, data: Partial<T>): Promise<DocumentRow<T>> {
    return this.client.request<DocumentRow<T>>(`${this.path}/${encodePath(id)}`, {
      method: "PATCH",
      body: JSON.stringify({ data }),
    });
  }

  async delete(id: string): Promise<void> {
    await this.client.request<void>(`${this.path}/${encodePath(id)}`, {
      method: "DELETE",
    });
  }

  async query(input: QueryInput): Promise<Page<DocumentRow<T>>> {
    return this.client.request<Page<DocumentRow<T>>>(`${this.path}/query`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  watch(onChange: (event: DbChangeEvent) => void, options: WatchOptions = {}): EventSource {
    return watchCollectionEvents(this.client, `${this.path}/events`, onChange, options);
  }
}

function watchCollectionEvents(
  client: LCloudDbClient,
  path: string,
  onChange: (event: DbChangeEvent) => void,
  options: WatchOptions,
): EventSource {
  const qs = new URLSearchParams();
  if (options.since !== undefined) qs.set("since", String(options.since));
  const query = qs.toString();
  return watchEvents(client.url(`${path}${query ? `?${query}` : ""}`), onChange, options);
}
