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

export interface CloudRow {
  id: number;
  chat_id: number;
  name: string;
  owner_user_id?: number | null;
  created_at: string | null;
}

export interface FileRow {
  id: number;
  cloud_id: number;
  message_id: number;
  owner_user_id?: number | null;
  name: string;
  mime: string;
  size: number;
  compressed?: boolean;
  original_size_bytes?: number | null;
  caption_kind?: "LC1" | "LC2";
  uploaded_at: string | null;
  deleted_at?: string | null;
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

export interface ListInput {
  limit?: number;
  offset?: number;
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

export type BatchWrite<T extends JsonObject = JsonObject> =
  | { op: "create"; id?: string; data: T }
  | { op: "set"; id: string; data: T }
  | { op: "update"; id: string; data: Partial<T> }
  | { op: "delete"; id: string };

export interface BatchWriteResult<T extends JsonObject = JsonObject> {
  index: number;
  op: BatchWrite<T>["op"];
  id: string;
  document: DocumentRow<T> | null;
}

export interface BatchResult<T extends JsonObject = JsonObject> {
  items: BatchWriteResult<T>[];
  total: number;
}

export interface UploadProgress {
  loaded: number;
  total: number;
  percent: number;
  phase: "uploading";
}

export interface Lc2UploadFields {
  clientSha256: string;
  signature: string;
  ts: number;
}

export interface UploadMediaOptions {
  name?: string;
  compress?: boolean;
  lc2?: Lc2UploadFields;
  onProgress?: (progress: UploadProgress) => void;
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

  cloud(id: number): CloudRef {
    return new CloudRef(this, id);
  }

  file(id: number): FileRef {
    return new FileRef(this, id);
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

  async listClouds(): Promise<CloudRow[]> {
    return this.request("/api/v1/clouds");
  }

  async createCloud(name: string): Promise<CloudRow> {
    return this.request("/api/v1/clouds", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
  }

  async ensureCloud(name: string): Promise<CloudRow> {
    const found = (await this.listClouds()).find((row) => row.name === name);
    if (found) return found;
    return this.createCloud(name);
  }

  async deleteCloud(id: number): Promise<void> {
    await this.request<void>(`/api/v1/clouds/${id}`, { method: "DELETE" });
  }

  async listFiles(cloudId: number, input: ListInput = {}): Promise<Page<FileRow>> {
    const qs = new URLSearchParams();
    if (input.limit !== undefined) qs.set("limit", String(input.limit));
    if (input.offset !== undefined) qs.set("offset", String(input.offset));
    const query = qs.toString();
    return this.request<Page<FileRow>>(
      `/api/v1/clouds/${cloudId}/files${query ? `?${query}` : ""}`,
    );
  }

  async uploadFile(
    cloudId: number,
    file: Blob,
    options: UploadMediaOptions = {},
  ): Promise<FileRow> {
    const fd = new FormData();
    const name =
      options.name ??
      ("name" in file && typeof file.name === "string" ? file.name : "upload.bin");
    fd.append("file", file, name);
    fd.append("compress", String(options.compress ?? true));
    if (options.lc2) {
      fd.append("client_sha256", options.lc2.clientSha256);
      fd.append("signature", options.lc2.signature);
      fd.append("ts", String(options.lc2.ts));
    }

    const path = `/api/v1/clouds/${cloudId}/files`;
    if (options.onProgress && typeof XMLHttpRequest !== "undefined") {
      return this.uploadWithXhr(path, fd, options.onProgress);
    }
    return this.request<FileRow>(path, {
      method: "POST",
      body: fd,
    });
  }

  async deleteFile(id: number): Promise<void> {
    await this.request<void>(`/api/v1/files/${id}`, { method: "DELETE" });
  }

  fileDownloadUrl(id: number): string {
    return this.url(`/api/v1/files/${id}/download`);
  }

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    if (!headers.has("Content-Type") && init.body !== undefined) {
      if (!isFormData(init.body)) headers.set("Content-Type", "application/json");
    }
    if (this.apiKey) {
      headers.set("Authorization", `Bearer ${this.apiKey}`);
    }
    const response = await this.fetchImpl(this.url(path), {
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

  url(path: string): string {
    return `${this.endpoint}${path}`;
  }

  private uploadWithXhr(
    path: string,
    body: FormData,
    onProgress: (progress: UploadProgress) => void,
  ): Promise<FileRow> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", this.url(path));
      xhr.withCredentials = true;
      if (this.apiKey) xhr.setRequestHeader("Authorization", `Bearer ${this.apiKey}`);
      xhr.upload.addEventListener("progress", (event) => {
        if (!event.lengthComputable) return;
        onProgress({
          loaded: event.loaded,
          total: event.total,
          percent: event.total ? (event.loaded / event.total) * 100 : 0,
          phase: "uploading",
        });
      });
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText) as FileRow);
          } catch (error) {
            reject(error);
          }
          return;
        }
        reject(parseXhrError(xhr));
      };
      xhr.onerror = () => reject(new LCloudDbError(0, "network_error", null));
      xhr.send(body);
    });
  }
}

function encodePath(value: string): string {
  return encodeURIComponent(value);
}

function isFormData(body: unknown): boolean {
  return typeof FormData !== "undefined" && body instanceof FormData;
}

function parseXhrError(xhr: XMLHttpRequest): LCloudDbError {
  let detail: unknown = xhr.responseText;
  try {
    detail = JSON.parse(xhr.responseText);
  } catch {
    // keep text response
  }
  const reason =
    typeof detail === "object" &&
    detail !== null &&
    "detail" in detail &&
    typeof detail.detail === "object" &&
    detail.detail !== null &&
    "reason" in detail.detail
      ? String(detail.detail.reason)
      : `http_${xhr.status}`;
  return new LCloudDbError(xhr.status, reason, detail);
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

  async batch(writes: BatchWrite<T>[]): Promise<BatchResult<T>> {
    return this.client.request<BatchResult<T>>(`${this.path}/batch`, {
      method: "POST",
      body: JSON.stringify({ writes }),
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

export class CloudRef {
  constructor(
    private readonly client: LCloudDbClient,
    private readonly id: number,
  ) {}

  async listFiles(input: ListInput = {}): Promise<Page<FileRow>> {
    return this.client.listFiles(this.id, input);
  }

  async upload(file: Blob, options: UploadMediaOptions = {}): Promise<FileRow> {
    return this.client.uploadFile(this.id, file, options);
  }

  async delete(): Promise<void> {
    await this.client.deleteCloud(this.id);
  }
}

export class FileRef {
  constructor(
    private readonly client: LCloudDbClient,
    private readonly id: number,
  ) {}

  downloadUrl(): string {
    return this.client.fileDownloadUrl(this.id);
  }

  async delete(): Promise<void> {
    await this.client.deleteFile(this.id);
  }
}
