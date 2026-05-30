import { useCallback, useState } from 'react'
import { deriveKeypair, generateSeedPhrase } from '@/auth/seed'
import { loginWithKeypair } from '@/api/v2_client'
import type { UserKeypair } from '@/hooks/useAuthV2'

interface Props {
  onSuccess: (kp: UserKeypair) => void
  onCancel?: () => void
}

/**
 * Generate-new-account screen.
 *
 * Flow:
 *   1. User picks 12 or 24 words (default 12)
 *   2. Click "Сгенерировать" → fresh BIP39 phrase displayed BIG
 *   3. User sees a strong warning + "Я сохранил" checkbox
 *   4. User clicks "Войти" → derive keypair → challenge/verify → done
 *
 * The mnemonic is shown EXACTLY ONCE. We never persist it. Once the
 * user clicks "Войти" we discard `mnemonic` from state.
 */
export function CreateAccountScreen({ onSuccess, onCancel }: Props) {
  const [words, setWords] = useState<12 | 24>(12)
  const [mnemonic, setMnemonic] = useState<string | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const generate = useCallback(() => {
    setMnemonic(generateSeedPhrase(words))
    setConfirmed(false)
    setErr(null)
  }, [words])

  const proceed = useCallback(async () => {
    if (!mnemonic) return
    setBusy(true)
    setErr(null)
    try {
      const ident = await deriveKeypair(mnemonic)
      const result = await loginWithKeypair(ident.privkeySeed, ident.pubkeyHex)
      if (!result.registered) {
        setErr('Этот аккаунт уже зарегистрирован — войдите вместо создания.')
        setBusy(false)
        return
      }
      // Success — cleanse mnemonic from memory before passing keypair up
      setMnemonic(null)
      onSuccess({
        pubkey: ident.pubkey,
        privkeySeed: ident.privkeySeed,
        pubkeyHex: ident.pubkeyHex,
      })
    } catch (e) {
      setErr((e as Error).message ?? 'Не удалось войти')
      setBusy(false)
    }
  }, [mnemonic, onSuccess])

  const copy = useCallback(() => {
    if (mnemonic) navigator.clipboard.writeText(mnemonic)
  }, [mnemonic])

  if (!mnemonic) {
    return (
      <div className="space-y-5">
        <h2 className="text-2xl font-bold">Создать новый аккаунт</h2>
        <p className="text-sm text-zinc-500">
          Сгенерируется случайная сид-фраза. Это ваш приватный ключ — её
          невозможно восстановить, если вы потеряете.
        </p>
        <div className="flex gap-2">
          {([12, 24] as const).map((n) => (
            <button
              key={n}
              onClick={() => setWords(n)}
              className={`px-4 py-2 rounded-lg border ${
                words === n
                  ? 'border-emerald-500 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
                  : 'border-zinc-300 dark:border-zinc-700'
              }`}
            >
              {n} слов
            </button>
          ))}
        </div>
        <button
          onClick={generate}
          className="w-full py-3 bg-emerald-500 hover:bg-emerald-600 text-white rounded-lg font-medium"
        >
          Сгенерировать сид-фразу
        </button>
        {onCancel && (
          <button
            onClick={onCancel}
            className="w-full py-2 text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
          >
            Назад
          </button>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-5">
      <h2 className="text-2xl font-bold">Ваша сид-фраза</h2>
      <div className="rounded-lg border-2 border-amber-500 bg-amber-50 dark:bg-amber-950/30 p-4 text-sm space-y-2">
        <p className="font-semibold text-amber-900 dark:text-amber-200">
          ⚠️ Сохраните эти слова СЕЙЧАС
        </p>
        <ul className="list-disc list-inside text-amber-800 dark:text-amber-300 space-y-1">
          <li>Это единственный способ войти в аккаунт.</li>
          <li>Если потеряете — все файлы пропадут навсегда.</li>
          <li>Никому не показывайте. Кто получит — станет вами.</li>
          <li>Запишите на бумаге или в менеджере паролей.</li>
        </ul>
      </div>

      <div className="grid grid-cols-3 sm:grid-cols-4 gap-2 p-4 bg-zinc-100 dark:bg-zinc-900 rounded-lg font-mono text-base">
        {mnemonic.split(' ').map((w, i) => (
          <div
            key={i}
            className="flex items-center gap-1 px-2 py-1.5 bg-white dark:bg-zinc-800 rounded border border-zinc-200 dark:border-zinc-700"
          >
            <span className="text-xs text-zinc-400 w-5 text-right">{i + 1}.</span>
            <span className="select-all">{w}</span>
          </div>
        ))}
      </div>

      <button
        onClick={copy}
        className="w-full py-2 text-sm border border-zinc-300 dark:border-zinc-700 rounded-lg hover:bg-zinc-50 dark:hover:bg-zinc-900"
      >
        📋 Скопировать
      </button>

      <label className="flex items-center gap-3 p-3 rounded-lg border border-zinc-300 dark:border-zinc-700 cursor-pointer hover:bg-zinc-50 dark:hover:bg-zinc-900">
        <input
          type="checkbox"
          checked={confirmed}
          onChange={(e) => setConfirmed(e.target.checked)}
          className="w-5 h-5 accent-emerald-500"
        />
        <span className="text-sm">Я сохранил сид-фразу в надёжном месте</span>
      </label>

      {err && (
        <div className="text-sm text-red-600 dark:text-red-400 p-3 bg-red-50 dark:bg-red-950/30 rounded-lg">
          {err}
        </div>
      )}

      <button
        onClick={proceed}
        disabled={!confirmed || busy}
        className="w-full py-3 bg-emerald-500 hover:bg-emerald-600 disabled:bg-zinc-300 disabled:cursor-not-allowed text-white rounded-lg font-medium"
      >
        {busy ? 'Вход…' : 'Войти'}
      </button>

      <button
        onClick={() => {
          setMnemonic(null)
          setConfirmed(false)
        }}
        className="w-full py-2 text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300 text-sm"
      >
        Сгенерировать заново
      </button>
    </div>
  )
}
