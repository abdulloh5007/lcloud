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
import type { FileRow, FilesPage, ThumbSize } from "@/api/types";
import {
  Download,
  Trash2,
  Upload,
  FileIcon,
  Image,
  Film,
  Music,
  FileText,
} from "lucide-react";
import { classNames, formatBytes, formatDate } from "@/lib/format";
import { Button } from "./ui/Button";
import { FilePreviewModal } from "./FilePreviewModal";

type ViewMode = "grid" | "list";
const PAGE_SIZE = 50;

interface Props {
  cloudId: number | null;
  quality: ThumbSize;
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
  error?: string;
}

interface Page {
  items: FileRow[];
  total: number;
  limit: number;
  offset: number;
}

export function FilesPanel({ cloudId, quality }: Props) {
  const qc = useQueryClient();
  const [view, setView] = useState<ViewMode>("grid");
  const [query, setQuery] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [progress, setProgress] = useState<Record<string, ProgressItem>>({});
  const [dragOver, setDragOver] = useState(false);
  const [previewFile, setPreviewFile] = useState<FileRow | null>(null);

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

  const remove = useMutation({
    mutationFn: (id: number) => filesApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["files"] });
      qc.invalidateQueries({ queryKey: ["search"] });
    },
  });

  const fileInputRef = useRef<HTMLInputElement | null>(null);

  async function handleFiles(filesIn: FileList | File[]) {
    if (cloudId === null) {
      window.alert("Выберите cloud в сайдбаре, чтобы загрузить файлы.");
      return;
    }
    const arr = Array.from(filesIn);
    for (const f of arr) {
      const key = `${f.name}-${Date.now()}-${Math.random()}`;
      setProgress((p) => ({
        ...p,
        [key]: { name: f.name, loaded: 0, total: f.size },
      }));
      try {
        await filesApi.upload(cloudId, f, (loaded, total) => {
          setProgress((p) => ({
            ...p,
            [key]: { name: f.name, loaded, total },
          }));
        });
        setProgress((p) => {
          const { [key]: _omit, ...rest } = p;
          return rest;
        });
      } catch (e) {
        const msg = e instanceof ApiError ? e.reason : "upload failed";
        setProgress((p) => ({
          ...p,
          [key]: { name: f.name, loaded: f.size, total: f.size, error: msg },
        }));
      }
    }
    qc.invalidateQueries({ queryKey: ["files"] });
    qc.invalidateQueries({ queryKey: ["search"] });
  }

  return (
    <main className="flex-1 flex flex-col bg-bg dark:bg-bg-dark min-w-0">
      <div className="border-b border-neutral-200 dark:border-neutral-800 px-3 sm:px-4 py-3 flex flex-wrap items-center gap-2 sm:gap-3">
        <input
          type="text"
          placeholder="🔍 Поиск…"
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
          <Upload size={16} />
          <span className="hidden sm:inline">Загрузить</span>
        </Button>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files) void handleFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {Object.keys(progress).length > 0 && (
        <div className="border-b border-neutral-200 dark:border-neutral-800 px-4 py-2 space-y-1.5">
          {Object.entries(progress).map(([k, p]) => {
            const pct = p.total ? Math.min(100, (p.loaded / p.total) * 100) : 0;
            return (
              <div key={k} className="text-xs">
                <div className="flex justify-between">
                  <span className="truncate max-w-[60%]">{p.name}</span>
                  <span
                    className={classNames(
                      "tabular-nums",
                      p.error ? "text-red-600" : "text-neutral-500",
                    )}
                  >
                    {p.error
                      ? `❌ ${p.error}`
                      : `${formatBytes(p.loaded)} / ${formatBytes(p.total)}`}
                  </span>
                </div>
                <div className="h-1 bg-neutral-200 dark:bg-neutral-800 rounded overflow-hidden">
                  <div
                    className={classNames(
                      "h-full",
                      p.error ? "bg-red-500" : "bg-blue-500",
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
            void handleFiles(e.dataTransfer.files);
          }
        }}
      >
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
            <Upload size={32} className="mb-3 opacity-40" />
            <p className="mb-1 text-center px-4">
              {cloudId === null
                ? "Введите запрос или выберите cloud."
                : "Нет файлов. Перетащите сюда или нажмите «Загрузить»."}
            </p>
          </div>
        )}

        {view === "grid" ? (
          <div className="grid grid-cols-2 sm:grid-cols-[repeat(auto-fill,minmax(180px,1fr))] md:grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-2 sm:gap-3">
            {items.map((f) => (
              <FileGridCard
                key={f.id}
                file={f}
                quality={quality}
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
          quality={quality}
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

function FileGridCard({
  file,
  quality,
  onOpen,
  onDelete,
}: {
  file: FileRow;
  quality: ThumbSize;
  onOpen: () => void;
  onDelete: () => void;
}) {
  const Icon = mimeIcon(file.mime);
  const isImage = file.mime.startsWith("image/");
  return (
    <div
      className="rounded-xl bg-panel dark:bg-panel-dark border border-neutral-200 dark:border-neutral-800 p-3 flex flex-col gap-2 hover:border-neutral-300 dark:hover:border-neutral-700 transition cursor-pointer"
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
            src={filesApi.thumbUrl(file.id, quality === "high" ? "med" : quality)}
            alt={file.name}
            loading="lazy"
            decoding="async"
            className="w-full h-full object-cover"
          />
        </div>
      ) : (
        <div className="aspect-[4/3] rounded-lg bg-neutral-100 dark:bg-neutral-900 flex items-center justify-center">
          <Icon size={48} className="text-neutral-400" />
        </div>
      )}
      <div className="min-w-0">
        <div className="text-sm font-medium truncate" title={file.name}>
          {file.name}
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
            className="p-1.5 rounded hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-500 hover:text-blue-600"
            aria-label="Download"
            title="Download"
          >
            <Download size={14} />
          </a>
          <button
            type="button"
            className="p-1.5 rounded hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-500 hover:text-red-600"
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
