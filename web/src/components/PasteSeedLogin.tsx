import { useCallback, useState } from 'react'
import { deriveKeypair, isValidSeedPhrase } from '@/auth/seed'
import { loginWithKeypair } from '@/api/v2_client'
import type { UserKeypair } from '@/hooks/useAuthV2'

interface Props {
  onSuccess: (kp: UserKeypair) => void
  onCreate?: () => void
}

/**
 * Existing-account login: paste seed phrase → derive → challenge/verify.
 *
 * The mnemonic is held in component state only as long as the user is
 * typing it. After login succeeds we drop it from memory and only
 * propagate the keypair to the parent.
 */
export function PasteSeedLogin({ onSuccess, onCreate }: Props) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const trimmed = text.trim().replace(/\s+/g, ' ')
  const valid = isValidSeedPhrase(trimmed)

  const submit = useCallback(async () => {
    if (!valid || busy) return
    setBusy(true)
    setErr(null)
    try {
      const ident = await deriveKeypair(trimmed)
      await loginWithKeypair(ident.privkeySeed, ident.pubkeyHex)
      // Cleanse the textarea on success
      setText('')
      onSuccess({
        pubkey: ident.pubkey,
        privkeySeed: ident.privkeySeed,
        pubkeyHex: ident.pubkeyHex,
      })
    } catch (e) {
      const status = (e as { status?: number } | null)?.status
      if (status === 403) {
        setErr('Аккаунт заблокирован.')
      } else if (status === 401) {
        setErr('Не удалось проверить подпись. Проверьте сид-фразу.')
      } else {
        setErr((e as Error).message ?? 'Ошибка входа')
      }
      setBusy(false)
    }
  }, [trimmed, valid, busy, onSuccess])

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-bold">Войти по сид-фразе</h2>
      <p className="text-sm text-zinc-500">
        Вставьте 12 или 24 слова, разделённых пробелами.
      </p>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="abandon ability able about above absent absorb abstract absurd abuse access accident"
        rows={4}
        autoComplete="off"
        spellCheck={false}
        className="w-full p-3 font-mono text-sm bg-zinc-50 dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded-lg focus:border-emerald-500 outline-none"
      />
      <div className="flex items-center justify-between text-xs text-zinc-500">
        <span>{trimmed ? `${trimmed.split(' ').length} слов` : 'Введите фразу…'}</span>
        {trimmed && !valid && (
          <span className="text-amber-600 dark:text-amber-400">
            ⚠️ Невалидная BIP39 фраза
          </span>
        )}
        {valid && <span className="text-emerald-600 dark:text-emerald-400">✓ ОК</span>}
      </div>

      {err && (
        <div className="text-sm text-red-600 dark:text-red-400 p-3 bg-red-50 dark:bg-red-950/30 rounded-lg">
          {err}
        </div>
      )}

      <button
        onClick={submit}
        disabled={!valid || busy}
        className="w-full py-3 bg-emerald-500 hover:bg-emerald-600 disabled:bg-zinc-300 disabled:cursor-not-allowed text-white rounded-lg font-medium"
      >
        {busy ? 'Проверяем…' : 'Войти'}
      </button>

      {onCreate && (
        <button
          onClick={onCreate}
          className="w-full py-2 text-sm text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
        >
          Создать новый аккаунт →
        </button>
      )}
    </div>
  )
}
