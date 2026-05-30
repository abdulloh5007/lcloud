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
