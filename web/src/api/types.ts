// Shared TypeScript types matching the FastAPI responses.

export interface AuthMe {
  id: number;
  first_name: string | null;
  username: string | null;
}

export type LoginFlowState = "no_session" | "code_sent" | "pwd_needed" | "authorized";

export interface AuthState {
  authorized: boolean;
  userbot_authed: boolean;
  userbot_started: boolean;
  bootstrap_mode: boolean;
  state: LoginFlowState;
  me: AuthMe | null;
}

export interface CloudRow {
  id: number;
  chat_id: number;
  name: string;
  created_at: string | null;
}

export interface FileRow {
  id: number;
  cloud_id: number;
  message_id: number;
  name: string;
  mime: string;
  size: number;
  uploaded_at: string | null;
  deleted_at: string | null;
  /** V2 only: which caption format the file was uploaded with. */
  caption_kind?: "LC1" | "LC2";
  /** V2 only: pubkey of the V2 user who owns this row, NULL for legacy admin. */
  owner_user_id?: number | null;
}

export interface TagRow {
  id: number;
  name: string;
  color: string;
  icon: string;
  bg_color: string;
  created_at: string | null;
}

export interface FilesPage {
  items: FileRow[];
  total: number;
  limit: number;
  offset: number;
}

export type ThumbSize = "low" | "med" | "high";

export interface SearchResult {
  items: FileRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface ApiErrorBody {
  detail?: { reason?: string; [k: string]: unknown } | string;
}

export type JsonAccessRule =
  | "owner"
  | "document_owner"
  | "authenticated"
  | "public";
export type JsonWhereOp =
  | "=="
  | "!="
  | "<"
  | "<="
  | ">"
  | ">="
  | "contains"
  | "startsWith";

export interface JsonWriteValidator {
  max_bytes?: number | null;
  max_fields?: number | null;
  required_fields?: string[];
  allowed_fields?: string[];
}

export interface JsonCollectionRow {
  id: number;
  name: string;
  owner_user_id: number;
  read_rule: JsonAccessRule;
  write_rule: JsonAccessRule;
  write_validator: JsonWriteValidator | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface JsonDbPublicKeyRow {
  id: number;
  key: string;
  prefix: string;
  label: string;
  created_at: string | null;
  revoked_at: string | null;
}

export interface JsonStoragePublicKeyRow {
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

export interface JsonDocumentRow {
  id: string;
  collection_id: number;
  data: Record<string, unknown>;
  version: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface JsonDocumentsPage {
  items: JsonDocumentRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface JsonRulesRow {
  collection: string;
  collection_id: number;
  read: JsonAccessRule;
  write: JsonAccessRule;
  public_base_path: string;
}

export interface JsonValidatorRow {
  collection: string;
  collection_id: number;
  validator: JsonWriteValidator | null;
}

export interface JsonWhereFilter {
  field: string;
  op: JsonWhereOp;
  value: unknown;
}

export interface JsonQueryInput {
  where?: JsonWhereFilter[];
  order_by?: string | null;
  order?: "asc" | "desc";
  limit?: number;
  offset?: number;
}

export interface JsonDbMeta {
  name: string;
  version: string;
  documents: Record<string, unknown>;
  collections: Record<string, unknown>;
  document_ids: Record<string, unknown>;
  pagination: {
    default_limit: number;
    max_limit: number;
    offset_min: number;
  };
  query: {
    max_where_filters: number;
    max_field_path_length: number;
    operators: JsonWhereOp[];
    field_paths: string;
    engine: string;
    indexes: string;
  };
  batch: {
    max_writes: number;
    operations: string[];
    atomic: boolean;
  };
  realtime: {
    transport: string;
    event: string;
    owner_path: string;
    public_path: string;
    cursor: string;
    query_params: string[];
    poll_seconds: number;
    batch_limit: number;
  };
  access_rules: {
    rules: JsonAccessRule[];
    default_read: JsonAccessRule;
    default_write: JsonAccessRule;
    public_base_path: string;
    owner_manage_path: string;
    write_validator_path: string;
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
  media: Record<string, unknown>;
  auth: Record<string, unknown>;
  rate_limits: Record<string, unknown>;
  not_supported_yet: string[];
}

export interface JsonDbEvent {
  id: number;
  collection_id: number;
  doc_id: string | null;
  op: string;
  payload: Record<string, unknown>;
  created_at: string | null;
}
