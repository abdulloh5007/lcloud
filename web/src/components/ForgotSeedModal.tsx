import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ApiError, pinApi } from "@/api/client";
import { Check, Clipboard, KeyRound, UnlockKeyhole } from "lucide-react";

interface Props {
  onClose: () => void;
}

/**
 * "Forgot seed phrase?" recovery modal.
 *
 * Anonymous flow — no auth required. User provides:
 *   - their contact_handle (the @username/email/phone given when buying)
 *   - 4-digit PIN they set up earlier
 *
 * On success, the seed phrase is shown for 60 seconds with a copy button.
 * Backend rate-limits 10/h per IP and locks the account for 1h after 5
 * wrong PINs.
 */
export function ForgotSeedModal({ onClose }: Props) {
  const [contact, setContact] = useState("");
  const [digits, setDigits] = useState<string[]>(["", "", "", ""]);
  const [revealed, setRevealed] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const inputs = useRef<(HTMLInputElement | null)[]>([]);

  const recoverM = useMutation({
    mutationFn: (pin: string) => pinApi.recover(contact.trim(), pin),
    onSuccess: (data) => setRevealed(data.mnemonic),
  });

  function handleDigit(idx: number, val: string) {
    const cleaned = val.replace(/\D/g, "").slice(-1);
    const next = [...digits];
    next[idx] = cleaned;
    setDigits(next);
    if (cleaned && idx < 3) inputs.current[idx + 1]?.focus();
  }

  function handleBackspace(
    idx: number,
    e: React.KeyboardEvent<HTMLInputElement>
  ) {
    if (e.key === "Backspace" && !digits[idx] && idx > 0) {
      inputs.current[idx - 1]?.focus();
    }
  }

  // When 4 digits entered + contact filled → submit
  useEffect(() => {
    if (
      digits.every((d) => d !== "") &&
      contact.trim().length >= 2 &&
      !recoverM.isPending &&
      !revealed
    ) {
      recoverM.mutate(digits.join(""));
    }
  }, [digits, contact, recoverM, revealed]);

  function copy() {
    if (revealed) {
      navigator.clipboard.writeText(revealed);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  }

  // Auto-close revealed view after 90 seconds
  useEffect(() => {
    if (!revealed) return;
    const t = setTimeout(() => onClose(), 90_000);
    return () => clearTimeout(t);
  }, [revealed, onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="bg-white dark:bg-neutral-900 rounded-2xl shadow-xl p-6 max-w-md w-full max-h-[90vh] overflow-y-auto">
        {revealed ? (
          <div className="space-y-4">
            <div className="text-center">
              <UnlockKeyhole size={34} className="mx-auto mb-2 text-emerald-600" />
              <h2 className="text-xl font-bold">Ваша сид-фраза</h2>
              <p className="text-xs text-zinc-500 mt-2">
                Сохраните прямо сейчас. Окно закроется автоматически через
                90 секунд.
              </p>
            </div>
            <div className="grid grid-cols-3 sm:grid-cols-4 gap-1.5 p-3 rounded-lg bg-amber-50 dark:bg-amber-950/30 border-2 border-amber-400 dark:border-amber-700 font-mono text-xs">
              {revealed.split(" ").map((w, i) => (
                <div
                  key={i}
                  className="flex items-center gap-1 px-2 py-1 bg-white dark:bg-neutral-800 rounded border border-amber-200 dark:border-amber-800"
                >
                  <span className="text-[10px] text-zinc-400 w-4 text-right">
                    {i + 1}
                  </span>
                  <span className="select-all">{w}</span>
                </div>
              ))}
            </div>
            <button
              onClick={copy}
              className="inline-flex w-full items-center justify-center gap-2 py-2 text-sm bg-blue-500 hover:bg-blue-600 text-white rounded-lg"
            >
              {copied ? <Check size={15} /> : <Clipboard size={15} />}
              {copied ? "Скопировано" : "Копировать"}
            </button>
            <button
              onClick={onClose}
              className="w-full py-2 text-sm bg-emerald-500 hover:bg-emerald-600 text-white rounded-lg"
            >
              Я сохранил, закрыть
            </button>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="text-center">
              <KeyRound size={34} className="mx-auto mb-2 text-blue-600" />
              <h2 className="text-xl font-bold">Восстановление</h2>
              <p className="text-sm text-zinc-500 mt-2">
                Введите контакт, который вы указывали при покупке аккаунта,
                и 4-значный PIN.
              </p>
            </div>
            <div>
              <label className="text-xs font-medium text-zinc-600 dark:text-zinc-400 block mb-1">
                Контакт (@username / email / phone)
              </label>
              <input
                value={contact}
                onChange={(e) => setContact(e.target.value)}
                placeholder="@your_username"
                autoFocus
                maxLength={128}
                className="w-full px-3 py-2 bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded-lg text-sm focus:border-emerald-500 outline-none"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-zinc-600 dark:text-zinc-400 block mb-2">
                PIN
              </label>
              <div className="flex gap-2 justify-center">
                {Array.from({ length: 4 }).map((_, i) => (
                  <input
                    key={i}
                    ref={(el) => {
                      inputs.current[i] = el;
                    }}
                    type="tel"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    maxLength={1}
                    value={digits[i]}
                    onChange={(e) => handleDigit(i, e.target.value)}
                    onKeyDown={(e) => handleBackspace(i, e)}
                    className="w-14 h-14 text-2xl text-center border-2 border-zinc-300 dark:border-zinc-700 rounded-xl bg-white dark:bg-zinc-900 focus:border-emerald-500 outline-none font-mono"
                  />
                ))}
              </div>
            </div>

            {recoverM.isError && (
              <div className="text-sm p-3 bg-red-50 dark:bg-red-950/30 rounded-lg text-red-600 dark:text-red-400">
                {humanizeError(recoverM.error as Error)}
              </div>
            )}
            {recoverM.isPending && (
              <div className="text-center text-sm text-zinc-500">
                Проверяем…
              </div>
            )}

            <button
              onClick={onClose}
              className="w-full py-2 text-sm text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
            >
              Отмена
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function humanizeError(err: Error): string {
  if (err instanceof ApiError) {
    const reason = (err.detail as { reason?: string; attempts_left?: number; retry_after_seconds?: number } | null)
      ?.reason;
    const detail = err.detail as {
      reason?: string;
      attempts_left?: number;
      retry_after_seconds?: number;
    } | null;
    if (reason === "wrong_pin") {
      return `Неверный PIN. Осталось попыток: ${detail?.attempts_left ?? "?"}`;
    }
    if (reason === "locked") {
      const mins = Math.ceil((detail?.retry_after_seconds ?? 3600) / 60);
      return `Аккаунт заблокирован на ${mins} мин из-за неверных попыток.`;
    }
    if (reason === "rate_limited") {
      return "Слишком много попыток с вашего IP. Попробуйте через час.";
    }
    if (reason === "not_found") {
      return "Контакт не найден или PIN не настроен.";
    }
  }
  return err.message;
}
