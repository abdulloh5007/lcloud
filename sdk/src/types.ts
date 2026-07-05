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
