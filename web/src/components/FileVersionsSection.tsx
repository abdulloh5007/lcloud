import { useQuery } from "@tanstack/react-query";
import { versions, files as filesApi } from "@/api/client";
import { formatBytes, formatDate } from "@/lib/format";

interface Props {
  fileId: number;
}

/**
 * Inline section showing file version history (replaces-chain).
 *
 * Used inside FilePreviewModal — only renders when there are >1 entries
 * (so single-version files don't show empty UI).
 *
 * Each row: timestamp, size, "current"/"superseded" badge, optional
 * download link for old versions.
 */
export function FileVersionsSection({ fileId }: Props) {
  const q = useQuery({
    queryKey: ["versions", fileId],
    queryFn: () => versions.list(fileId),
  });

  if (q.isLoading) return null;
  if (!q.data || q.data.length <= 1) return null;

  return (
    <div className="border-t border-neutral-200 dark:border-neutral-800 px-3 sm:px-4 py-3 bg-panel dark:bg-panel-dark">
      <div className="text-xs text-neutral-500 mb-2">
        🕒 Версии ({q.data.length})
      </div>
      <div className="space-y-1.5">
        {q.data.map((v, i) => {
          const isLive = v.deleted_at === null;
          const isCurrent = i === 0;
          return (
            <div
              key={v.id}
              className={`flex items-center gap-3 px-2.5 py-1.5 rounded text-xs ${
                isLive
                  ? "bg-emerald-50 dark:bg-emerald-950/30"
                  : "bg-neutral-50 dark:bg-neutral-800/50 opacity-70"
              }`}
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-mono text-[10px] text-neutral-400">
                    #{v.id}
                  </span>
                  <span>
                    {v.uploaded_at ? formatDate(v.uploaded_at) : "—"}
                  </span>
                  {isCurrent && (
                    <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-emerald-200 text-emerald-900 dark:bg-emerald-900 dark:text-emerald-100">
                      сейчас
                    </span>
                  )}
                  {!isLive && (
                    <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-neutral-200 text-neutral-700 dark:bg-neutral-700 dark:text-neutral-300">
                      старая
                    </span>
                  )}
                </div>
                <div className="text-[11px] text-neutral-500 mt-0.5">
                  {formatBytes(v.size_bytes)}
                  {v.compressed && " · 📦 сжата"}
                </div>
              </div>
              {!isLive && (
                <a
                  href={filesApi.downloadUrl(v.id)}
                  className="text-blue-600 hover:text-blue-700 text-[11px] shrink-0"
                  title="Скачать эту версию"
                >
                  ⬇
                </a>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
