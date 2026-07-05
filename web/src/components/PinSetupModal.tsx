import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { pinApi } from "@/api/client";

interface Props {
  /** Mnemonic to encrypt — must be the user's own (server validates). */
  mnemonic: string;
  /** Called after successful setup (or after user dismisses). */
  onDone: () => void;
  /** Show as full-screen modal (true) or inline (false). */
  modal?: boolean;
}

/**
 * Modal that prompts the user to set a 4-digit PIN for seed-phrase recovery.
 *
 * Shown right after the first successful login from `PasteSeedLogin` —
 * the mnemonic is still in browser memory at that point and gets shipped
 * to the server (encrypted with the PIN-derived key).
 *
 * On 4-digit completion, sends `{pin, mnemonic}` to `/auth/v2/pin/setup`.
 * The server verifies the mnemonic actually belongs to the logged-in user
 * before storing.
 */
export function PinSetupModal({ mnemonic, onDone, modal = true }: Props) {
  const [digits, setDigits] = useState<string[]>(["", "", "", ""]);
  const [confirm, setConfirm] = useState<string[]>(["", "", "", ""]);
  const [stage, setStage] = useState<"enter" | "confirm">("enter");
  const inputs = useRef<(HTMLInputElement | null)[]>([]);
  const submittedPin = useRef<string | null>(null);

  const setupM = useMutation({
    mutationFn: (pin: string) => pinApi.setup(pin, mnemonic),
    onSuccess: () => onDone(),
    onError: () => {
      submittedPin.current = null;
    },
  });

  // Auto-advance focus
  function handleDigit(idx: number, val: string, target: "enter" | "confirm") {
    const cleaned = val.replace(/\D/g, "").slice(-1); // last digit only
    if (target === "enter") {
      const next = [...digits];
      next[idx] = cleaned;
      setDigits(next);
    } else {
      const next = [...confirm];
      next[idx] = cleaned;
      setConfirm(next);
    }
    if (cleaned && idx < 3) {
      // focus next
      const nextInput = inputs.current[idx + 1];
      if (nextInput) nextInput.focus();
    }
  }

  function handleBackspace(
    idx: number,
    e: React.KeyboardEvent<HTMLInputElement>,
    target: "enter" | "confirm"
  ) {
    if (e.key === "Backspace") {
      const arr = target === "enter" ? digits : confirm;
      if (!arr[idx] && idx > 0) {
        const prev = inputs.current[idx - 1];
        if (prev) prev.focus();
      }
    }
  }

  // When all 4 entered → advance
  useEffect(() => {
    if (stage === "enter" && digits.every((d) => d !== "")) {
      setStage("confirm");
      // focus first confirm input on next tick
      setTimeout(() => inputs.current[0]?.focus(), 0);
    }
  }, [digits, stage]);

  // When confirm filled → submit if matches
  useEffect(() => {
    if (stage === "confirm" && confirm.every((d) => d !== "")) {
      const enterPin = digits.join("");
      const confirmPin = confirm.join("");
      if (
        enterPin === confirmPin &&
        !setupM.isPending &&
        submittedPin.current !== enterPin
      ) {
        submittedPin.current = enterPin;
        setupM.mutate(enterPin);
      }
    }
  }, [confirm, stage, digits, setupM]);

  const enterPin = digits.join("");
  const confirmPin = confirm.join("");
  const mismatch =
    stage === "confirm" &&
    confirm.every((d) => d !== "") &&
    enterPin !== confirmPin;

  const content = (
    <div className="space-y-4">
      <div className="text-center">
        <div className="text-3xl mb-2">🔒</div>
        <h2 className="text-xl font-bold">Установите PIN-код</h2>
        <p className="text-sm text-zinc-500 mt-2">
          Если вы потеряете сид-фразу — этот 4-значный PIN поможет её
          восстановить.
        </p>
      </div>

      <div className="space-y-2">
        <div className="text-xs font-medium text-center text-zinc-600 dark:text-zinc-400">
          {stage === "enter" ? "Введите PIN" : "Подтвердите PIN"}
        </div>
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
              value={stage === "enter" ? digits[i] : confirm[i]}
              onChange={(e) => handleDigit(i, e.target.value, stage)}
              onKeyDown={(e) => handleBackspace(i, e, stage)}
              autoFocus={i === 0}
              className="w-14 h-14 text-2xl text-center border-2 border-zinc-300 dark:border-zinc-700 rounded-xl bg-white dark:bg-zinc-900 focus:border-emerald-500 outline-none font-mono"
            />
          ))}
        </div>
        {mismatch && (
          <div className="text-xs text-red-500 text-center mt-2">
            PIN не совпадает. Попробуйте ещё раз:
          </div>
        )}
        {mismatch && (
          <button
            onClick={() => {
              setConfirm(["", "", "", ""]);
              setStage("enter");
              setDigits(["", "", "", ""]);
              submittedPin.current = null;
              setTimeout(() => inputs.current[0]?.focus(), 0);
            }}
            className="block mx-auto text-sm text-blue-600"
          >
            Начать заново
          </button>
        )}
      </div>

      <div className="rounded-lg bg-amber-50 dark:bg-amber-950/30 p-3 text-xs text-amber-800 dark:text-amber-300">
        ⚠️ После 5 неверных попыток восстановления — аккаунт блокируется на 1
        час. Запомните PIN.
      </div>

      {setupM.isError && (
        <div className="text-sm text-red-500 p-2 bg-red-50 dark:bg-red-950/30 rounded">
          {(setupM.error as Error).message}
        </div>
      )}
      {setupM.isPending && (
        <div className="text-center text-sm text-zinc-500">Шифруем…</div>
      )}

      <button
        onClick={onDone}
        className="w-full py-2 text-sm text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
      >
        Пропустить (не рекомендуется)
      </button>
    </div>
  );

  if (!modal) return content;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="bg-white dark:bg-neutral-900 rounded-2xl shadow-xl p-6 max-w-sm w-full">
        {content}
      </div>
    </div>
  );
}
