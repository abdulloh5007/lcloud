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

export type AccessRule = "owner" | "authenticated" | "public";

export interface LCloudDbOptions {
  endpoint: string;
  apiKey?: string;
  fetch?: typeof fetch;
  /**
   * Defaults to "include" so same-origin LCloud web sessions work.
   * Use "omit" for public, browser-only apps hosted on another origin.
   */
  credentials?: RequestCredentials;
}

export interface LCloudPublicClientOptions {
  endpoint: string;
  publishableKey?: string;
  storageKey?: string;
  fetch?: typeof fetch;
}

export interface CollectionRow {
  id: number;
  name: string;
  owner_user_id: number;
  read_rule: AccessRule;
  write_rule: AccessRule;
  write_validator: WriteValidator | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface DbPublicKeyRow {
  id: number;
  key: string;
  prefix: string;
  label: string;
  created_at: string | null;
  revoked_at: string | null;
}

export interface StoragePublicKeyRow {
  id: number;
  cloud_id: number;
  key: string;
  prefix: string;
  label: string;
  allow_upload: boolean;
  allow_list: boolean;
  allow_download: boolean;
  allow_delete: boolean;
  max_file_bytes: number | null;
  created_at: string | null;
  revoked_at: string | null;
}

export interface CreateStoragePublicKeyInput {
  cloud_id: number;
  label?: string;
  allow_upload?: boolean;
  allow_list?: boolean;
  allow_download?: boolean;
  allow_delete?: boolean;
  max_file_bytes?: number | null;
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

export interface DbChangeEvent {
  id: number;
  collection_id: number;
  doc_id: string | null;
  op: string;
  payload: JsonObject;
  created_at: string | null;
}

export interface WatchOptions {
  since?: number;
  onError?: (event: Event) => void;
}

export interface CollectionRules {
  collection: string;
  collection_id: number;
  read: AccessRule;
  write: AccessRule;
  public_base_path: string;
}

export interface WriteValidator {
  max_bytes?: number | null;
  max_fields?: number | null;
  required_fields?: string[];
  allowed_fields?: string[];
}

export interface CollectionValidator {
  collection: string;
  collection_id: number;
  validator: WriteValidator | null;
}

export interface LCloudDbMeta {
  name: string;
  version: string;
  documents: {
    data_type: "json_object";
    recommended_max_size_bytes: number;
    patch: "shallow_top_level_merge";
    generated_id_prefix: string;
  };
  collections: {
    name_regex: string;
    name_max_length: number;
    reserved: string[];
  };
  document_ids: {
    regex: string;
    max_length: number;
  };
  pagination: {
    default_limit: number;
    max_limit: number;
    offset_min: number;
  };
  query: {
    max_where_filters: number;
    max_field_path_length: number;
    operators: WhereOp[];
    field_paths: "dot_notation";
    engine: string;
    indexes: string;
  };
  batch: {
    max_writes: number;
    operations: BatchWrite["op"][];
    atomic: boolean;
  };
  realtime: {
    transport: "sse";
    event: "lcloud.db.change";
    owner_path: string;
    public_path: string;
    cursor: string;
    query_params: string[];
    poll_seconds: number;
    batch_limit: number;
  };
  access_rules: {
    rules: AccessRule[];
    default_read: AccessRule;
    default_write: AccessRule;
    public_base_path: string;
    publishable_key_path: string;
    owner_manage_path: string;
    write_validator_path: string;
    publishable_key_manage_path: string;
    publishable_key_prefix: string;
    max_publishable_keys_per_user: number;
    public_read_rate_limit: {
      capacity: number;
      window_seconds: number;
      key: string;
    };
    public_write_rate_limit: {
      capacity: number;
      window_seconds: number;
      key: string;
    };
    write_validator: {
      max_configurable_bytes: number;
      fields: string[];
      scope: string;
    };
  };
  media: {
    max_upload_bytes: number;
    list_max_limit: number;
    default_compress: boolean;
    lc2_client_signing: string;
    publishable_storage_key_prefix?: string;
    publishable_storage_key_manage_path?: string;
    publishable_storage_key_path?: string;
    max_publishable_storage_keys_per_user?: number;
    public_storage_read_rate_limit?: {
      capacity: number;
      window_seconds: number;
      key: string;
    };
    public_storage_write_rate_limit?: {
      capacity: number;
      window_seconds: number;
      key: string;
    };
  };
  auth: {
    methods: string[];
    max_active_api_keys_per_user: number;
    api_keys_safe_for_public_browser: boolean;
    v2_login_rate_limit: {
      capacity: number;
      window_seconds: number;
      key: string;
      applies_to: string[];
    };
  };
  rate_limits: {
    db_api: string;
    storage_api: string;
    telegram_mtproto: {
      rate_per_second: number;
      burst: number;
      max_floodwait_seconds: number;
    };
  };
  not_supported_yet: string[];
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

  publicCollection<T extends JsonObject = JsonObject>(
    collectionId: number,
  ): PublicCollectionRef<T> {
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

export class LCloudDbClient {
  private readonly endpoint: string;
  private readonly apiKey?: string;
  private readonly fetchImpl: typeof fetch;
  private readonly credentials: RequestCredentials;

  constructor(options: LCloudDbOptions) {
    this.endpoint = options.endpoint.replace(/\/+$/, "");
    this.apiKey = options.apiKey;
    this.fetchImpl = options.fetch ?? fetch;
    this.credentials = options.credentials ?? "include";
  }

  collection<T extends JsonObject = JsonObject>(name: string): CollectionRef<T> {
    return new CollectionRef<T>(this, name);
  }

  publicCollection<T extends JsonObject = JsonObject>(
    collectionId: number,
  ): PublicCollectionRef<T> {
    return new PublicCollectionRef<T>(this, collectionId);
  }

  cloud(id: number): CloudRef {
    return new CloudRef(this, id);
  }

  file(id: number): FileRef {
    return new FileRef(this, id);
  }

  async meta(): Promise<LCloudDbMeta> {
    return this.request("/api/v1/db/_meta");
  }

  async createCollection(name: string): Promise<CollectionRow> {
    return this.request("/api/v1/db/collections", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
  }

  async listPublicKeys(): Promise<DbPublicKeyRow[]> {
    return this.request("/api/v1/db/public-keys");
  }

  async createPublicKey(label = ""): Promise<DbPublicKeyRow> {
    return this.request("/api/v1/db/public-keys", {
      method: "POST",
      body: JSON.stringify({ label }),
    });
  }

  async revokePublicKey(id: number): Promise<void> {
    await this.request<void>(`/api/v1/db/public-keys/${id}`, {
      method: "DELETE",
    });
  }

  async listStorageKeys(): Promise<StoragePublicKeyRow[]> {
    return this.request("/api/v1/storage/public-keys");
  }

  async createStorageKey(input: CreateStoragePublicKeyInput): Promise<StoragePublicKeyRow> {
    return this.request("/api/v1/storage/public-keys", {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  async revokeStorageKey(id: number): Promise<void> {
    await this.request<void>(`/api/v1/storage/public-keys/${id}`, {
      method: "DELETE",
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

  async getCollectionRules(name: string): Promise<CollectionRules> {
    return this.request(`/api/v1/db/collections/${encodePath(name)}/rules`);
  }

  async setCollectionRules(
    name: string,
    rules: { read: AccessRule; write: AccessRule },
  ): Promise<CollectionRules> {
    return this.request(`/api/v1/db/collections/${encodePath(name)}/rules`, {
      method: "PUT",
      body: JSON.stringify(rules),
    });
  }

  async getCollectionValidator(name: string): Promise<CollectionValidator> {
    return this.request(`/api/v1/db/collections/${encodePath(name)}/validator`);
  }

  async setCollectionValidator(
    name: string,
    validator: WriteValidator,
  ): Promise<CollectionValidator> {
    return this.request(`/api/v1/db/collections/${encodePath(name)}/validator`, {
      method: "PUT",
      body: JSON.stringify(validator),
    });
  }

  async deleteCollectionValidator(name: string): Promise<void> {
    await this.request<void>(`/api/v1/db/collections/${encodePath(name)}/validator`, {
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
      credentials: this.credentials,
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

  uploadWithXhr(
    path: string,
    body: FormData,
    onProgress: (progress: UploadProgress) => void,
  ): Promise<FileRow> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", this.url(path));
      xhr.withCredentials = this.credentials === "include";
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

function watchEvents(
  url: string,
  onChange: (event: DbChangeEvent) => void,
  options: WatchOptions = {},
): EventSource {
  if (typeof EventSource === "undefined") {
    throw new Error("EventSource is not available in this runtime");
  }
  const source = new EventSource(url, { withCredentials: true });
  source.addEventListener("lcloud.db.change", (event) => {
    onChange(JSON.parse((event as MessageEvent).data) as DbChangeEvent);
  });
  if (options.onError) {
    source.addEventListener("error", options.onError);
  }
  return source;
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
    const qs = new URLSearchParams();
    if (options.since !== undefined) qs.set("since", String(options.since));
    const query = qs.toString();
    return watchEvents(
      this.client.url(`${this.path}/events${query ? `?${query}` : ""}`),
      onChange,
      options,
    );
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
    const qs = new URLSearchParams();
    if (input.limit !== undefined) qs.set("limit", String(input.limit));
    if (input.offset !== undefined) qs.set("offset", String(input.offset));
    const query = qs.toString();
    return this.client.request<Page<DocumentRow<T>>>(
      `${this.path}${query ? `?${query}` : ""}`,
    );
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
    const qs = new URLSearchParams();
    if (options.since !== undefined) qs.set("since", String(options.since));
    const query = qs.toString();
    return watchEvents(
      this.client.url(`${this.path}/events${query ? `?${query}` : ""}`),
      onChange,
      options,
    );
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
    const qs = new URLSearchParams();
    if (input.limit !== undefined) qs.set("limit", String(input.limit));
    if (input.offset !== undefined) qs.set("offset", String(input.offset));
    const query = qs.toString();
    return this.client.request<Page<DocumentRow<T>>>(
      `${this.path}${query ? `?${query}` : ""}`,
    );
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
    const qs = new URLSearchParams();
    if (options.since !== undefined) qs.set("since", String(options.since));
    const query = qs.toString();
    return watchEvents(
      this.client.url(`${this.path}/events${query ? `?${query}` : ""}`),
      onChange,
      options,
    );
  }
}


export class PublicStorageRef {
  constructor(
    private readonly client: LCloudDbClient,
    private readonly storageKey: string,
  ) {}

  private get path(): string {
    return `/api/v1/public/storage/key/${encodePath(this.storageKey)}/files`;
  }

  async listFiles(input: ListInput = {}): Promise<Page<FileRow>> {
    const qs = new URLSearchParams();
    if (input.limit !== undefined) qs.set("limit", String(input.limit));
    if (input.offset !== undefined) qs.set("offset", String(input.offset));
    const query = qs.toString();
    return this.client.request<Page<FileRow>>(
      `${this.path}${query ? `?${query}` : ""}`,
    );
  }

  async upload(file: Blob, options: UploadMediaOptions = {}): Promise<FileRow> {
    const fd = new FormData();
    const name =
      options.name ??
      ("name" in file && typeof file.name === "string" ? file.name : "upload.bin");
    fd.append("file", file, name);
    fd.append("compress", String(options.compress ?? true));
    if (options.onProgress && typeof XMLHttpRequest !== "undefined") {
      return this.client.uploadWithXhr(this.path, fd, options.onProgress);
    }
    return this.client.request<FileRow>(this.path, {
      method: "POST",
      body: fd,
    });
  }

  file(id: number): PublicStorageFileRef {
    return new PublicStorageFileRef(this.client, this.storageKey, id);
  }

  downloadUrl(id: number): string {
    return this.file(id).downloadUrl();
  }

  async deleteFile(id: number): Promise<void> {
    await this.file(id).delete();
  }
}

export class PublicStorageFileRef {
  constructor(
    private readonly client: LCloudDbClient,
    private readonly storageKey: string,
    private readonly id: number,
  ) {}

  private get path(): string {
    return `/api/v1/public/storage/key/${encodePath(this.storageKey)}/files/${this.id}`;
  }

  downloadUrl(): string {
    return this.client.url(`${this.path}/download`);
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
