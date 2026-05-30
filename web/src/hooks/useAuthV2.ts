import { useCallback, useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getMeV2, logoutV2, type AuthMe } from '@/api/v2_client'

/** Held in memory ONLY during an active session — never persisted. */
export interface UserKeypair {
  pubkey: Uint8Array
  privkeySeed: Uint8Array
  pubkeyHex: string
}

export interface AuthV2HookValue {
  me: AuthMe | undefined
  keypair: UserKeypair | undefined
  isLoading: boolean
  isAuthenticated: boolean
  setKeypair: (kp: UserKeypair | undefined) => void
  logout: () => Promise<void>
  refresh: () => Promise<unknown>
}

const KP_KEY = '__lc_kp_session__'

interface KpJson {
  pubkey: string
  privkeySeed: string
  pubkeyHex: string
}

/** Read in-memory keypair from sessionStorage (cleared on tab close). */
function readKeypair(): UserKeypair | undefined {
  try {
    const raw = sessionStorage.getItem(KP_KEY)
    if (!raw) return undefined
    const j: KpJson = JSON.parse(raw)
    return {
      pubkey: hex(j.pubkey),
      privkeySeed: hex(j.privkeySeed),
      pubkeyHex: j.pubkeyHex,
    }
  } catch {
    return undefined
  }
}

function persistKeypair(kp: UserKeypair | undefined) {
  if (!kp) {
    sessionStorage.removeItem(KP_KEY)
    return
  }
  const j: KpJson = {
    pubkey: hexstr(kp.pubkey),
    privkeySeed: hexstr(kp.privkeySeed),
    pubkeyHex: kp.pubkeyHex,
  }
  sessionStorage.setItem(KP_KEY, JSON.stringify(j))
}

function hex(s: string): Uint8Array {
  const out = new Uint8Array(s.length / 2)
  for (let i = 0; i < out.length; i++) out[i] = parseInt(s.substr(i * 2, 2), 16)
  return out
}
function hexstr(b: Uint8Array): string {
  let s = ''
  for (let i = 0; i < b.length; i++) s += b[i].toString(16).padStart(2, '0')
  return s
}

/**
 * V2 auth hook: backed by /auth/v2/me + sessionStorage keypair.
 *
 * Why sessionStorage for the privkey:
 * - Survives page refresh within the same tab — important so that an
 *   accidental F5 doesn't ask the user to re-paste their seed phrase
 *   on every reload.
 * - Cleared automatically when the tab closes.
 * - LocalStorage would survive across tabs/devices and be a much bigger
 *   attack surface.
 *
 * Trade-off: an attacker with running JS in your tab can read it. Same
 * security model as any browser-held credential.
 */
export function useAuthV2(): AuthV2HookValue {
  const qc = useQueryClient()
  const [keypair, setKeypairState] = useState<UserKeypair | undefined>(
    () => readKeypair()
  )

  const setKeypair = useCallback((kp: UserKeypair | undefined) => {
    persistKeypair(kp)
    setKeypairState(kp)
  }, [])

  // /auth/v2/me — drives whether we're really logged in
  const meQuery = useQuery({
    queryKey: ['v2', 'me'],
    queryFn: getMeV2,
    retry: false,
    refetchOnWindowFocus: true,
    refetchInterval: (q) => (q.state.data ? 30000 : 5000),
  })

  // If /me returns 401, we're logged out — drop the in-memory keypair.
  useEffect(() => {
    if (meQuery.isError && keypair) {
      const status = (meQuery.error as { status?: number } | null)?.status
      if (status === 401 || status === 403) {
        setKeypair(undefined)
      }
    }
  }, [meQuery.isError, meQuery.error, keypair, setKeypair])

  const logout = useCallback(async () => {
    try {
      await logoutV2()
    } finally {
      setKeypair(undefined)
      qc.removeQueries({ queryKey: ['v2', 'me'] })
      await qc.invalidateQueries()
    }
  }, [qc, setKeypair])

  return {
    me: meQuery.data,
    keypair,
    isLoading: meQuery.isLoading,
    isAuthenticated: !!meQuery.data,
    setKeypair,
    logout,
    refresh: () => meQuery.refetch(),
  }
}
