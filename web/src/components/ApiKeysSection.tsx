/**
 * API Keys management tab inside Settings.
 *
 * UX:
 *   - List of user's keys with prefix, label, last_used_at, status badge
 *   - "+ Создать ключ" → opens a small inline form (label + button)
 *   - On mint: shows raw key in a banner with copy-to-clipboard.
 *     The banner has a "Готово, я сохранил" button that hides the raw key.
 *   - Each row has "Отозвать" button (with confirmation prompt)
 */
import { useCallback, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  listApiKeys,
  mintApiKey,
  revokeApiKey,
  type ApiKey,
  type MintedApiKey,
} from '@/api/v2_client'

export function ApiKeysSection() {
  const qc = useQueryClient()
  const keysQ = useQuery({ queryKey: ['v2', 'keys'], queryFn: listApiKeys })
  const [creating, setCreating] = useState(false)
  const [label, setLabel] = useState('')
  const [justMinted, setJustMinted] = useState<MintedApiKey | null>(null)

  const mintM = useMutation({
    mutationFn: (lbl: string) => mintApiKey(lbl),
    onSuccess: (data) => {
      setJustMinted(data)
      setLabel('')
      setCreating(false)
      void qc.invalidateQueries({ queryKey: ['v2', 'keys'] })
    },
  })

  const revokeM = useMutation({
    mutationFn: (id: number) => revokeApiKey(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['v2', 'keys'] }),
  })

  const copy = useCallback(async (text: string) => {
    await navigator.clipboard.writeText(text)
  }, [])

  return (
    <div className="space-y-4">
      <div>
        <h3 className="font-medium">API-ключи</h3>
        <p className="text-xs text-neutral-500 mt-0.5">
          Используйте для доступа к API из скриптов или внешних сервисов.
          Передавайте как{' '}
          <code className="px-1 py-0.5 rounded bg-neutral-100 dark:bg-neutral-800 text-xs">
            Authorization: Bearer lc-...
          </code>
        </p>
      </div>

      {justMinted && (
        <div className="rounded-lg border-2 border-emerald-500 bg-emerald-50 dark:bg-emerald-950/30 p-4 space-y-2">
          <p className="text-sm font-semibold text-emerald-900 dark:text-emerald-200">
            ✓ Ключ создан. Сохраните его сейчас — мы покажем его только один раз.
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 px-3 py-2 bg-white dark:bg-neutral-900 border border-emerald-300 dark:border-emerald-800 rounded font-mono text-sm select-all">
              {justMinted.raw}
            </code>
            <button
              onClick={() => void copy(justMinted.raw)}
              className="px-3 py-2 text-sm bg-emerald-500 hover:bg-emerald-600 text-white rounded"
            >
              📋
            </button>
          </div>
          <button
            onClick={() => setJustMinted(null)}
            className="text-xs text-emerald-700 dark:text-emerald-300 hover:underline"
          >
            Я сохранил, скрыть
          </button>
        </div>
      )}

      {!creating && !justMinted && (
        <button
          onClick={() => setCreating(true)}
          className="w-full py-2 border border-dashed border-neutral-300 dark:border-neutral-700 rounded-lg text-sm text-neutral-600 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-900"
        >
          + Создать ключ
        </button>
      )}

      {creating && (
        <div className="flex gap-2 items-end p-3 rounded-lg border border-neutral-200 dark:border-neutral-800">
          <div className="flex-1">
            <label className="text-xs text-neutral-500">
              Название (для себя — необязательно)
            </label>
            <input
              autoFocus
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              maxLength={64}
              placeholder="e.g. ci-bot, mobile-app, ..."
              onKeyDown={(e) =>
                e.key === 'Enter' && !mintM.isPending && mintM.mutate(label)
              }
              className="w-full mt-1 px-3 py-1.5 text-sm bg-white dark:bg-neutral-900 border border-neutral-300 dark:border-neutral-700 rounded"
            />
          </div>
          <button
            onClick={() => mintM.mutate(label)}
            disabled={mintM.isPending}
            className="px-4 py-1.5 text-sm bg-emerald-500 hover:bg-emerald-600 disabled:opacity-50 text-white rounded"
          >
            {mintM.isPending ? 'Создаём…' : 'Создать'}
          </button>
          <button
            onClick={() => {
              setCreating(false)
              setLabel('')
            }}
            className="px-3 py-1.5 text-sm text-neutral-500"
          >
            Отмена
          </button>
        </div>
      )}

      {mintM.isError && (
        <div className="text-sm text-red-600 dark:text-red-400">
          {(mintM.error as Error).message}
        </div>
      )}

      {/* List */}
      <div className="space-y-2">
        {keysQ.isLoading && (
          <div className="text-sm text-neutral-500">Загрузка…</div>
        )}
        {keysQ.data?.length === 0 && !creating && !justMinted && (
          <div className="text-sm text-neutral-500 text-center py-4">
            У вас ещё нет ключей.
          </div>
        )}
        {keysQ.data?.map((k) => (
          <KeyRow
            key={k.id}
            k={k}
            onRevoke={() => {
              if (
                confirm(
                  `Отозвать ключ ${k.prefix}…? Сервисы, использующие его, перестанут работать.`
                )
              ) {
                revokeM.mutate(k.id)
              }
            }}
          />
        ))}
      </div>
    </div>
  )
}

function KeyRow({ k, onRevoke }: { k: ApiKey; onRevoke: () => void }) {
  const lastUsed = k.last_used_at ? new Date(k.last_used_at).toLocaleString() : '—'
  const created = k.created_at ? new Date(k.created_at).toLocaleDateString() : '—'
  const isRevoked = k.revoked_at !== null
  return (
    <div
      className={`flex items-center gap-3 px-3 py-2 rounded-lg border ${
        isRevoked
          ? 'border-neutral-200 dark:border-neutral-800 opacity-50'
          : 'border-neutral-200 dark:border-neutral-800'
      }`}
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <code className="font-mono text-sm">{k.prefix}…</code>
          {k.label && (
            <span className="text-xs text-neutral-500 truncate">— {k.label}</span>
          )}
          {isRevoked && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-neutral-100 dark:bg-neutral-800 text-neutral-500">
              отозван
            </span>
          )}
        </div>
        <div className="text-xs text-neutral-400">
          Создан: {created} • Использован: {lastUsed}
        </div>
      </div>
      {!isRevoked && (
        <button
          onClick={onRevoke}
          className="px-2 py-1 text-xs text-red-600 hover:bg-red-50 dark:hover:bg-red-950/30 rounded"
        >
          Отозвать
        </button>
      )}
    </div>
  )
}
