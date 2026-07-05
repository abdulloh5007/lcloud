import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { payments, type PaymentRequestRow } from "@/api/client";
import { AlertTriangle, Check, Clipboard, ShieldCheck, X } from "lucide-react";

/**
 * Admin page: review and approve payment requests.
 *
 * Lifecycle of a request:
 *   pending → approved (seed phrase shown to admin once)
 *   pending → rejected
 *
 * On approval, the server generates a 24-word seed phrase and creates
 * the User row. The phrase is shown to admin in a modal — admin's job
 * is to forward it to the buyer (e.g. Telegram DM).
 *
 * Mounted as a "Заявки" tab in the SettingsModal when the logged-in
 * user has role='admin'.
 */
export function PaymentRequestsAdmin() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<"pending" | "approved" | "rejected" | "all">(
    "pending"
  );
  const list = useQuery({
    queryKey: ["admin", "payments", filter],
    queryFn: () => payments.list(filter === "all" ? undefined : filter),
    refetchInterval: 30_000,
  });
  const [approvedSeed, setApprovedSeed] = useState<{
    request_id: number;
    contact: string;
    seed: string;
  } | null>(null);

  const approveM = useMutation({
    mutationFn: (id: number) => payments.approve(id),
    onSuccess: (data) => {
      setApprovedSeed({
        request_id: data.request_id,
        contact: data.contact_handle,
        seed: data.seed_phrase,
      });
      qc.invalidateQueries({ queryKey: ["admin", "payments"] });
    },
  });

  const rejectM = useMutation({
    mutationFn: ({ id, reason }: { id: number; reason: string }) =>
      payments.reject(id, reason),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "payments"] }),
  });

  return (
    <div className="space-y-4">
      <div>
        <h3 className="font-medium">Заявки на покупку</h3>
        <p className="text-xs text-neutral-500 mt-0.5">
          Подтвердите оплату — система сгенерирует сид-фразу. Перешлите её
          покупателю по контакту и удалите у себя.
        </p>
      </div>

      <div className="flex gap-1 text-sm flex-wrap">
        {(["pending", "approved", "rejected", "all"] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`min-h-10 px-3 py-1 rounded-md transition-[background-color,color,scale] duration-150 ease-out active:scale-[0.96] ${
              filter === f
                ? "bg-blue-600 text-white"
                : "bg-neutral-100 dark:bg-neutral-800 text-neutral-700 dark:text-neutral-300"
            }`}
          >
            {f === "pending"
              ? "Ожидают"
              : f === "approved"
                ? "Одобрены"
                : f === "rejected"
                  ? "Отклонены"
                  : "Все"}
          </button>
        ))}
      </div>

      <div className="space-y-2">
        {list.isLoading && <div className="text-sm text-neutral-500">Загрузка…</div>}
        {list.data?.length === 0 && (
          <div className="text-sm text-neutral-500 text-center py-6">
            Заявок нет.
          </div>
        )}
        {list.data?.map((r) => (
          <RequestRow
            key={r.id}
            req={r}
            onApprove={() => {
              if (confirm(`Одобрить заявку #${r.id} от ${r.contact_handle}?`)) {
                approveM.mutate(r.id);
              }
            }}
            onReject={() => {
              const reason = prompt(`Причина отклонения заявки #${r.id}?`);
              if (reason !== null) rejectM.mutate({ id: r.id, reason });
            }}
            busy={approveM.isPending || rejectM.isPending}
          />
        ))}
      </div>

      {approvedSeed && (
        <ApprovedSeedModal
          info={approvedSeed}
          onClose={() => setApprovedSeed(null)}
        />
      )}
    </div>
  );
}

function RequestRow({
  req,
  onApprove,
  onReject,
  busy,
}: {
  req: PaymentRequestRow;
  onApprove: () => void;
  onReject: () => void;
  busy: boolean;
}) {
  const created = req.created_at ? new Date(req.created_at) : null;
  return (
    <div
      className={`rounded-lg border p-3 ${
        req.status === "pending"
          ? "border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-950/20"
          : req.status === "approved"
            ? "border-emerald-300 dark:border-emerald-800 opacity-70"
            : "border-red-300 dark:border-red-800 opacity-50"
      }`}
    >
      <div className="flex flex-wrap items-start gap-2 justify-between">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <code className="font-mono text-xs">#{req.id}</code>
            <span className="font-medium text-sm break-all">
              {req.contact_handle}
            </span>
            <span
              className={`text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded ${
                req.status === "pending"
                  ? "bg-amber-200 dark:bg-amber-900 text-amber-900 dark:text-amber-200"
                  : req.status === "approved"
                    ? "bg-emerald-200 dark:bg-emerald-900 text-emerald-900 dark:text-emerald-200"
                    : "bg-red-200 dark:bg-red-900 text-red-900 dark:text-red-200"
              }`}
            >
              {req.status}
            </span>
          </div>
          <div className="text-xs text-neutral-500 mt-0.5">
            ${(req.amount_cents / 100).toFixed(2)} {req.currency}
            {created && ` · ${created.toLocaleString()}`}
            {req.ip_addr && ` · ${req.ip_addr}`}
          </div>
          {req.note && (
            <div className="text-xs mt-2 text-neutral-700 dark:text-neutral-300 whitespace-pre-wrap">
              {req.note}
            </div>
          )}
        </div>
        {req.status === "pending" && (
          <div className="flex gap-1.5 shrink-0">
            <button
              onClick={onApprove}
              disabled={busy}
              className="inline-flex items-center gap-1.5 px-3 py-1 text-xs bg-emerald-500 hover:bg-emerald-600 disabled:opacity-50 text-white rounded"
            >
              <Check size={13} />
              Одобрить
            </button>
            <button
              onClick={onReject}
              disabled={busy}
              className="inline-flex items-center gap-1.5 px-3 py-1 text-xs bg-red-500 hover:bg-red-600 disabled:opacity-50 text-white rounded"
            >
              <X size={13} />
              Отклонить
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function ApprovedSeedModal({
  info,
  onClose,
}: {
  info: { request_id: number; contact: string; seed: string };
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [confirmed, setConfirmed] = useState(false);

  function copy() {
    navigator.clipboard.writeText(info.seed).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="bg-white dark:bg-neutral-900 rounded-2xl p-6 max-w-lg w-full max-h-[90vh] overflow-y-auto space-y-4">
        <div>
          <h3 className="flex items-center gap-2 text-lg font-bold">
            <ShieldCheck size={20} className="text-emerald-600" />
            Заявка #{info.request_id} одобрена
          </h3>
          <p className="text-sm text-neutral-500 mt-1">
            Перешлите эту сид-фразу пользователю <strong>{info.contact}</strong>:
          </p>
        </div>

        <div className="rounded-lg border-2 border-amber-500 bg-amber-50 dark:bg-amber-950/30 p-4">
          <div className="text-xs font-semibold text-amber-900 dark:text-amber-200 mb-2">
            <span className="inline-flex items-center gap-1.5">
              <AlertTriangle size={14} />
              Эта фраза показывается ОДИН раз. Сервер её больше не сохранит.
            </span>
          </div>
          <div className="grid grid-cols-3 sm:grid-cols-4 gap-1.5 mt-3 font-mono text-xs">
            {info.seed.split(" ").map((w, i) => (
              <div
                key={i}
                className="flex items-center gap-1 px-2 py-1 bg-white dark:bg-neutral-800 rounded border border-amber-200 dark:border-amber-800"
              >
                <span className="text-[10px] text-neutral-400 w-4 text-right">
                  {i + 1}
                </span>
                <span className="select-all">{w}</span>
              </div>
            ))}
          </div>
        </div>

        <button
          onClick={copy}
          className="inline-flex w-full items-center justify-center gap-2 py-2 text-sm bg-blue-500 hover:bg-blue-600 text-white rounded-lg"
        >
          {copied ? <Check size={15} /> : <Clipboard size={15} />}
          {copied ? "Скопировано" : "Скопировать всю фразу"}
        </button>

        <label className="flex items-center gap-2 p-3 rounded-lg border border-neutral-300 dark:border-neutral-700 cursor-pointer">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
            className="w-4 h-4 accent-emerald-500"
          />
          <span className="text-sm">
            Я скопировал и переслал{" "}
            <span className="text-neutral-500">{info.contact}</span> — закрыть.
          </span>
        </label>

        <button
          onClick={onClose}
          disabled={!confirmed}
          className="w-full py-3 bg-emerald-500 hover:bg-emerald-600 disabled:bg-zinc-300 disabled:cursor-not-allowed text-white rounded-lg"
        >
          Закрыть и забыть
        </button>
      </div>
    </div>
  );
}
