import { LCloudDbError, reasonFromDetail } from "./errors.js";
import type { FileRow, UploadProgress } from "./types.js";
import { isFormData } from "./utils.js";

export interface HttpClientOptions {
  endpoint: string;
  apiKey?: string;
  fetch?: typeof fetch;
  credentials?: RequestCredentials;
  accessToken?: () => Promise<string | null>;
}

export class HttpClient {
  private readonly endpoint: string;
  private readonly apiKey?: string;
  private readonly fetchImpl: typeof fetch;
  private readonly credentials: RequestCredentials;
  private readonly accessToken?: () => Promise<string | null>;

  constructor(options: HttpClientOptions) {
    this.endpoint = options.endpoint.replace(/\/+$/, "");
    this.apiKey = options.apiKey;
    this.fetchImpl = options.fetch ?? fetch;
    this.credentials = options.credentials ?? "include";
    this.accessToken = options.accessToken;
  }

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    if (!headers.has("Content-Type") && init.body !== undefined) {
      if (!isFormData(init.body)) headers.set("Content-Type", "application/json");
    }
    const token = this.apiKey ?? (await this.accessToken?.());
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await this.fetchImpl(this.url(path), {
      credentials: this.credentials,
      ...init,
      headers,
    });
    if (!response.ok) {
      throw await responseError(response);
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
      void this.startXhrUpload(path, body, onProgress, resolve, reject);
    });
  }

  private async startXhrUpload(
    path: string,
    body: FormData,
    onProgress: (progress: UploadProgress) => void,
    resolve: (value: FileRow) => void,
    reject: (reason: unknown) => void,
  ): Promise<void> {
    try {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", this.url(path));
      xhr.withCredentials = this.credentials === "include";
      const token = this.apiKey ?? (await this.accessToken?.());
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
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
    } catch (error) {
      reject(error);
    }
  }
}

async function responseError(response: Response): Promise<LCloudDbError> {
  let detail: unknown = await response.text();
  try {
    detail = JSON.parse(String(detail));
  } catch {
    // keep text detail
  }
  return new LCloudDbError(
    response.status,
    reasonFromDetail(detail, `http_${response.status}`),
    detail,
  );
}

function parseXhrError(xhr: XMLHttpRequest): LCloudDbError {
  let detail: unknown = xhr.responseText;
  try {
    detail = JSON.parse(xhr.responseText);
  } catch {
    // keep text response
  }
  return new LCloudDbError(xhr.status, reasonFromDetail(detail, `http_${xhr.status}`), detail);
}
