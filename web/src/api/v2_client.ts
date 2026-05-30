/**
 * V2 API client: typed wrappers around `/auth/v2/*` and `/api/v1/*`.
 *
 * Cookie auth is handled by the browser automatically (lc_user_session
 * is HttpOnly + SameSite=Strict). For programmatic use of the same
 * endpoints from non-browser contexts, send `Authorization: Bearer
 * lck_<api_key>` instead.
 *
 * All requests use `credentials: 'same-origin'` so the cookie is sent.
 */
import { signBytes } from '../auth/seed'
import { signFileForUpload } from '../auth/lc2'

// ------------------------------------------------------------ shared types

export interface AuthMe {
  user_id: number
  role: 'admin' | 'user'
  pubkey: string
  label: string | null
  storage_used_bytes: number
  storage_quota_bytes: number
  created_at: string | null
}

export interface ApiKey {
  id: number
  prefix: string
  label: string
  created_at: string | null
  last_used_at: string | null
  revoked_at: string | null
}

export interface MintedApiKey extends ApiKey {
  raw: string
}

export interface CloudV2 {
  id: number
  chat_id: number
  name: string
  owner_user_id: number | null
  created_at: string | null
}

export interface FileV2 {
  id: number
  cloud_id: number
  message_id: number
  owner_user_id: number | null
  name: string
  mime: string
  size: number
  uploaded_at: string | null
  deleted_at: string | null
  caption_kind?: 'LC1' | 'LC2'
  uploaded_at_unix?: number
}

export interface PaginatedFiles {
  items: FileV2[]
  total: number
  limit: number
  offset: number
}

export interface QuotaInfo {
  used_bytes: number
  quota_bytes: number
  free_bytes: number
}

// ------------------------------------------------------------ low-level fetch

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail: unknown
    try {
      detail = (await res.json()).detail
    } catch {
      detail = await res.text()
    }
    const err = new Error(`HTTP ${res.status}: ${JSON.stringify(detail)}`) as Error & {
      status: number
      detail: unknown
    }
    err.status = res.status
    err.detail = detail
    throw err
  }
  return (await res.json()) as T
}

const jsonHeaders = { 'Content-Type': 'application/json' }

// ---------------------------------------------------------------- auth v2

export async function getChallenge(pubkeyHex: string): Promise<{
  challenge_jwt: string
  nonce: string
  expires_in: number
}> {
  const res = await fetch('/auth/v2/challenge', {
    method: 'POST',
    headers: jsonHeaders,
    credentials: 'same-origin',
    body: JSON.stringify({ pubkey: pubkeyHex }),
  })
  return jsonOrThrow(res)
}

export async function verifyChallenge(
  challengeJwt: string,
  signatureHex: string
): Promise<{ user_id: number; role: string; registered: boolean }> {
  const res = await fetch('/auth/v2/verify', {
    method: 'POST',
    headers: jsonHeaders,
    credentials: 'same-origin',
    body: JSON.stringify({
      challenge_jwt: challengeJwt,
      signature: signatureHex,
    }),
  })
  return jsonOrThrow(res)
}

export async function getMeV2(): Promise<AuthMe> {
  const res = await fetch('/auth/v2/me', { credentials: 'same-origin' })
  return jsonOrThrow(res)
}

export async function logoutV2(): Promise<void> {
  await fetch('/auth/v2/logout', { method: 'POST', credentials: 'same-origin' })
}

/** Helper: full challenge + sign + verify roundtrip. */
export async function loginWithKeypair(
  privkeySeed: Uint8Array,
  pubkeyHex: string
): Promise<{ user_id: number; role: string; registered: boolean }> {
  const ch = await getChallenge(pubkeyHex)
  const nonceBytes = hexToBytes(ch.nonce)
  const sig = await signBytes(privkeySeed, nonceBytes)
  return await verifyChallenge(ch.challenge_jwt, bytesToHexLocal(sig))
}

// ---------------------------------------------------------------- api keys

export async function listApiKeys(): Promise<ApiKey[]> {
  return jsonOrThrow(
    await fetch('/api/v1/keys', { credentials: 'same-origin' })
  )
}

export async function mintApiKey(label: string): Promise<MintedApiKey> {
  const res = await fetch('/api/v1/keys', {
    method: 'POST',
    headers: jsonHeaders,
    credentials: 'same-origin',
    body: JSON.stringify({ label }),
  })
  return jsonOrThrow(res)
}

export async function revokeApiKey(id: number): Promise<void> {
  const res = await fetch(`/api/v1/keys/${id}`, {
    method: 'DELETE',
    credentials: 'same-origin',
  })
  await jsonOrThrow(res)
}

// ---------------------------------------------------------------- clouds

export async function listClouds(): Promise<CloudV2[]> {
  return jsonOrThrow(
    await fetch('/api/v1/clouds', { credentials: 'same-origin' })
  )
}

export async function createCloud(name: string): Promise<CloudV2> {
  const res = await fetch('/api/v1/clouds', {
    method: 'POST',
    headers: jsonHeaders,
    credentials: 'same-origin',
    body: JSON.stringify({ name }),
  })
  return jsonOrThrow(res)
}

export async function deleteCloud(cloudId: number): Promise<void> {
  await fetch(`/api/v1/clouds/${cloudId}`, {
    method: 'DELETE',
    credentials: 'same-origin',
  })
}

// ---------------------------------------------------------------- files

export async function listFiles(
  cloudId: number,
  opts: { limit?: number; offset?: number } = {}
): Promise<PaginatedFiles> {
  const params = new URLSearchParams()
  if (opts.limit) params.set('limit', String(opts.limit))
  if (opts.offset) params.set('offset', String(opts.offset))
  const qs = params.toString() ? `?${params}` : ''
  return jsonOrThrow(
    await fetch(`/api/v1/clouds/${cloudId}/files${qs}`, {
      credentials: 'same-origin',
    })
  )
}

export interface UploadFileOpts {
  /** If provided, sign with this user's keypair → server stores LC2 caption. */
  signWith?: { pubkey: Uint8Array; privkeySeed: Uint8Array }
  onProgress?: (phase: 'signing' | 'uploading', percent: number) => void
}

export async function uploadFile(
  cloudId: number,
  file: File,
  opts: UploadFileOpts = {}
): Promise<FileV2> {
  const fd = new FormData()
  fd.append('file', file)

  if (opts.signWith) {
    opts.onProgress?.('signing', 0)
    const signed = await signFileForUpload(
      file,
      opts.signWith.pubkey,
      opts.signWith.privkeySeed
    )
    fd.append('client_sha256', signed.fileSha256Hex)
    fd.append('signature', signed.signatureHex)
    fd.append('ts', String(signed.ts))
    opts.onProgress?.('signing', 100)
  }
  opts.onProgress?.('uploading', 0)

  // We use XMLHttpRequest for upload progress — `fetch` doesn't expose it.
  return await new Promise<FileV2>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `/api/v1/clouds/${cloudId}/files`, true)
    xhr.withCredentials = true
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        opts.onProgress?.('uploading', (e.loaded / e.total) * 100)
      }
    }
    xhr.onerror = () => reject(new Error('network error'))
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText))
        } catch (e) {
          reject(e)
        }
      } else {
        let detail: unknown
        try {
          detail = JSON.parse(xhr.responseText).detail
        } catch {
          detail = xhr.responseText
        }
        const err = new Error(`HTTP ${xhr.status}: ${JSON.stringify(detail)}`) as Error & {
          status: number
          detail: unknown
        }
        err.status = xhr.status
        err.detail = detail
        reject(err)
      }
    }
    xhr.send(fd)
  })
}

export async function deleteFile(fileId: number): Promise<void> {
  await fetch(`/api/v1/files/${fileId}`, {
    method: 'DELETE',
    credentials: 'same-origin',
  })
}

export function fileDownloadUrl(fileId: number): string {
  return `/api/v1/files/${fileId}/download`
}

// ---------------------------------------------------------------- quota

export async function getQuota(): Promise<QuotaInfo> {
  return jsonOrThrow(
    await fetch('/api/v1/files/quota', { credentials: 'same-origin' })
  )
}

// ---------------------------------------------------------------- helpers

function hexToBytes(hex: string): Uint8Array {
  const out = new Uint8Array(hex.length / 2)
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(hex.substr(i * 2, 2), 16)
  }
  return out
}

function bytesToHexLocal(b: Uint8Array): string {
  let s = ''
  for (let i = 0; i < b.length; i++) s += b[i].toString(16).padStart(2, '0')
  return s
}
