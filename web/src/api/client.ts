import type {
  ApiErrorBody,
  AuthState,
  CloudRow,
  FileRow,
  FilesPage,
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
  list: () => api<CloudRow[]>("/clouds"),
  create: (name: string) =>
    api<CloudRow>("/clouds", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  remove: (id: number) =>
    api<void>(`/clouds/${id}`, { method: "DELETE" }),
};

// ---------------------------------------------------------------- files

export const files = {
  list: (cloudId: number, params: { limit?: number; offset?: number } = {}) => {
    const sp = new URLSearchParams();
    if (params.limit !== undefined) sp.set("limit", String(params.limit));
    if (params.offset !== undefined) sp.set("offset", String(params.offset));
    const qs = sp.toString();
    return api<FilesPage>(`/clouds/${cloudId}/files${qs ? `?${qs}` : ""}`);
  },
  rename: (id: number, name: string) =>
    api<FileRow>(`/files/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),
  upload: (
    cloudId: number,
    file: File,
    onProgress?: (loaded: number, total: number) => void,
  ): Promise<FileRow> => {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/clouds/${cloudId}/files`);
      xhr.withCredentials = true;
      xhr.upload.addEventListener("progress", (e) => {
        if (onProgress && e.lengthComputable) {
          onProgress(e.loaded, e.total);
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
      const fd = new FormData();
      fd.append("file", file);
      xhr.send(fd);
    });
  },
  remove: (id: number) => api<void>(`/files/${id}`, { method: "DELETE" }),
  downloadUrl: (id: number) => `/files/${id}/download`,
  thumbUrl: (id: number, size: ThumbSize) =>
    `/files/${id}/thumb?size=${size}`,
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
