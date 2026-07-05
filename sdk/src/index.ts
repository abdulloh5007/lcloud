export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export type JsonObject = { [key: string]: JsonValue };

export type WhereOp =
  | "=="
  | "!="
  | "<"
  | "<="
  | ">"
  | ">="
  | "contains"
  | "startsWith";

export interface LCloudDbOptions {
  endpoint: string;
  apiKey?: string;
  fetch?: typeof fetch;
}

export interface CollectionRow {
  id: number;
  name: string;
  owner_user_id: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface DocumentRow<T extends JsonObject = JsonObject> {
  id: string;
  collection_id: number;
  data: T;
  version: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface QueryWhere {
  field: string;
  op?: WhereOp;
  value: JsonValue;
}

export interface QueryInput {
  where?: QueryWhere[];
  order_by?: string;
  order?: "asc" | "desc";
  limit?: number;
  offset?: number;
}

export class LCloudDbError extends Error {
  constructor(
    public status: number,
    public reason: string,
    public detail: unknown,
  ) {
    super(`LCloud DB ${status}: ${reason}`);
  }
}

export function createClient(options: LCloudDbOptions): LCloudDbClient {
  return new LCloudDbClient(options);
}

export class LCloudDbClient {
  private readonly endpoint: string;
  private readonly apiKey?: string;
  private readonly fetchImpl: typeof fetch;

  constructor(options: LCloudDbOptions) {
    this.endpoint = options.endpoint.replace(/\/+$/, "");
    this.apiKey = options.apiKey;
    this.fetchImpl = options.fetch ?? fetch;
  }

  collection<T extends JsonObject = JsonObject>(name: string): CollectionRef<T> {
    return new CollectionRef<T>(this, name);
  }

  async createCollection(name: string): Promise<CollectionRow> {
    return this.request("/api/v1/db/collections", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
  }

  async ensureCollection(name: string): Promise<CollectionRow> {
    try {
      return await this.createCollection(name);
    } catch (error) {
      if (error instanceof LCloudDbError && error.reason === "collection_exists") {
        const found = (await this.listCollections()).find((row) => row.name === name);
        if (found) return found;
      }
      throw error;
    }
  }

  async listCollections(): Promise<CollectionRow[]> {
    return this.request("/api/v1/db/collections");
  }

  async deleteCollection(name: string): Promise<void> {
    await this.request<void>(`/api/v1/db/collections/${encodePath(name)}`, {
      method: "DELETE",
    });
  }

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    if (!headers.has("Content-Type") && init.body !== undefined) {
      headers.set("Content-Type", "application/json");
    }
    if (this.apiKey) {
      headers.set("Authorization", `Bearer ${this.apiKey}`);
    }
    const response = await this.fetchImpl(`${this.endpoint}${path}`, {
      credentials: "include",
      ...init,
      headers,
    });
    if (!response.ok) {
      let detail: unknown = await response.text();
      try {
        detail = JSON.parse(String(detail));
      } catch {
        // keep text detail
      }
      const reason =
        typeof detail === "object" &&
        detail !== null &&
        "detail" in detail &&
        typeof detail.detail === "object" &&
        detail.detail !== null &&
        "reason" in detail.detail
          ? String(detail.detail.reason)
          : `http_${response.status}`;
      throw new LCloudDbError(response.status, reason, detail);
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }
}

function encodePath(value: string): string {
  return encodeURIComponent(value);
}

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
    const qs = new URLSearchParams();
    if (input.limit !== undefined) qs.set("limit", String(input.limit));
    if (input.offset !== undefined) qs.set("offset", String(input.offset));
    const query = qs.toString();
    return this.client.request<Page<DocumentRow<T>>>(
      `${this.path}${query ? `?${query}` : ""}`,
    );
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
