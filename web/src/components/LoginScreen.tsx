import { useState } from "react";
import { ApiError, auth } from "@/api/client";
import { Button } from "./ui/Button";
import { TextField } from "./ui/TextField";

interface Props {
  bootstrapMode: boolean;
  userbotAuthed: boolean;
  onAuthorized: () => void;
}

type Stage = "phone" | "code" | "password";

export function LoginScreen({
  bootstrapMode,
  userbotAuthed,
  onAuthorized,
}: Props) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-neutral-100 dark:bg-neutral-950 p-4">
      <div className="w-full max-w-sm bg-panel dark:bg-panel-dark rounded-2xl shadow-xl p-6">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-2xl">☁️</span>
          <h1 className="text-xl font-semibold">LCloud</h1>
        </div>

        {bootstrapMode || !userbotAuthed ? (
          <BootstrapForm bootstrapMode={bootstrapMode} onAuthorized={onAuthorized} />
        ) : (
          <MagicLinkInstructions userbotAuthed={userbotAuthed} />
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- magic-link

function MagicLinkInstructions({ userbotAuthed }: { userbotAuthed: boolean }) {
  return (
    <div>
      <p className="text-sm text-neutral-500 dark:text-neutral-400 mb-5">
        Вход в админ-панель — через одноразовую ссылку из Telegram.
      </p>

      <ol className="space-y-3 text-sm">
        <li className="flex gap-2">
          <span className="font-mono text-neutral-400">1.</span>
          <span>
            Открой <b>Saved Messages</b> в Telegram под своим аккаунтом
            {!userbotAuthed && (
              <span className="block text-xs text-amber-700 dark:text-amber-400 mt-0.5">
                (юзербот сейчас не авторизован — пиши разработчику)
              </span>
            )}
          </span>
        </li>
        <li className="flex gap-2">
          <span className="font-mono text-neutral-400">2.</span>
          <span>
            Отправь команду{" "}
            <code className="px-1.5 py-0.5 rounded bg-neutral-100 dark:bg-neutral-800 text-blue-600 dark:text-blue-400 font-mono text-xs">
              /admin
            </code>
          </span>
        </li>
        <li className="flex gap-2">
          <span className="font-mono text-neutral-400">3.</span>
          <span>Тапни по ссылке, которую пришлёт бот в Saved Messages</span>
        </li>
      </ol>

      <p className="mt-6 text-xs text-neutral-400">
        Ссылка одноразовая, действует 15 минут. Каждый клик создаёт сессию на 7
        дней — повторно команду до истечения слать не нужно.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------- bootstrap (phone+code)

function BootstrapForm({
  bootstrapMode,
  onAuthorized,
}: {
  bootstrapMode: boolean;
  onAuthorized: () => void;
}) {
  const [stage, setStage] = useState<Stage>("phone");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function withBusy<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setError(null);
    setBusy(true);
    try {
      return await fn();
    } catch (e) {
      if (e instanceof ApiError) {
        setError(`${e.reason} (${e.status})`);
      } else if (e instanceof Error) {
        setError(e.message);
      } else {
        setError("unknown error");
      }
      return undefined;
    } finally {
      setBusy(false);
    }
  }

  async function submitPhone() {
    if (!phone.trim()) return;
    const res = await withBusy(() => auth.start(phone.trim()));
    if (res) setStage("code");
  }

  async function submitCode() {
    const res = await withBusy(() => auth.code(code.trim()));
    if (!res) return;
    if ("need_password" in res && res.need_password) {
      setStage("password");
    } else if ("authorized" in res) {
      onAuthorized();
    }
  }

  async function submitPassword() {
    const res = await withBusy(() => auth.password(password));
    if (res) onAuthorized();
  }

  async function cancelFlow() {
    await withBusy(() => auth.cancel());
    setStage("phone");
    setCode("");
    setPassword("");
  }

  return (
    <>
      <p className="text-sm text-neutral-500 dark:text-neutral-400 mb-6">
        {stage === "phone" &&
          (bootstrapMode
            ? "Bootstrap: подключите Telegram аккаунт"
            : "Юзербот не авторизован — войдите своим аккаунтом")}
        {stage === "code" && "Введите код из Telegram"}
        {stage === "password" && "Введите 2FA пароль"}
      </p>

      {stage === "phone" && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submitPhone();
          }}
          className="flex flex-col gap-3"
        >
          <TextField
            label="Номер телефона"
            type="tel"
            autoFocus
            placeholder="+1234567890"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            autoComplete="tel"
            required
          />
          <Button loading={busy} disabled={phone.trim().length < 5}>
            Отправить код
          </Button>
        </form>
      )}

      {stage === "code" && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submitCode();
          }}
          className="flex flex-col gap-3"
        >
          <TextField
            label="Код"
            autoFocus
            inputMode="numeric"
            placeholder="12345"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            required
          />
          <div className="flex gap-2">
            <Button type="submit" loading={busy} disabled={!code.trim()}>
              Продолжить
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => void cancelFlow()}
            >
              Отменить
            </Button>
          </div>
        </form>
      )}

      {stage === "password" && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submitPassword();
          }}
          className="flex flex-col gap-3"
        >
          <TextField
            label="2FA Password"
            type="password"
            autoFocus
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          <div className="flex gap-2">
            <Button type="submit" loading={busy} disabled={!password}>
              Войти
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => void cancelFlow()}
            >
              Отменить
            </Button>
          </div>
        </form>
      )}

      {error && (
        <div className="mt-4 rounded-lg bg-red-50 dark:bg-red-950/40 border border-red-200 dark:border-red-800 px-3 py-2 text-sm text-red-700 dark:text-red-300">
          {error}
        </div>
      )}
      <p className="mt-6 text-xs text-neutral-400">
        {bootstrapMode
          ? "Это первый bootstrap-вход. После успеха admin-id привяжется к этому аккаунту, и далее вход будет только через /admin-ссылку из Telegram."
          : "После входа дальнейшие сессии можно открывать через /admin-ссылку в Saved Messages — без повторного ввода кода."}
      </p>
    </>
  );
}
