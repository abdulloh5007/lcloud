import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ApiError, payments } from "@/api/client";
import { Check, Clipboard, CreditCard, PartyPopper, Send } from "lucide-react";

interface Props {
  onCancel?: () => void;
}

/**
 * "Купить аккаунт" flow.
 *
 * Steps:
 *   1. Show payment card details (static — Visa 4413..., Abdulloh Ergashev)
 *   2. Buyer pays via bank transfer / Yandex Money / phone-to-card
 *   3. Buyer fills form: their contact (Telegram @username or email/phone),
 *      optional note. Submits.
 *   4. Server queues a payment_request row. Admin reviews and approves.
 *   5. UI shows "Заявка #N принята. Ждите ответ от админа в Telegram/email."
 *
 * Once admin approves, they message the buyer the seed phrase. Buyer then
 * uses "Войти по сид-фразе" tab to log in.
 */
export function BuyAccountScreen({ onCancel }: Props) {
  const info = useQuery({ queryKey: ["payment_info"], queryFn: () => payments.info() });
  const [contact, setContact] = useState("");
  const [note, setNote] = useState("");
  const [copied, setCopied] = useState(false);

  const submit = useMutation({
    mutationFn: () => payments.request(contact.trim(), note.trim() || undefined),
  });

  useEffect(() => {
    if (copied) {
      const t = setTimeout(() => setCopied(false), 1500);
      return () => clearTimeout(t);
    }
  }, [copied]);

  function copyCard() {
    if (!info.data) return;
    navigator.clipboard.writeText(info.data.card_number).then(() => setCopied(true));
  }

  const valid = contact.trim().length >= 2 && contact.trim().length <= 128;

  if (submit.isSuccess) {
    return (
      <div className="space-y-5">
        <div className="rounded-lg border-2 border-emerald-500 bg-emerald-50 dark:bg-emerald-950/30 p-5 text-center">
          <PartyPopper size={34} className="mx-auto mb-2 text-emerald-600" />
          <h2 className="text-lg font-semibold text-emerald-900 dark:text-emerald-200">
            {submit.data.duplicate ? "Заявка уже в очереди" : "Заявка принята"}
          </h2>
          <p className="text-sm text-emerald-800 dark:text-emerald-300 mt-2">
            Номер заявки: <strong>#{submit.data.id}</strong>
          </p>
          <p className="text-xs text-emerald-700 dark:text-emerald-400 mt-3">
            Админ свяжется с вами по адресу <strong>{contact}</strong> и пришлёт
            сид-фразу для входа. Обычно в течение нескольких часов.
          </p>
        </div>

        {onCancel && (
          <button
            onClick={onCancel}
            className="w-full py-2 text-sm text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
          >
            На главную
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-2xl font-bold">Купить аккаунт</h2>
        <p className="text-sm text-zinc-500 mt-1">
          {info.data
            ? `${info.data.tier_label} — $${(info.data.amount_cents / 100).toFixed(2)} ${info.data.currency}`
            : "Загрузка тарифа…"}
        </p>
      </div>

      {info.data && (
        <div className="rounded-2xl bg-gradient-to-br from-blue-600 via-blue-700 to-purple-800 text-white p-5 shadow-lg select-none">
          <div className="flex justify-between items-start mb-6">
            <div>
              <div className="text-xs opacity-75 mb-1">PAY TO</div>
              <div className="font-mono text-sm tracking-wider">
                {info.data.scheme.toUpperCase()}
              </div>
            </div>
            <CreditCard size={28} className="opacity-90" />
          </div>
          <div className="font-mono text-lg tracking-[0.15em] mb-4 break-all">
            {info.data.card_number.match(/.{1,4}/g)?.join(" ")}
          </div>
          <div className="flex justify-between items-end">
            <div>
              <div className="text-[10px] uppercase opacity-60 tracking-widest">Cardholder</div>
              <div className="text-sm font-medium">{info.data.card_holder}</div>
            </div>
            <button
              onClick={copyCard}
              className="inline-flex min-h-10 items-center gap-1.5 px-3 py-1.5 bg-white/15 hover:bg-white/25 rounded-md text-xs backdrop-blur transition-[scale,background-color] duration-150 ease-out active:scale-[0.96]"
            >
              {copied ? <Check size={13} /> : <Clipboard size={13} />}
              {copied ? "Скопировано" : "Копировать №"}
            </button>
          </div>
        </div>
      )}

      <div className="space-y-3">
        <ol className="text-sm text-zinc-600 dark:text-zinc-300 space-y-2 ml-4 list-decimal">
          <li>
            Переведите <strong>${info.data ? (info.data.amount_cents / 100).toFixed(2) : "—"}</strong>{" "}
            (или эквивалент в RUB/UZS/KZT по курсу) на карту выше через банк или мобильное приложение.
          </li>
          <li>
            Укажите ниже свой <strong>Telegram @username</strong>, email или телефон,
            чтобы админ связался с вами и прислал сид-фразу.
          </li>
          <li>
            Дождитесь сообщения от админа (обычно несколько часов).
          </li>
        </ol>
      </div>

      <div className="space-y-3">
        <div>
          <label className="text-xs font-medium text-zinc-600 dark:text-zinc-400 block mb-1">
            Ваш контакт *
          </label>
          <input
            value={contact}
            onChange={(e) => setContact(e.target.value)}
            placeholder="@username, email или телефон"
            maxLength={128}
            autoFocus
            className="w-full px-3 py-2 bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded-lg text-sm focus:border-emerald-500 outline-none"
          />
        </div>
        <div>
          <label className="text-xs font-medium text-zinc-600 dark:text-zinc-400 block mb-1">
            Сообщение админу (необязательно)
          </label>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Например: 'Перевёл через Tinkoff на 700 ₽, имя плательщика Иван И.'"
            rows={3}
            maxLength={500}
            className="w-full px-3 py-2 bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded-lg text-sm focus:border-emerald-500 outline-none resize-none"
          />
        </div>
      </div>

      {submit.isError && (
        <div className="text-sm text-red-600 dark:text-red-400 p-3 bg-red-50 dark:bg-red-950/30 rounded-lg">
          {submit.error instanceof ApiError && submit.error.reason === "rate_limited"
            ? "Слишком много заявок с вашего IP. Попробуйте через час."
            : (submit.error as Error).message}
        </div>
      )}

      <button
        onClick={() => submit.mutate()}
        disabled={!valid || submit.isPending}
        className="inline-flex min-h-11 w-full items-center justify-center gap-2 py-3 bg-emerald-500 hover:bg-emerald-600 disabled:bg-zinc-300 disabled:cursor-not-allowed text-white rounded-lg font-medium transition-[scale,background-color] duration-150 ease-out active:scale-[0.96] disabled:active:scale-100"
      >
        {!submit.isPending && <Send size={16} />}
        {submit.isPending ? "Отправляем…" : "Я оплатил, отправить заявку"}
      </button>

      {onCancel && (
        <button
          onClick={onCancel}
          className="w-full py-2 text-sm text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
        >
          ← Назад к входу
        </button>
      )}
    </div>
  );
}
