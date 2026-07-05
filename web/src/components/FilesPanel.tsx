import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  files as filesApi,
  search as searchApi,
} from "@/api/client";
import type { FileRow, FilesPage } from "@/api/types";
import {
  Download,
  Trash2,
  Upload,
  Plus,
  X,
  AlertCircle,
  FileIcon,
  Image,
  Film,
  Music,
  FileText,
  Search,
  Send,
  ShieldCheck,
} from "lucide-react";
import { classNames, formatBytes, formatDate } from "@/lib/format";
import { Button } from "./ui/Button";
import { FilePreviewModal } from "./FilePreviewModal";

type ViewMode = "grid" | "list";
const PAGE_SIZE = 50;

interface Props {
  cloudId: number | null;
  /** Whether new uploads should be compressed server-side. */
  compressUploads: boolean;
}

function mimeIcon(mime: string) {
  if (mime.startsWith("image/")) return Image;
  if (mime.startsWith("video/")) return Film;
  if (mime.startsWith("audio/")) return Music;
  if (mime === "application/pdf" || mime.startsWith("text/")) return FileText;
  return FileIcon;
}

interface ProgressItem {
  name: string;
  loaded: number;
  total: number;
  phase: "signing" | "uploading";
  error?: string;
}

type UploadPreviewKind = "image" | "video" | "audio" | "file";

interface UploadQueueItem {
  id: string;
  file: File;
  previewUrl: string | null;
  kind: UploadPreviewKind;
}

interface Page {
  items: FileRow[];
  total: number;
  limit: number;
  offset: number;
}

function previewKind(file: File): UploadPreviewKind {
  if (file.type.startsWith("image/")) return "image";
  if (file.type.startsWith("video/")) return "video";
  if (file.type.startsWith("audio/")) return "audio";
  return "file";
}

function makeQueueItem(file: File): UploadQueueItem {
  const kind = previewKind(file);
  return {
    id: `${file.name}-${file.size}-${file.lastModified}-${crypto.randomUUID()}`,
    file,
    previewUrl: kind === "file" ? null : URL.createObjectURL(file),
    kind,
  };
}

function revokeQueueItem(item: UploadQueueItem) {
  if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
}

export function FilesPanel({ cloudId, compressUploads }: Props) {
  const qc = useQueryClient();
  const [view, setView] = useState<ViewMode>("grid");
  const [query, setQuery] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [progress, setProgress] = useState<Record<string, ProgressItem>>({});
  const [uploadQueue, setUploadQueue] = useState<UploadQueueItem[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [previewFile, setPreviewFile] = useState<FileRow | null>(null);
  const uploadQueueRef = useRef<UploadQueueItem[]>([]);
  const queuedBytes = useMemo(
    () => uploadQueue.reduce((sum, item) => sum + item.file.size, 0),
    [uploadQueue],
  );
  const hasActiveUploads = useMemo(
    () => Object.values(progress).some((item) => !item.error),
    [progress],
  );

  useEffect(() => {
    uploadQueueRef.current = uploadQueue;
  }, [uploadQueue]);

  useEffect(
    () => () => {
      uploadQueueRef.current.forEach(revokeQueueItem);
    },
    [],
  );

  // Close preview on Escape
  useEffect(() => {
    if (!previewFile) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setPreviewFile(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [previewFile]);

  // Debounce search input
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(query), 200);
    return () => clearTimeout(t);
  }, [query]);

  // List source: search vs cloud-files (both paginated)
  const useSearch =
    cloudId === null || debouncedQ.trim() !== "";
  const listKey = useMemo(
    () =>
      useSearch
        ? ["search", { q: debouncedQ, cloudId }]
        : ["files", cloudId],
    [useSearch, debouncedQ, cloudId],
  );

  const list = useInfiniteQuery<Page>({
    queryKey: listKey,
    initialPageParam: { offset: 0, limit: PAGE_SIZE },
    queryFn: async ({ pageParam }) => {
      const { offset, limit } = pageParam as { offset: number; limit: number };
      if (useSearch) {
        const r = await searchApi.query({
          q: debouncedQ.trim() || undefined,
          cloud_id: cloudId ?? undefined,
          limit,
          offset,
        });
        return { items: r.items, total: r.total, limit: r.limit, offset: r.offset };
      }
      if (cloudId === null) {
        return { items: [], total: 0, limit, offset };
      }
      const r: FilesPage = await filesApi.list(cloudId, { limit, offset });
      return r;
    },
    getNextPageParam: (last) =>
      last.offset + last.items.length < last.total
        ? { offset: last.offset + last.items.length, limit: PAGE_SIZE }
        : undefined,
  });

  const items = useMemo(
    () => (list.data?.pages ?? []).flatMap((p) => p.items),
    [list.data],
  );
  const totalCount = list.data?.pages?.[0]?.total ?? 0;

  // IntersectionObserver auto-fetch when sentinel visible
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (
            e.isIntersecting &&
            list.hasNextPage &&
            !list.isFetchingNextPage
          ) {
            void list.fetchNextPage();
          }
        }
      },
      { rootMargin: "300px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [list.hasNextPage, list.isFetchingNextPage, list.fetchNextPage, items.length]);

  /**
   * Optimistic delete: drop the file from the cached list immediately,
   * then re-validate against the server. If the server rejects, restore.
   * This makes the UI feel instant — no spinner, no flash.
   */
  const remove = useMutation({
    mutationFn: (id: number) => filesApi.remove(id),
    onMutate: async (id: number) => {
      await qc.cancelQueries({ queryKey: ["files", cloudId] });
      const snapshot = qc.getQueriesData({ queryKey: ["files", cloudId] });
      // For each cached page-set, drop the file optimistically
      qc.setQueriesData<{ pages?: Page[] } | undefined>(
        { queryKey: ["files", cloudId] },
        (old) => {
          if (!old?.pages) return old;
          return {
            ...old,
            pages: old.pages.map((p) => ({
              ...p,
              items: p.items.filter((f) => f.id !== id),
              total: Math.max(0, p.total - 1),
            })),
          };
        },
      );
      return { snapshot };
    },
    onError: (_err, _id, ctx) => {
      // Rollback on error
      if (ctx?.snapshot) {
        for (const [key, data] of ctx.snapshot) {
          qc.setQueryData(key, data);
        }
      }
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["files", cloudId] });
      qc.invalidateQueries({ queryKey: ["search"] });
      qc.invalidateQueries({ queryKey: ["v2", "quota"] });
    },
  });

  const fileInputRef = useRef<HTMLInputElement | null>(null);

  function addPendingFiles(filesIn: FileList | File[]) {
    if (cloudId === null) {
      window.alert("Выберите cloud в сайдбаре, чтобы загрузить файлы.");
      return;
    }
    const arr = Array.from(filesIn);
    if (arr.length === 0) return;
    setUploadQueue((current) => [
      ...current,
      ...arr.map((file) => makeQueueItem(file)),
    ]);
  }

  function removeQueuedFile(id: string) {
    setUploadQueue((current) => {
      const item = current.find((queued) => queued.id === id);
      if (item) revokeQueueItem(item);
      return current.filter((queued) => queued.id !== id);
    });
  }

  function clearQueue() {
    setUploadQueue((current) => {
      current.forEach(revokeQueueItem);
      return [];
    });
  }

  async function uploadQueuedFiles() {
    if (cloudId === null) {
      window.alert("Выберите cloud в сайдбаре, чтобы загрузить файлы.");
      return;
    }
    const batch = uploadQueueRef.current;
    if (batch.length === 0) return;

    setUploadQueue([]);
    for (const item of batch) {
      const f = item.file;
      const key = item.id;
      revokeQueueItem(item);
      setProgress((p) => ({
        ...p,
        [key]: { name: f.name, loaded: 0, total: f.size, phase: "signing" },
      }));
      try {
        await filesApi.upload(
          cloudId,
          f,
          (loaded, total, phase) => {
            setProgress((p) => ({
              ...p,
              [key]: { name: f.name, loaded, total, phase },
            }));
          },
          { compress: compressUploads }
        );
        setProgress((p) => {
          const { [key]: _omit, ...rest } = p;
          return rest;
        });
      } catch (e) {
        const msg = e instanceof ApiError ? e.reason : "upload failed";
        setProgress((p) => ({
          ...p,
          [key]: {
            name: f.name,
            loaded: f.size,
            total: f.size,
            phase: "uploading",
            error: msg,
          },
        }));
      }
    }
    qc.invalidateQueries({ queryKey: ["files"] });
    qc.invalidateQueries({ queryKey: ["search"] });
    qc.invalidateQueries({ queryKey: ["v2", "quota"] });
  }

  return (
    <main className="flex-1 flex flex-col bg-bg dark:bg-bg-dark min-w-0">
      <div className="border-b border-neutral-200 dark:border-neutral-800 px-3 sm:px-4 py-3 flex flex-wrap items-center gap-2 sm:gap-3">
        <input
          type="text"
          placeholder="Поиск…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="flex-1 min-w-[10rem] max-w-md rounded-lg border border-neutral-200 dark:border-neutral-700 bg-panel dark:bg-panel-dark px-3 py-1.5 text-sm"
        />
        <div className="flex items-center rounded-lg border border-neutral-200 dark:border-neutral-700 bg-panel dark:bg-panel-dark text-xs">
          <button
            type="button"
            onClick={() => setView("grid")}
            className={classNames(
              "px-3 py-1.5 rounded-l-lg",
              view === "grid"
                ? "bg-neutral-100 dark:bg-neutral-800"
                : "hover:bg-neutral-50 dark:hover:bg-neutral-900",
            )}
          >
            Grid
          </button>
          <button
            type="button"
            onClick={() => setView("list")}
            className={classNames(
              "px-3 py-1.5 rounded-r-lg",
              view === "list"
                ? "bg-neutral-100 dark:bg-neutral-800"
                : "hover:bg-neutral-50 dark:hover:bg-neutral-900",
            )}
          >
            List
          </button>
        </div>
        <Button
          variant="ghost"
          onClick={() => fileInputRef.current?.click()}
          disabled={cloudId === null}
        >
          <Plus size={16} />
          <span className="hidden sm:inline">Добавить</span>
        </Button>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files) addPendingFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {uploadQueue.length > 0 && (
        <section className="border-b border-neutral-200 dark:border-neutral-800 bg-panel/70 dark:bg-panel-dark/70 px-3 sm:px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
            <div className="min-w-0">
              <div className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">
                К загрузке
              </div>
              <div className="text-xs text-neutral-500 tabular-nums">
                {uploadQueue.length} файл(ов), {formatBytes(queuedBytes)}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={clearQueue}
              >
                <X size={14} />
                <span className="hidden sm:inline">Очистить</span>
              </Button>
              <Button
                type="button"
                size="sm"
                onClick={() => void uploadQueuedFiles()}
                disabled={
                  cloudId === null || uploadQueue.length === 0 || hasActiveUploads
                }
              >
                <Upload size={14} />
                <span>Загрузить {uploadQueue.length}</span>
              </Button>
            </div>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-[repeat(auto-fill,minmax(150px,1fr))] lg:grid-cols-[repeat(auto-fill,minmax(170px,1fr))] gap-2">
            {uploadQueue.map((item) => (
              <QueuedUploadCard
                key={item.id}
                item={item}
                onRemove={() => removeQueuedFile(item.id)}
              />
            ))}
          </div>
        </section>
      )}

      {Object.keys(progress).length > 0 && (
        <div className="border-b border-neutral-200 dark:border-neutral-800 px-4 py-2 space-y-1.5">
          {Object.entries(progress).map(([k, p]) => {
            const pct = p.total ? Math.min(100, (p.loaded / p.total) * 100) : 0;
            return (
              <div key={k} className="text-xs">
                <div className="flex justify-between">
                  <span className="truncate max-w-[60%]">
                    {p.phase === "signing" && !p.error && (
                      <ShieldCheck
                        size={13}
                        className="mr-1.5 inline text-emerald-600 dark:text-emerald-400"
                      />
                    )}
                    {p.phase === "uploading" && !p.error && (
                      <Send
                        size={13}
                        className="mr-1.5 inline text-blue-600 dark:text-blue-400"
                      />
                    )}
                    {p.name}
                  </span>
                  <span
                    className={classNames(
                      "tabular-nums",
                      p.error ? "text-red-600" : "text-neutral-500",
                    )}
                  >
                    {p.error
                      ? (
                        <span className="inline-flex items-center gap-1">
                          <AlertCircle size={13} />
                          {p.error}
                        </span>
                      )
                      : p.phase === "signing"
                        ? "Подписываем…"
                        : `${formatBytes(p.loaded)} / ${formatBytes(p.total)}`}
                  </span>
                </div>
                <div className="h-1 bg-neutral-200 dark:bg-neutral-800 rounded overflow-hidden">
                  <div
                    className={classNames(
                      "h-full transition-[width,background-color] duration-200 ease-out",
                      p.error
                        ? "bg-red-500"
                        : p.phase === "signing"
                          ? "bg-emerald-500"
                          : "bg-blue-500",
                    )}
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div
        className={classNames(
          "flex-1 overflow-y-auto thin-scroll p-3 sm:p-4",
          dragOver &&
            "ring-2 ring-blue-500 ring-inset bg-blue-50/50 dark:bg-blue-950/20",
        )}
        onDragOver={(e) => {
          if (cloudId !== null) {
            e.preventDefault();
            setDragOver(true);
          }
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          if (cloudId !== null && e.dataTransfer.files) {
            addPendingFiles(e.dataTransfer.files);
          }
        }}
      >
        {dragOver && (
        <div className="pointer-events-none sticky top-0 z-10 mb-3 rounded-lg border border-blue-200 dark:border-blue-800 bg-blue-50/95 dark:bg-blue-950/90 px-3 py-2 text-sm text-blue-700 dark:text-blue-200 shadow-sm">
            Отпустите файлы, чтобы добавить их в очередь.
          </div>
        )}
        {list.isLoading && (
          <div className="text-sm text-neutral-500">…</div>
        )}
        {list.isError && (
          <div className="text-sm text-red-600">
            ошибка: {String(list.error)}
          </div>
        )}
        {items.length === 0 && !list.isLoading && (
          <div className="flex flex-col items-center justify-center h-full text-sm text-neutral-400">
            {cloudId === null ? (
              <Search size={32} className="mb-3 opacity-40" />
            ) : (
              <Upload size={32} className="mb-3 opacity-40" />
            )}
            <p className="mb-1 text-center px-4">
              {cloudId === null
                ? "Введите запрос или выберите cloud."
                : "Нет файлов. Перетащите сюда несколько файлов или нажмите «Добавить»."}
            </p>
          </div>
        )}

        {view === "grid" ? (
          <div className="grid grid-cols-2 sm:grid-cols-[repeat(auto-fill,minmax(180px,1fr))] md:grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-2 sm:gap-3">
            {items.map((f) => (
              <FileGridCard
                key={f.id}
                file={f}
                onOpen={() => setPreviewFile(f)}
                onDelete={() => remove.mutate(f.id)}
              />
            ))}
          </div>
        ) : (
          <div className="rounded-lg border border-neutral-200 dark:border-neutral-800 overflow-hidden overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-neutral-50 dark:bg-neutral-900 text-left">
                <tr>
                  <th className="px-3 py-2 font-medium">Имя</th>
                  <th className="px-3 py-2 font-medium w-24 sm:w-32">
                    Размер
                  </th>
                  <th className="px-3 py-2 font-medium w-44 hidden md:table-cell">
                    Загружен
                  </th>
                  <th className="px-3 py-2 font-medium w-24 sm:w-28"></th>
                </tr>
              </thead>
              <tbody>
                {items.map((f) => {
                  const I = mimeIcon(f.mime);
                  return (
                    <tr
                      key={f.id}
                      className="border-t border-neutral-100 dark:border-neutral-800 hover:bg-neutral-50 dark:hover:bg-neutral-900 cursor-pointer"
                      onClick={() => setPreviewFile(f)}
                    >
                      <td className="px-3 py-2 flex items-center gap-2 max-w-0">
                        <I size={16} className="text-neutral-400 shrink-0" />
                        <span className="truncate">{f.name}</span>
                      </td>
                      <td className="px-3 py-2 tabular-nums text-neutral-500">
                        {formatBytes(f.size)}
                      </td>
                      <td className="px-3 py-2 text-neutral-500 hidden md:table-cell">
                        {formatDate(f.uploaded_at)}
                      </td>
                      <td
                        className="px-3 py-2 text-right whitespace-nowrap"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <a
                          href={filesApi.downloadUrl(f.id)}
                          className="inline-block p-1.5 mr-1 text-neutral-500 hover:text-blue-600"
                          aria-label="Download"
                          title="Download"
                        >
                          <Download size={14} />
                        </a>
                        <button
                          type="button"
                          className="p-1.5 text-neutral-500 hover:text-red-600"
                          onClick={() => {
                            if (window.confirm(`Удалить «${f.name}»?`)) {
                              remove.mutate(f.id);
                            }
                          }}
                          aria-label="Delete"
                          title="Delete"
                        >
                          <Trash2 size={14} />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Sentinel for IntersectionObserver-driven infinite scroll */}
        <div
          ref={sentinelRef}
          className="h-12 flex items-center justify-center text-xs text-neutral-400"
        >
          {list.isFetchingNextPage
            ? "Загрузка…"
            : list.hasNextPage
              ? "Прокрутите для загрузки ещё"
              : items.length > 0
                ? `Всего: ${totalCount}`
                : ""}
        </div>
      </div>

      {previewFile && (
        <FilePreviewModal
          file={previewFile}
          onClose={() => setPreviewFile(null)}
          onRenamed={(updated) => {
            setPreviewFile(updated);
            qc.invalidateQueries({ queryKey: ["files"] });
            qc.invalidateQueries({ queryKey: ["search"] });
          }}
        />
      )}
    </main>
  );
}

function QueuedUploadCard({
  item,
  onRemove,
}: {
  item: UploadQueueItem;
  onRemove: () => void;
}) {
  const Icon = mimeIcon(item.file.type);

  return (
    <div className="relative min-w-0 rounded-lg border border-neutral-200 dark:border-neutral-800 bg-bg dark:bg-bg-dark p-2">
      <button
        type="button"
        className="absolute right-1.5 top-1.5 z-10 inline-flex h-7 w-7 items-center justify-center rounded-md bg-white/90 text-neutral-600 shadow-sm ring-1 ring-black/5 hover:bg-red-50 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-neutral-900/90 dark:text-neutral-300 dark:ring-white/10 dark:hover:bg-red-950/50"
        onClick={onRemove}
        aria-label={`Убрать ${item.file.name} из очереди`}
        title="Убрать из очереди"
      >
        <X size={14} />
      </button>
      <div className="aspect-[4/3] overflow-hidden rounded-md bg-neutral-100 dark:bg-neutral-900">
        {item.kind === "image" && item.previewUrl ? (
          <img
            src={item.previewUrl}
            alt={item.file.name}
            className="h-full w-full object-cover media-outline"
          />
        ) : item.kind === "video" && item.previewUrl ? (
          <video
            src={item.previewUrl}
            className="h-full w-full object-cover media-outline"
            muted
            preload="metadata"
          />
        ) : item.kind === "audio" && item.previewUrl ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 px-2">
            <Music size={30} className="text-neutral-400" />
            <audio
              src={item.previewUrl}
              controls
              className="h-8 w-full max-w-full"
            />
          </div>
        ) : (
          <div className="flex h-full items-center justify-center">
            <Icon size={42} className="text-neutral-400" />
          </div>
        )}
      </div>
      <div className="mt-2 min-w-0">
        <div
          className="truncate text-xs font-medium text-neutral-900 dark:text-neutral-100"
          title={item.file.name}
        >
          {item.file.name}
        </div>
        <div className="mt-0.5 text-xs text-neutral-500 tabular-nums">
          {formatBytes(item.file.size)}
        </div>
      </div>
    </div>
  );
}

function FileGridCard({
  file,
  onOpen,
  onDelete,
}: {
  file: FileRow;
  onOpen: () => void;
  onDelete: () => void;
}) {
  const Icon = mimeIcon(file.mime);
  const isImage = file.mime.startsWith("image/");
  return (
    <div
      className="rounded-xl bg-panel dark:bg-panel-dark p-3 flex flex-col gap-2 surface-shadow surface-shadow-hover active:scale-[0.99] cursor-pointer"
      onClick={onOpen}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
    >
      {isImage ? (
        <div className="relative aspect-[4/3] rounded-lg overflow-hidden bg-neutral-100 dark:bg-neutral-900">
          <img
            // For grid: cap at "med" — even if user prefers "high" the
            // grid thumbnails would otherwise blow bandwidth on long lists.
            src={filesApi.thumbUrl(file.id, "med")}
            alt={file.name}
            loading="lazy"
            decoding="async"
            className="w-full h-full object-cover media-outline"
          />
        </div>
      ) : (
        <div className="aspect-[4/3] rounded-lg bg-neutral-100 dark:bg-neutral-900 flex items-center justify-center">
          <Icon size={48} className="text-neutral-400" />
        </div>
      )}
      <div className="min-w-0">
        <div className="flex items-center gap-1.5 min-w-0">
          {file.caption_kind === "LC2" && (
            <span
              title="Подписан клиентом (LC2)"
              className="inline-flex items-center gap-1 text-xs px-1 py-0.5 rounded bg-emerald-100 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-400 font-mono shrink-0"
            >
              <ShieldCheck size={11} />
              LC2
            </span>
          )}
          {file.caption_kind === "LC1" && (
            <span
              title="Подписан сервером (legacy LC1)"
              className="text-xs px-1 py-0.5 rounded bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400 font-mono shrink-0"
            >
              LC1
            </span>
          )}
          <div
            className="text-sm font-medium truncate min-w-0"
            title={file.name}
          >
            {file.name}
          </div>
        </div>
        <div className="text-xs text-neutral-500 tabular-nums">
          {formatBytes(file.size)}
        </div>
      </div>
      <div
        className="mt-auto flex items-center justify-between text-xs"
        onClick={(e) => e.stopPropagation()}
      >
        <span className="text-neutral-400">{formatDate(file.uploaded_at)}</span>
        <div className="flex items-center gap-1">
          <a
            href={filesApi.downloadUrl(file.id)}
            className="inline-flex h-10 w-10 items-center justify-center rounded-lg text-neutral-500 hover:bg-neutral-100 hover:text-blue-600 dark:hover:bg-neutral-800 transition-[background-color,color] duration-150 ease-out active:scale-[0.96]"
            aria-label="Download"
            title="Download"
          >
            <Download size={14} />
          </a>
          <button
            type="button"
            className="inline-flex h-10 w-10 items-center justify-center rounded-lg text-neutral-500 hover:bg-neutral-100 hover:text-red-600 dark:hover:bg-neutral-800 transition-[background-color,color,scale] duration-150 ease-out active:scale-[0.96]"
            onClick={() => {
              if (window.confirm(`Удалить «${file.name}»?`)) onDelete();
            }}
            aria-label="Delete"
            title="Delete"
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}
