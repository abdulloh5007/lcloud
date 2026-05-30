import { useCallback, useState } from 'react'
import { deriveKeypair, isValidSeedPhrase } from '@/auth/seed'
import { loginWithKeypair } from '@/api/v2_client'
import type { UserKeypair } from '@/hooks/useAuthV2'
import { PinSetupModal } from './PinSetupModal'

interface Props {
  onSuccess: (kp: UserKeypair) => void
  onCreate?: () => void
  onForgot?: () => void
}

/**
 * Existing-account login: paste seed phrase → derive → challenge/verify.
 *
 * After login succeeds, if the user hasn't dismissed the PIN-setup
 * prompt for this pubkey before, we offer to encrypt the mnemonic
 * with a 4-digit PIN and ship it to the server for later recovery.
 */
export function PasteSeedLogin({ onSuccess, onCreate, onForgot }: Props) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [postLoginPinPrompt, setPostLoginPinPrompt] = useState<{
    mnemonic: string
    keypair: UserKeypair
  } | null>(null)

  const trimmed = text.trim().replace(/\s+/g, ' ')
  const valid = isValidSeedPhrase(trimmed)

  const submit = useCallback(async () => {
    if (!valid || busy) return
    setBusy(true)
    setErr(null)
    try {
      const ident = await deriveKeypair(trimmed)
      await loginWithKeypair(ident.privkeySeed, ident.pubkeyHex)
      const kp: UserKeypair = {
        pubkey: ident.pubkey,
        privkeySeed: ident.privkeySeed,
        pubkeyHex: ident.pubkeyHex,
      }
      // Has the user already dismissed/accepted the PIN prompt for this pubkey?
      const dismissedKey = `pin_prompt_dismissed:${ident.pubkeyHex}`
      const dismissed = localStorage.getItem(dismissedKey) === 'true'
      if (!dismissed) {
        setPostLoginPinPrompt({ mnemonic: trimmed, keypair: kp })
      } else {
        setText('')
        onSuccess(kp)
      }
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

  if (postLoginPinPrompt) {
    return (
      <PinSetupModal
        modal={false}
        mnemonic={postLoginPinPrompt.mnemonic}
        onDone={() => {
          localStorage.setItem(
            `pin_prompt_dismissed:${postLoginPinPrompt.keypair.pubkeyHex}`,
            'true'
          )
          const kp = postLoginPinPrompt.keypair
          setPostLoginPinPrompt(null)
          setText('')
          onSuccess(kp)
        }}
      />
    )
  }

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
        <span>
          {trimmed ? `${trimmed.split(' ').length} слов` : 'Введите фразу…'}
        </span>
        {trimmed && !valid && (
          <span className="text-amber-600 dark:text-amber-400">
            ⚠️ Невалидная BIP39 фраза
          </span>
        )}
        {valid && (
          <span className="text-emerald-600 dark:text-emerald-400">✓ ОК</span>
        )}
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

      {onForgot && (
        <button
          onClick={onForgot}
          className="w-full py-2 text-xs text-blue-600 hover:text-blue-700 dark:text-blue-400"
        >
          Забыли сид-фразу? Восстановить по PIN →
        </button>
      )}

      {onCreate && (
        <button
          onClick={onCreate}
          className="w-full py-2 text-sm text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
        >
          Купить новый аккаунт →
        </button>
      )}
    </div>
  )
}
