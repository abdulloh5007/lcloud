import { LCloudDbError, reasonFromDetail } from "./errors.js";
import type {
  AppSession,
  AppUser,
  AuthResponse,
  AuthStorage,
  LCloudPublicClientOptions,
} from "./types.js";

type AuthListener = (user: AppUser | null) => void;

export class LCloudAuth {
  private readonly endpoint: string;
  private readonly publishableKey: string;
  private readonly fetchImpl: typeof fetch;
  private readonly storage?: AuthStorage;
  private readonly storageKey: string;
  private session: AppSession | null;
  private refreshPromise: Promise<AppSession> | null = null;
  private readonly listeners = new Set<AuthListener>();

  constructor(options: LCloudPublicClientOptions & { publishableKey: string }) {
    this.endpoint = options.endpoint.replace(/\/+$/, "");
    this.publishableKey = options.publishableKey;
    this.fetchImpl = options.fetch ?? fetch;
    this.storage = options.authStorage ?? browserStorage();
    this.storageKey = `lcloud.auth:${this.endpoint}:${this.publishableKey}`;
    this.session = this.readSession();
  }

  get currentUser(): AppUser | null {
    return this.session?.user ?? null;
  }

  async signInAnonymously(): Promise<AppSession> {
    const response = await this.authRequest<AuthResponse>("anonymous", { method: "POST" });
    return this.saveResponse(response);
  }

  async getAccessToken(): Promise<string | null> {
    const session = this.session;
    if (!session) return null;
    if (session.expires_at > Date.now() + 60_000) return session.access_token;
    return (await this.refresh()).access_token;
  }

  async refresh(): Promise<AppSession> {
    if (this.refreshPromise) return this.refreshPromise;
    const refreshToken = this.session?.refresh_token;
    if (!refreshToken) throw new Error("No LCloud auth session to refresh");
    this.refreshPromise = this.authRequest<AuthResponse>("refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    })
      .then((response) => this.saveResponse(response))
      .catch((error: unknown) => {
        if (error instanceof LCloudDbError && error.status === 401) this.clearSession();
        throw error;
      })
      .finally(() => {
        this.refreshPromise = null;
      });
    return this.refreshPromise;
  }

  async signOut(): Promise<void> {
    const refreshToken = this.session?.refresh_token;
    if (refreshToken) {
      try {
        await this.authRequest<void>("sign-out", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: refreshToken }),
        });
      } catch {
        // Local sign-out must still work while the network is unavailable.
      } finally {
        this.clearSession();
      }
      return;
    }
    this.clearSession();
  }

  onAuthStateChange(listener: AuthListener): () => void {
    this.listeners.add(listener);
    listener(this.currentUser);
    return () => this.listeners.delete(listener);
  }

  private async authRequest<T>(action: string, init: RequestInit): Promise<T> {
    const path = `/api/v1/public/auth/key/${encodeURIComponent(this.publishableKey)}/${action}`;
    const response = await this.fetchImpl(`${this.endpoint}${path}`, init);
    if (!response.ok) {
      let detail: unknown = await response.text();
      try {
        detail = JSON.parse(String(detail));
      } catch {
        // Keep the server response as text.
      }
      throw new LCloudDbError(
        response.status,
        reasonFromDetail(detail, `http_${response.status}`),
        detail,
      );
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  private saveResponse(response: AuthResponse): AppSession {
    const session: AppSession = {
      access_token: response.access_token,
      refresh_token: response.refresh_token,
      expires_at: Date.now() + response.expires_in * 1000,
      token_type: response.token_type,
      user: response.user,
    };
    this.session = session;
    try {
      this.storage?.setItem(this.storageKey, JSON.stringify(session));
    } catch {
      // Keep an in-memory session when browser storage is unavailable.
    }
    this.emit();
    return session;
  }

  private readSession(): AppSession | null {
    try {
      const raw = this.storage?.getItem(this.storageKey);
      return raw ? (JSON.parse(raw) as AppSession) : null;
    } catch {
      return null;
    }
  }

  private clearSession(): void {
    this.session = null;
    try {
      this.storage?.removeItem(this.storageKey);
    } catch {
      // The in-memory session is already cleared.
    }
    this.emit();
  }

  private emit(): void {
    for (const listener of this.listeners) listener(this.currentUser);
  }
}

function browserStorage(): AuthStorage | undefined {
  try {
    return typeof localStorage === "undefined" ? undefined : localStorage;
  } catch {
    return undefined;
  }
}
