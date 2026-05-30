import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ApiError, files as filesApi } from "@/api/client";
import type { FileRow } from "@/api/types";
import { Download, X, Pencil, Check, Share2 } from "lucide-react";
import { Button } from "./ui/Button";
import { TextField } from "./ui/TextField";
import { Modal } from "./ui/Modal";
import { useIsMobile } from "@/hooks/useAuth";
import { FileTagsBar } from "./Tags";
import { FileVersionsSection } from "./FileVersionsSection";
import { ShareFileModal } from "./ShareFileModal";
import { formatBytes, formatDate } from "@/lib/format";

interface Props {
  file: FileRow;
  onClose: () => void;
  onRenamed: (updated: FileRow) => void;
}

type Kind = "image" | "video" | "audio" | "pdf" | "text" | "other";

function classify(mime: string): Kind {
  if (mime.startsWith("image/")) return "image";
  if (mime.startsWith("video/")) return "video";
  if (mime.startsWith("audio/")) return "audio";
  if (mime === "application/pdf") return "pdf";
  if (
    mime.startsWith("text/") ||
    mime.endsWith("+json") ||
    mime === "application/json"
  )
    return "text";
  return "other";
}

export function FilePreviewModal({ file, onClose, onRenamed }: Props) {
  const isMobile = useIsMobile();
  const [shareOpen, setShareOpen] = useState(false);

  // Rename UI state
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(file.name);
  const [renameError, setRenameError] = useState<string | null>(null);

  useEffect(() => {
    setDraft(file.name);
    setEditing(false);
    setRenameError(null);
  }, [file.id, file.name]);

  const rename = useMutation({
    mutationFn: (name: string) => filesApi.rename(file.id, name),
    onSuccess: (updated) => {
      onRenamed(updated);
      setEditing(false);
      setRenameError(null);
    },
    onError: (e: unknown) => {
      setRenameError(
        e instanceof ApiError ? `${e.reason} (${e.status})` : "rename failed",
      );
    },
  });

  const kind = classify(file.mime);
  // Image / video / pdf / text use the actual file or thumb (image only).
  const imageSrc =
    kind === "image"
      ? filesApi.thumbUrl(file.id, "med")
      : filesApi.downloadUrl(file.id);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-2 sm:p-4"
      onClick={onClose}
    >
      <div
        className="relative w-full max-w-5xl bg-panel dark:bg-panel-dark rounded-2xl shadow-xl flex flex-col max-h-[95vh] overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="flex items-center gap-2 px-3 sm:px-4 py-3 border-b border-neutral-200 dark:border-neutral-800">
          <div className="min-w-0 flex-1">
            {editing && !isMobile ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  const v = draft.trim();
                  if (!v) return;
                  rename.mutate(v);
                }}
                className="flex items-center gap-2"
              >
                <TextField
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  autoFocus
                  className="text-sm"
                  error={renameError ?? undefined}
                />
                <Button
                  type="submit"
                  size="sm"
                  loading={rename.isPending}
                  disabled={!draft.trim() || draft === file.name}
                  aria-label="Save name"
                >
                  <Check size={14} />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setEditing(false);
                    setDraft(file.name);
                    setRenameError(null);
                  }}
                >
                  <X size={14} />
                </Button>
              </form>
            ) : (
              <div className="flex items-center gap-2">
                <span
                  className="font-medium truncate"
                  title={file.name}
                >
                  {file.name}
                </span>
                <button
                  type="button"
                  onClick={() => setEditing(true)}
                  className="p-1 text-neutral-400 hover:text-blue-600"
                  aria-label="Rename"
                  title="Rename"
                >
                  <Pencil size={14} />
                </button>
              </div>
            )}
            <div className="text-xs text-neutral-500 tabular-nums truncate">
              {formatBytes(file.size)} · {file.mime} ·{" "}
              {formatDate(file.uploaded_at)}
            </div>
          </div>

          <button
            onClick={() => setShareOpen(true)}
            className="ml-1 sm:ml-2 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-500 text-white text-sm hover:bg-emerald-600"
            aria-label="Поделиться"
          >
            <Share2 size={14} />
            <span className="hidden sm:inline">Поделиться</span>
          </button>
          <a
            href={filesApi.downloadUrl(file.id)}
            className="ml-1 sm:ml-2 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-600 text-white text-sm hover:bg-blue-700"
            aria-label="Download"
          >
            <Download size={14} />
            <span className="hidden sm:inline">Download</span>
          </a>
          <Button
            variant="ghost"
            size="sm"
            onClick={onClose}
            aria-label="Close"
          >
            <X size={16} />
          </Button>
        </div>

        <div className="flex-1 min-h-0 overflow-auto p-2 sm:p-4 flex items-center justify-center bg-neutral-100 dark:bg-neutral-950">
          <PreviewBody
            kind={kind}
            url={imageSrc}
            fullUrl={filesApi.downloadUrl(file.id)}
            mime={file.mime}
            name={file.name}
            isHighQuality={true}
          />
        </div>

        {/* Per-file tags — current tags + "+" to add; click on chip = edit/delete */}
        <div className="border-t border-neutral-200 dark:border-neutral-800 px-3 sm:px-4 py-3 bg-panel dark:bg-panel-dark">
          <div className="text-xs text-neutral-500 mb-2">Теги:</div>
          <FileTagsBar fileId={file.id} />
        </div>

        <FileVersionsSection fileId={file.id} />

        {shareOpen && (
          <ShareFileModal
            fileId={file.id}
            fileName={file.name}
            onClose={() => setShareOpen(false)}
          />
        )}
      </div>

      {/* On small screens, rename happens in a dedicated modal. */}
      <Modal
        open={editing && isMobile}
        onClose={() => {
          setEditing(false);
          setDraft(file.name);
          setRenameError(null);
        }}
        title="Переименовать файл"
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const v = draft.trim();
            if (!v) return;
            rename.mutate(v);
          }}
          className="flex flex-col gap-3"
        >
          <TextField
            label="Имя файла"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            autoFocus
            error={renameError ?? undefined}
            required
          />
          <div className="flex gap-2 justify-end">
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setEditing(false);
                setDraft(file.name);
                setRenameError(null);
              }}
            >
              Отмена
            </Button>
            <Button
              type="submit"
              loading={rename.isPending}
              disabled={!draft.trim() || draft === file.name}
            >
              Сохранить
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
}

function PreviewBody({
  kind,
  url,
  fullUrl,
  mime,
  name,
  isHighQuality,
}: {
  kind: Kind;
  url: string;
  fullUrl: string;
  mime: string;
  name: string;
  isHighQuality: boolean;
}) {
  switch (kind) {
    case "image":
      // For "high", use the full file directly (skips thumb redirect roundtrip)
      return (
        <img
          src={isHighQuality ? fullUrl : url}
          alt={name}
          className="max-w-full max-h-[80vh] object-contain"
        />
      );
    case "video":
      return (
        <video
          src={fullUrl}
          controls
          preload="metadata"
          className="max-w-full max-h-[80vh]"
        >
          Your browser cannot play this video.
        </video>
      );
    case "audio":
      return (
        <audio
          src={fullUrl}
          controls
          preload="metadata"
          className="w-full max-w-xl"
        >
          Your browser cannot play this audio.
        </audio>
      );
    case "pdf":
      return (
        <embed
          src={fullUrl}
          type="application/pdf"
          className="w-full h-[80vh]"
        />
      );
    case "text":
      return (
        <iframe
          src={fullUrl}
          title={name}
          className="w-full h-[80vh] bg-white dark:bg-neutral-100 rounded"
        />
      );
    case "other":
    default:
      return (
        <div className="flex flex-col items-center text-center text-sm text-neutral-500 py-12">
          <div className="text-4xl mb-3">📄</div>
          <div className="font-medium text-neutral-700 dark:text-neutral-200">
            Превью не поддерживается для {mime || "этого типа"}
          </div>
          <div className="mt-1 text-xs">
            Используйте Download для скачивания.
          </div>
        </div>
      );
  }
}
