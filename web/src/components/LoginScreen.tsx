import { useState } from 'react'
import { BuyAccountScreen } from './BuyAccountScreen'
import { PasteSeedLogin } from './PasteSeedLogin'
import { BootstrapAdminTGForm } from './BootstrapAdminTGForm'
import { ForgotSeedModal } from './ForgotSeedModal'
import type { LoginFlowState } from '@/api/types'
import type { UserKeypair } from '@/hooks/useAuthV2'

interface Props {
  userbotAuthed: boolean
  bootstrapMode: boolean
  authFlowState: LoginFlowState
  onSignedIn: (kp: UserKeypair) => void
  onAdminConnected: () => void
}

type Mode = 'login' | 'buy'

export function LoginScreen({
  userbotAuthed,
  bootstrapMode,
  authFlowState,
  onSignedIn,
  onAdminConnected,
}: Props) {
  const [mode, setMode] = useState<Mode>('login')
  const [forgotOpen, setForgotOpen] = useState(false)

  return (
    <>
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
                  ? 'Подключите ваш Telegram-аккаунт. После подключения юзербот пришлёт вам сид-фразу в Saved Messages.'
                  : 'Юзербот не авторизован — войдите своим Telegram-аккаунтом.'}
              </p>
              <BootstrapAdminTGForm
                authFlowState={authFlowState}
                onAuthorized={onAdminConnected}
              />
            </>
          ) : mode === 'login' ? (
            <PasteSeedLogin
              onSuccess={onSignedIn}
              onCreate={() => setMode('buy')}
              onForgot={() => setForgotOpen(true)}
            />
          ) : (
            <BuyAccountScreen onCancel={() => setMode('login')} />
          )}
        </div>
      </div>
      {forgotOpen && <ForgotSeedModal onClose={() => setForgotOpen(false)} />}
    </>
  )
}
