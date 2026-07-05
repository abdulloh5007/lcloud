import type {
  ApiErrorBody,
  AuthState,
  CloudRow,
  FileRow,
  FilesPage,
  JsonCollectionRow,
  JsonDbMeta,
  JsonDocumentsPage,
  JsonQueryInput,
  JsonRulesRow,
  JsonValidatorRow,
  JsonWriteValidator,
  SearchResult,
  TagRow,
  ThumbSize,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    public reason: string,
    public detail: unknown,
  ) {
    super(`API ${status}: ${reason}`);
  }
}

async function rawRequest(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  if (
    init.body !== undefined &&
    !(init.body instanceof FormData) &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }
  return fetch(path, { credentials: "same-origin", ...init, headers });
}

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await rawRequest(path, init);
  if (!r.ok) {
    let body: ApiErrorBody | string = "";
    try {
      body = (await r.json()) as ApiErrorBody;
    } catch {
      body = await r.text();
    }
    const reason =
      typeof body === "object" && body && typeof body.detail === "object"
        ? body.detail?.reason ?? "unknown"
        : typeof body === "object" && body && typeof body.detail === "string"
          ? body.detail
          : `http_${r.status}`;
    throw new ApiError(r.status, String(reason), body);
  }
  if (r.status === 204) return undefined as T;
  return (await r.json()) as T;
}

// ---------------------------------------------------------------- auth

export const auth = {
  state: () => api<AuthState>("/auth/state"),
  start: (phone: string) =>
    api<{ ok: boolean; state: string }>("/auth/telegram/start", {
      method: "POST",
      body: JSON.stringify({ phone }),
    }),
  code: (code: string) =>
    api<
      | { authorized: true; me: AuthState["me"] }
      | { need_password: true; state: string }
    >("/auth/telegram/code", {
      method: "POST",
      body: JSON.stringify({ code }),
    }),
  password: (password: string) =>
    api<{ authorized: true; me: AuthState["me"] }>(
      "/auth/telegram/password",
      {
        method: "POST",
        body: JSON.stringify({ password }),
      },
    ),
  cancel: () => api<{ ok: boolean }>("/auth/telegram/cancel", { method: "POST" }),
  logout: () => api<{ ok: boolean }>("/auth/logout", { method: "POST" }),
};

// ---------------------------------------------------------------- clouds

export const clouds = {
  list: () => api<CloudRow[]>("/api/v1/clouds"),
  create: (name: string) =>
    api<CloudRow>("/api/v1/clouds", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  remove: (id: number) =>
    api<void>(`/api/v1/clouds/${id}`, { method: "DELETE" }),
};

// ---------------------------------------------------------------- files

/**
 * sessionStorage-held keypair (set by useAuthV2 on successful login).
 * Used here so the upload helper can transparently sign files with LC2.
 * Returns undefined if not logged in via V2.
 */
function readSessionKeypair():
  | { pubkey: Uint8Array; privkeySeed: Uint8Array }
  | undefined {
  try {
    const raw = sessionStorage.getItem("__lc_kp_session__");
    if (!raw) return undefined;
    const j = JSON.parse(raw) as { pubkey: string; privkeySeed: string };
    const hex2 = (s: string) => {
      const b = new Uint8Array(s.length / 2);
      for (let i = 0; i < b.length; i++) b[i] = parseInt(s.substr(i * 2, 2), 16);
      return b;
    };
    return { pubkey: hex2(j.pubkey), privkeySeed: hex2(j.privkeySeed) };
  } catch {
    return undefined;
  }
}

export type UploadPhase = "signing" | "uploading";

export const files = {
  list: (cloudId: number, params: { limit?: number; offset?: number } = {}) => {
    const sp = new URLSearchParams();
    if (params.limit !== undefined) sp.set("limit", String(params.limit));
    if (params.offset !== undefined) sp.set("offset", String(params.offset));
    const qs = sp.toString();
    return api<FilesPage>(
      `/api/v1/clouds/${cloudId}/files${qs ? `?${qs}` : ""}`,
    );
  },
  rename: (id: number, name: string) =>
    api<FileRow>(`/files/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),
  upload: async (
    cloudId: number,
    file: File,
    onProgress?: (
      loaded: number,
      total: number,
      phase: UploadPhase,
    ) => void,
    opts: { compress?: boolean } = {},
  ): Promise<FileRow> => {
    const fd = new FormData();
    fd.append("file", file);
    // Always send the compress flag so server uses our preference
    // (true is already the server default but explicitness wins).
    fd.append("compress", String(opts.compress ?? true));

    // If we have a V2 keypair → sign client-side (LC2 caption).
    const kp = readSessionKeypair();
    if (kp) {
      onProgress?.(0, 100, "signing");
      const { signFileForUpload } = await import("@/auth/lc2");
      const signed = await signFileForUpload(file, kp.pubkey, kp.privkeySeed);
      fd.append("client_sha256", signed.fileSha256Hex);
      fd.append("signature", signed.signatureHex);
      fd.append("ts", String(signed.ts));
      onProgress?.(100, 100, "signing");
    }

    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api/v1/clouds/${cloudId}/files`);
      xhr.withCredentials = true;
      xhr.upload.addEventListener("progress", (e) => {
        if (onProgress && e.lengthComputable) {
          onProgress(e.loaded, e.total, "uploading");
        }
      });
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText));
          } catch (e) {
            reject(e);
          }
        } else {
          let reason = `http_${xhr.status}`;
          try {
            const body = JSON.parse(xhr.responseText);
            reason = body?.detail?.reason ?? reason;
          } catch {
            // ignore
          }
          reject(new ApiError(xhr.status, reason, xhr.responseText));
        }
      };
      xhr.onerror = () => reject(new ApiError(0, "network_error", null));
      xhr.send(fd);
    });
  },
  remove: (id: number) => api<void>(`/api/v1/files/${id}`, { method: "DELETE" }),
  downloadUrl: (id: number) => `/api/v1/files/${id}/download`,
  thumbUrl: (id: number, size: ThumbSize) =>
    `/files/${id}/thumb?size=${size}`,  // V1 thumb route — V2 thumb not implemented yet
  setTags: (fileId: number, tagIds: number[]) =>
    api<{ file_id: number; tag_ids: number[] }>(
      `/files/${fileId}/tags`,
      { method: "PUT", body: JSON.stringify({ tag_ids: tagIds }) },
    ),
  getTags: (fileId: number) => api<TagRow[]>(`/files/${fileId}/tags`),
};

// ---------------------------------------------------------------- tags

export const tags = {
  list: () => api<TagRow[]>("/tags"),
  create: (input: Omit<TagRow, "id" | "created_at">) =>
    api<TagRow>("/tags", { method: "POST", body: JSON.stringify(input) }),
  patch: (id: number, input: Partial<Omit<TagRow, "id" | "created_at">>) =>
    api<TagRow>(`/tags/${id}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    }),
  remove: (id: number) =>
    api<void>(`/tags/${id}`, { method: "DELETE" }),
};

// ---------------------------------------------------------------- payments

export interface PaymentInfo {
  card_number: string;
  card_holder: string;
  scheme: string;
  amount_cents: number;
  currency: string;
  tier_label: string;
}

export interface PaymentRequestRow {
  id: number;
  contact_handle: string;
  amount_cents: number;
  currency: string;
  note: string | null;
  status: 'pending' | 'approved' | 'rejected';
  created_at: string | null;
  approved_at: string | null;
  rejected_at: string | null;
  generated_user_id: number | null;
  ip_addr: string | null;
}

export const payments = {
  info: () => api<PaymentInfo>('/api/v1/payments/info'),
  request: (contact_handle: string, note?: string) =>
    api<{ id: number; status: string; duplicate: boolean }>(
      '/api/v1/payments/request',
      {
        method: 'POST',
        body: JSON.stringify({ contact_handle, note }),
      },
    ),
  // Admin only
  list: (status?: 'pending' | 'approved' | 'rejected') => {
    const qs = status ? `?status=${status}` : '';
    return api<PaymentRequestRow[]>(`/api/v1/admin/payments${qs}`);
  },
  approve: (id: number) =>
    api<{
      request_id: number;
      user_id: number;
      contact_handle: string;
      seed_phrase: string;
      warning: string;
    }>(`/api/v1/admin/payments/${id}/approve`, { method: 'POST' }),
  reject: (id: number, reason?: string) =>
    api<{ request_id: number; status: string }>(
      `/api/v1/admin/payments/${id}/reject`,
      {
        method: 'POST',
        body: JSON.stringify({ reason }),
      },
    ),
};

// ---------------------------------------------------------------- shares

export interface FileShare {
  id: number;
  file_id: number;
  token: string;
  url?: string;
  created_at: string | null;
  expires_at: string | null;
  max_downloads: number | null;
  download_count: number;
  revoked_at: string | null;
  active: boolean;
}

export const shares = {
  create: (
    fileId: number,
    opts: { expires_in_seconds?: number; max_downloads?: number } = {},
  ) =>
    api<FileShare>(`/api/v1/files/${fileId}/shares`, {
      method: "POST",
      body: JSON.stringify(opts),
    }),
  listForFile: (fileId: number) =>
    api<FileShare[]>(`/api/v1/files/${fileId}/shares`),
  revoke: (shareId: number) =>
    api<void>(`/api/v1/shares/${shareId}`, { method: "DELETE" }),
};

// ---------------------------------------------------------------- versions

export interface FileVersion {
  id: number;
  original_name: string;
  size_bytes: number;
  mime: string;
  uploaded_at: string | null;
  deleted_at: string | null;
  replaces_file_id: number | null;
  compressed: boolean;
}

export const versions = {
  list: (fileId: number) =>
    api<FileVersion[]>(`/api/v1/files/${fileId}/versions`),
};

export const pinApi = {
  /** Setup PIN (authenticated). Browser holds mnemonic in memory only. */
  setup: (pin: string, mnemonic: string) =>
    api<{ ok: boolean }>("/auth/v2/pin/setup", {
      method: "POST",
      body: JSON.stringify({ pin, mnemonic }),
    }),
  /** Recover seed phrase by contact + PIN (anonymous, IP rate-limited). */
  recover: (contact_handle: string, pin: string) =>
    api<{ mnemonic: string; warning: string }>("/auth/v2/pin/recover", {
      method: "POST",
      body: JSON.stringify({ contact_handle, pin }),
    }),
};

// ---------------------------------------------------------------- search

export const search = {
  query: (params: {
    q?: string;
    cloud_id?: number;
    tag?: number[];
    limit?: number;
    offset?: number;
  }) => {
    const sp = new URLSearchParams();
    if (params.q) sp.set("q", params.q);
    if (params.cloud_id !== undefined) sp.set("cloud_id", String(params.cloud_id));
    for (const t of params.tag ?? []) sp.append("tag", String(t));
    if (params.limit !== undefined) sp.set("limit", String(params.limit));
    if (params.offset !== undefined) sp.set("offset", String(params.offset));
    const qs = sp.toString();
    return api<SearchResult>(`/search${qs ? `?${qs}` : ""}`);
  },
};

// ---------------------------------------------------------------- JSON DB

function enc(v: string): string {
  return encodeURIComponent(v);
}

export const jsonDb = {
  meta: () => api<JsonDbMeta>("/api/v1/db/_meta"),
  listCollections: () => api<JsonCollectionRow[]>("/api/v1/db/collections"),
  createCollection: (name: string) =>
    api<JsonCollectionRow>("/api/v1/db/collections", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  deleteCollection: (collection: string) =>
    api<void>(`/api/v1/db/collections/${enc(collection)}`, {
      method: "DELETE",
    }),
  listDocuments: (
    collection: string,
    params: { limit?: number; offset?: number } = {},
  ) => {
    const sp = new URLSearchParams();
    if (params.limit !== undefined) sp.set("limit", String(params.limit));
    if (params.offset !== undefined) sp.set("offset", String(params.offset));
    const qs = sp.toString();
    return api<JsonDocumentsPage>(
      `/api/v1/db/${enc(collection)}${qs ? `?${qs}` : ""}`,
    );
  },
  queryDocuments: (collection: string, input: JsonQueryInput) =>
    api<JsonDocumentsPage>(`/api/v1/db/${enc(collection)}/query`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  createDocument: (
    collection: string,
    input: { id?: string; data: Record<string, unknown> },
  ) =>
    api<JsonDocumentsPage["items"][number]>(`/api/v1/db/${enc(collection)}`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  setDocument: (
    collection: string,
    id: string,
    data: Record<string, unknown>,
  ) =>
    api<JsonDocumentsPage["items"][number]>(
      `/api/v1/db/${enc(collection)}/${enc(id)}`,
      {
        method: "PUT",
        body: JSON.stringify({ data }),
      },
    ),
  patchDocument: (
    collection: string,
    id: string,
    data: Record<string, unknown>,
  ) =>
    api<JsonDocumentsPage["items"][number]>(
      `/api/v1/db/${enc(collection)}/${enc(id)}`,
      {
        method: "PATCH",
        body: JSON.stringify({ data }),
      },
    ),
  deleteDocument: (collection: string, id: string) =>
    api<void>(`/api/v1/db/${enc(collection)}/${enc(id)}`, {
      method: "DELETE",
    }),
  getRules: (collection: string) =>
    api<JsonRulesRow>(`/api/v1/db/collections/${enc(collection)}/rules`),
  setRules: (
    collection: string,
    rules: Pick<JsonRulesRow, "read" | "write">,
  ) =>
    api<JsonRulesRow>(`/api/v1/db/collections/${enc(collection)}/rules`, {
      method: "PUT",
      body: JSON.stringify(rules),
    }),
  getValidator: (collection: string) =>
    api<JsonValidatorRow>(`/api/v1/db/collections/${enc(collection)}/validator`),
  setValidator: (collection: string, validator: JsonWriteValidator) =>
    api<JsonValidatorRow>(`/api/v1/db/collections/${enc(collection)}/validator`, {
      method: "PUT",
      body: JSON.stringify(validator),
    }),
  deleteValidator: (collection: string) =>
    api<void>(`/api/v1/db/collections/${enc(collection)}/validator`, {
      method: "DELETE",
    }),
  eventsUrl: (collection: string, since = 0) =>
    `/api/v1/db/${enc(collection)}/events?since=${since}`,
};
