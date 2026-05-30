/**
 * Account tab: shows the logged-in user's identity, role, storage usage.
 *
 * Note: pubkey is the only persistent identifier we have for the user
 * (we don't have email/username — it's all seed-phrase derived).
 */
import { useQuery } from '@tanstack/react-query'
import { getQuota, type AuthMe } from '@/api/v2_client'

interface Props {
  me: AuthMe
  onLogout: () => void
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`
  if (n < 1024 ** 4) return `${(n / 1024 ** 3).toFixed(2)} GB`
  return `${(n / 1024 ** 4).toFixed(2)} TB`
}

export function AccountSection({ me, onLogout }: Props) {
  const quotaQ = useQuery({
    queryKey: ['v2', 'quota'],
    queryFn: getQuota,
    refetchInterval: 30_000,
  })
  const quota = quotaQ.data ?? {
    used_bytes: me.storage_used_bytes,
    quota_bytes: me.storage_quota_bytes,
    free_bytes: Math.max(0, me.storage_quota_bytes - me.storage_used_bytes),
  }
  const pct = quota.quota_bytes > 0 ? (quota.used_bytes / quota.quota_bytes) * 100 : 0

  const fingerprint = me.pubkey.slice(0, 16)

  return (
    <div className="space-y-5">
      <div>
        <h3 className="font-medium mb-2">Идентичность</h3>
        <div className="rounded-lg border border-neutral-200 dark:border-neutral-800 p-3 space-y-2 text-sm">
          <Row label="ID">{me.user_id}</Row>
          <Row label="Pubkey">
            <code className="font-mono text-xs">{fingerprint}…</code>
          </Row>
          {me.created_at && (
            <Row label="Создан">
              {new Date(me.created_at).toLocaleDateString()}
            </Row>
          )}
        </div>
      </div>

      <div>
        <h3 className="font-medium mb-2">Хранилище</h3>
        <div className="rounded-lg border border-neutral-200 dark:border-neutral-800 p-3 space-y-2">
          <div className="flex justify-between text-sm">
            <span>{formatBytes(quota.used_bytes)} использовано</span>
            <span className="text-neutral-500">
              из {formatBytes(quota.quota_bytes)}
            </span>
          </div>
          <div className="h-2 rounded-full bg-neutral-100 dark:bg-neutral-800 overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${
                pct > 90
                  ? 'bg-red-500'
                  : pct > 70
                    ? 'bg-amber-500'
                    : 'bg-emerald-500'
              }`}
              style={{ width: `${Math.min(100, pct).toFixed(1)}%` }}
            />
          </div>
          <div className="text-xs text-neutral-400">
            {formatBytes(quota.free_bytes)} свободно
          </div>
        </div>
      </div>

      <button
        onClick={onLogout}
        className="w-full py-2 text-sm border border-red-300 dark:border-red-800 text-red-600 dark:text-red-400 rounded-lg hover:bg-red-50 dark:hover:bg-red-950/30"
      >
        Выйти из аккаунта
      </button>

      <p className="text-xs text-neutral-400">
        После выхода вам понадобится снова ввести сид-фразу.
      </p>
    </div>
  )
}

function Row({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div className="flex justify-between gap-3">
      <span className="text-neutral-500">{label}</span>
      <span>{children}</span>
    </div>
  )
}
