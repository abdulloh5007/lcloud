import { useState } from 'react'
import { CreateAccountScreen } from './CreateAccountScreen'
import { PasteSeedLogin } from './PasteSeedLogin'
import { BootstrapAdminTGForm } from './BootstrapAdminTGForm'
import type { UserKeypair } from '@/hooks/useAuthV2'

interface Props {
  /** From /auth/state — server says userbot is not connected to admin TG yet. */
  userbotAuthed: boolean
  bootstrapMode: boolean
  /** Called once a user successfully signs in via seed phrase (V2 cookie set). */
  onSignedIn: (kp: UserKeypair) => void
  /** Called when admin completes phone+code login — refresh /auth/state. */
  onAdminConnected: () => void
}

type Mode = 'login' | 'create'

/**
 * Top-level login orchestrator:
 *
 * 1. If the userbot itself isn't connected to a Telegram account → show
 *    the V1 admin phone+code form (this is a one-time bootstrap step;
 *    after success the userbot sends 12 words to Saved Messages).
 *
 * 2. Otherwise → seed-phrase auth (V2). Toggle between Login and Create.
 */
export function LoginScreen({
  userbotAuthed,
  bootstrapMode,
  onSignedIn,
  onAdminConnected,
}: Props) {
  const [mode, setMode] = useState<Mode>('login')

  return (
    <div className="min-h-screen flex items-center justify-center bg-neutral-100 dark:bg-neutral-950 p-4">
      <div className="w-full max-w-md bg-white dark:bg-neutral-900 rounded-2xl shadow-xl p-6 sm:p-8">
        <div className="flex items-center gap-2 mb-6">
          <span className="text-3xl">☁️</span>
          <h1 className="text-2xl font-bold">LCloud</h1>
        </div>

        {!userbotAuthed ? (
          <>
            <p className="text-sm text-zinc-500 mb-4">
              {bootstrapMode
                ? 'Подключите ваш Telegram-аккаунт. После подключения юзербот пришлёт вам сид-фразу администратора в Saved Messages.'
                : 'Юзербот не авторизован — войдите своим Telegram-аккаунтом.'}
            </p>
            <BootstrapAdminTGForm onAuthorized={onAdminConnected} />
          </>
        ) : mode === 'login' ? (
          <PasteSeedLogin
            onSuccess={onSignedIn}
            onCreate={() => setMode('create')}
          />
        ) : (
          <CreateAccountScreen
            onSuccess={onSignedIn}
            onCancel={() => setMode('login')}
          />
        )}
      </div>
    </div>
  )
}
