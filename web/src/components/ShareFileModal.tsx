import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { shares, type FileShare } from "@/api/client";

interface Props {
  fileId: number;
  fileName: string;
  onClose: () => void;
}

const EXPIRY_PRESETS: Array<{ label: string; seconds: number | null }> = [
  { label: "1 час", seconds: 3600 },
  { label: "1 день", seconds: 86400 },
  { label: "7 дней", seconds: 604800 },
  { label: "30 дней", seconds: 2592000 },
  { label: "Без срока", seconds: null },
];

const DOWNLOAD_PRESETS: Array<{ label: string; max: number | null }> = [
  { label: "1 раз", max: 1 },
  { label: "10", max: 10 },
  { label: "100", max: 100 },
  { label: "Без лимита", max: null },
];

/**
 * Modal: create + manage public share links for a file.
 *
 * UX:
 *   - Two preset rows (срок жизни / лимит скачиваний)
 *   - Big primary button "Создать ссылку"
 *   - Below: list of existing shares for this file with copy/revoke
 */
export function ShareFileModal({ fileId, fileName, onClose }: Props) {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["shares", fileId],
    queryFn: () => shares.listForFile(fileId),
  });
  const [expiry, setExpiry] = useState<number | null>(86400);
  const [downloads, setDownloads] = useState<number | null>(null);
  const [copiedId, setCopiedId] = useState<number | null>(null);

  const createM = useMutation({
    mutationFn: () =>
      shares.create(fileId, {
        expires_in_seconds: expiry ?? undefined,
        max_downloads: downloads ?? undefined,
      }),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["shares", fileId] });
      // Auto-copy newly created link
      if (data.url) {
        navigator.clipboard.writeText(data.url).then(() => {
          setCopiedId(data.id);
          setTimeout(() => setCopiedId(null), 1500);
        });
      }
    },
  });

  const revokeM = useMutation({
    mutationFn: (id: number) => shares.revoke(id),
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: ["shares", fileId] });
      const prev = qc.getQueryData<FileShare[]>(["shares", fileId]);
      qc.setQueryData<FileShare[]>(["shares", fileId], (old) =>
        old
          ? old.map((s) =>
              s.id === id
                ? { ...s, revoked_at: new Date().toISOString(), active: false }
                : s,
            )
          : old,
      );
      return { prev };
    },
    onError: (_err, _id, ctx) => {
      if (ctx?.prev) qc.setQueryData(["shares", fileId], ctx.prev);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["shares", fileId] }),
  });

  function copy(s: FileShare) {
    if (!s.url) return;
    navigator.clipboard.writeText(s.url).then(() => {
      setCopiedId(s.id);
      setTimeout(() => setCopiedId(null), 1500);
    });
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      onClick={onClose}
    >
      <div
        className="bg-white dark:bg-neutral-900 rounded-2xl shadow-xl p-5 w-full max-w-lg max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex justify-between items-start mb-4">
          <div>
            <h2 className="text-lg font-bold">🔗 Поделиться</h2>
            <p className="text-xs text-neutral-500 mt-1 truncate max-w-xs">
              {fileName}
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-2xl text-neutral-400 hover:text-neutral-700 leading-none"
            aria-label="Закрыть"
          >
            ×
          </button>
        </div>

        {/* New share configuration */}
        <div className="space-y-3 p-3 rounded-lg bg-neutral-50 dark:bg-neutral-800/50 border border-neutral-200 dark:border-neutral-800">
          <div>
            <div className="text-xs font-medium text-neutral-600 dark:text-neutral-400 mb-1.5">
              Срок жизни
            </div>
            <div className="flex flex-wrap gap-1.5">
              {EXPIRY_PRESETS.map((p) => (
                <button
                  key={p.label}
                  onClick={() => setExpiry(p.seconds)}
                  className={`px-2.5 py-1 rounded-md text-xs ${
                    expiry === p.seconds
                      ? "bg-blue-600 text-white"
                      : "bg-white dark:bg-neutral-700 text-neutral-700 dark:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-600"
                  }`}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>
          <div>
            <div className="text-xs font-medium text-neutral-600 dark:text-neutral-400 mb-1.5">
              Лимит скачиваний
            </div>
            <div className="flex flex-wrap gap-1.5">
              {DOWNLOAD_PRESETS.map((p) => (
                <button
                  key={p.label}
                  onClick={() => setDownloads(p.max)}
                  className={`px-2.5 py-1 rounded-md text-xs ${
                    downloads === p.max
                      ? "bg-blue-600 text-white"
                      : "bg-white dark:bg-neutral-700 text-neutral-700 dark:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-600"
                  }`}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>
          <button
            onClick={() => createM.mutate()}
            disabled={createM.isPending}
            className="w-full py-2.5 bg-emerald-500 hover:bg-emerald-600 disabled:opacity-50 text-white rounded-lg font-medium text-sm"
          >
            {createM.isPending ? "Создаём…" : "🔗 Создать ссылку"}
          </button>
          {createM.isError && (
            <div className="text-xs text-red-600 dark:text-red-400">
              {(createM.error as Error).message}
            </div>
          )}
        </div>

        {/* Existing shares */}
        <div className="mt-4 space-y-2">
          {list.isLoading && <div className="text-sm text-neutral-500">Загрузка…</div>}
          {list.data?.length === 0 && (
            <div className="text-sm text-neutral-500 text-center py-4">
              У этого файла пока нет ссылок.
            </div>
          )}
          {list.data?.map((s) => (
            <ShareRow
              key={s.id}
              share={s}
              copied={copiedId === s.id}
              onCopy={() => copy(s)}
              onRevoke={() => {
                if (
                  confirm(
                    `Отозвать ссылку? Все, у кого она есть, перестанут скачивать.`,
                  )
                ) {
                  revokeM.mutate(s.id);
                }
              }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function ShareRow({
  share,
  copied,
  onCopy,
  onRevoke,
}: {
  share: FileShare;
  copied: boolean;
  onCopy: () => void;
  onRevoke: () => void;
}) {
  const expiresInfo = share.expires_at
    ? `до ${new Date(share.expires_at).toLocaleString()}`
    : "без срока";
  const dlInfo = share.max_downloads
    ? `${share.download_count}/${share.max_downloads} скачано`
    : `${share.download_count} скачано`;

  return (
    <div
      className={`p-3 rounded-lg border ${
        share.active
          ? "border-neutral-200 dark:border-neutral-700"
          : "border-neutral-200 dark:border-neutral-800 opacity-50"
      }`}
    >
      <div className="flex items-center gap-2 mb-1.5">
        <code className="text-[11px] font-mono text-neutral-500 break-all flex-1 min-w-0">
          {share.url ?? `…/share/${share.token}`}
        </code>
        {share.active && (
          <button
            onClick={onCopy}
            className="px-2 py-1 text-xs bg-blue-500 hover:bg-blue-600 text-white rounded shrink-0"
          >
            {copied ? "✓" : "📋"}
          </button>
        )}
      </div>
      <div className="flex items-center justify-between gap-2 text-xs text-neutral-500">
        <div className="flex flex-wrap gap-x-2">
          <span>{expiresInfo}</span>
          <span>·</span>
          <span>{dlInfo}</span>
          {!share.active && <span className="text-red-500">· отозвана</span>}
        </div>
        {share.active && (
          <button
            onClick={onRevoke}
            className="text-red-600 hover:text-red-700 text-xs"
          >
            Отозвать
          </button>
        )}
      </div>
    </div>
  );
}
