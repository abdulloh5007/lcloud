import { useEffect, useMemo, useState } from 'react'
import { ApiError, auth } from '@/api/client'
import type { LoginFlowState } from '@/api/types'
import { Button } from './ui/Button'
import { TextField } from './ui/TextField'

interface Props {
  authFlowState: LoginFlowState
  onAuthorized: () => void
}

type Stage = 'phone' | 'code' | 'password'
const STAGE_KEY = 'lcloud:telegram-login-stage'
const PHONE_KEY = 'lcloud:telegram-login-phone'

function stageFromFlow(state: LoginFlowState): Stage {
  if (state === 'pwd_needed') return 'password'
  if (state === 'code_sent') return 'code'
  return 'phone'
}

function readStoredStage(fallback: Stage): Stage {
  const stored = sessionStorage.getItem(STAGE_KEY)
  return stored === 'phone' || stored === 'code' || stored === 'password'
    ? stored
    : fallback
}

/**
 * One-time admin TG-account connection: phone → code → (optional 2FA password).
 *
 * After this completes, the userbot:
 *   - claims the admin's TG ID (writes data/keys/admin.tgid stamp)
 *   - generates a 12-word BIP39 seed for web auth
 *   - sends those 12 words to the admin's Saved Messages
 *
 * The user then uses those 12 words to log in via seed-phrase flow.
 */
export function BootstrapAdminTGForm({ authFlowState, onAuthorized }: Props) {
  const initialStage = useMemo(() => stageFromFlow(authFlowState), [authFlowState])
  const [stage, setStage] = useState<Stage>(() =>
    initialStage === 'phone' ? 'phone' : readStoredStage(initialStage)
  )
  const [phone, setPhone] = useState(() => sessionStorage.getItem(PHONE_KEY) ?? '')
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (authFlowState === 'code_sent' && stage === 'phone') {
      setStage('code')
    } else if (authFlowState === 'pwd_needed' && stage !== 'password') {
      setStage('password')
    }
  }, [authFlowState, stage])

  useEffect(() => {
    sessionStorage.setItem(STAGE_KEY, stage)
  }, [stage])

  useEffect(() => {
    if (phone.trim()) sessionStorage.setItem(PHONE_KEY, phone)
  }, [phone])

  function clearStoredFlow() {
    sessionStorage.removeItem(STAGE_KEY)
    sessionStorage.removeItem(PHONE_KEY)
  }

  async function submitPhone() {
    if (!phone.trim() || busy) return
    setBusy(true)
    setErr(null)
    try {
      await auth.start(phone.trim())
      setStage('code')
    } catch (e) {
      if (e instanceof ApiError && e.reason === 'flow_already_active') {
        setStage('code')
        setErr(null)
      } else {
        setErr(humanizeApiError(e))
      }
    } finally {
      setBusy(false)
    }
  }

  async function submitCode() {
    if (!code.trim() || busy) return
    setBusy(true)
    setErr(null)
    try {
      const r = await auth.code(code.trim())
      if (('need_password' in r && r.need_password) || ('state' in r && r.state === 'pwd_needed')) {
        setStage('password')
      } else {
        clearStoredFlow()
        onAuthorized()
      }
    } catch (e) {
      setErr(humanizeApiError(e))
    } finally {
      setBusy(false)
    }
  }

  async function submitPassword() {
    if (!password || busy) return
    setBusy(true)
    setErr(null)
    try {
      await auth.password(password)
      clearStoredFlow()
      onAuthorized()
    } catch (e) {
      setErr(humanizeApiError(e))
    } finally {
      setBusy(false)
    }
  }

  async function cancel() {
    try {
      await auth.cancel()
    } finally {
      setStage('phone')
      setCode('')
      setPassword('')
      setErr(null)
      clearStoredFlow()
    }
  }

  return (
    <div className="space-y-4">
      {stage === 'phone' && (
        <>
          <TextField
            label="Номер телефона"
            placeholder="+71234567890"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submitPhone()}
            autoFocus
          />
          <Button onClick={submitPhone} disabled={!phone.trim() || busy}>
            {busy ? 'Отправляем код…' : 'Получить код'}
          </Button>
        </>
      )}
      {stage === 'code' && (
        <>
          <p className="text-xs text-zinc-500">
            {phone ? `Код отправлен в Telegram на ${phone}` : 'Код уже отправлен в Telegram.'}
          </p>
          <TextField
            label="Код из Telegram"
            placeholder="12345"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submitCode()}
            autoFocus
          />
          <div className="flex gap-2">
            <Button onClick={submitCode} disabled={!code.trim() || busy}>
              {busy ? 'Проверяем…' : 'Войти'}
            </Button>
            <button
              onClick={cancel}
              className="px-3 py-2 text-sm text-zinc-500 hover:text-zinc-700"
            >
              Отмена
            </button>
          </div>
        </>
      )}
      {stage === 'password' && (
        <>
          <p className="text-xs text-zinc-500">
            У аккаунта {phone} включена 2FA. Введите облачный пароль Telegram.
          </p>
          <TextField
            label="Облачный пароль"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submitPassword()}
            autoFocus
          />
          <div className="flex gap-2">
            <Button onClick={submitPassword} disabled={!password || busy}>
              {busy ? 'Проверяем…' : 'Войти'}
            </Button>
            <button
              onClick={cancel}
              className="px-3 py-2 text-sm text-zinc-500 hover:text-zinc-700"
            >
              Отмена
            </button>
          </div>
        </>
      )}
      {err && (
        <div className="text-sm text-red-600 dark:text-red-400 p-3 bg-red-50 dark:bg-red-950/30 rounded-lg">
          {err}
        </div>
      )}
    </div>
  )
}

function humanizeApiError(e: unknown): string {
  if (e instanceof ApiError) {
    const reason = (e.detail as { reason?: string } | null)?.reason
    if (reason === 'rate_limited') return 'Слишком много попыток, подождите минуту.'
    if (reason === 'wrong_code') return 'Неверный код.'
    if (reason === 'wrong_password') return 'Неверный пароль.'
    if (reason === 'phone_invalid') return 'Неверный формат номера.'
    if (reason === 'wrong_account') return 'Этот аккаунт не подходит — нужен исходный.'
    if (reason === 'expired') return 'Код истёк, начните заново.'
    if (reason === 'flow_already_active') return 'Код уже отправлен. Введите код или нажмите «Отмена», чтобы начать заново.'
  }
  return (e as Error).message ?? 'Ошибка'
}
