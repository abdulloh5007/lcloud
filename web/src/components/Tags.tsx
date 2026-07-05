import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import * as Lucide from "lucide-react";
import { ApiError, files as filesApi, tags as tagsApi } from "@/api/client";
import type { TagRow } from "@/api/types";
import { classNames } from "@/lib/format";
import { Button } from "./ui/Button";
import { Modal } from "./ui/Modal";
import { TextField } from "./ui/TextField";

const PALETTE = [
  "#ef4444", "#f97316", "#eab308", "#22c55e", "#10b981",
  "#06b6d4", "#3b82f6", "#6366f1", "#a855f7", "#ec4899",
  "#64748b", "#737373",
];

const ICONS = [
  "Star", "Heart", "Bookmark", "Flag", "Tag",
  "Briefcase", "Folder", "FileText", "Image", "Music",
  "Video", "Code", "Camera", "Calendar", "MapPin",
  "Phone", "Mail", "User", "Users", "Lock",
];

type IconRecord = Record<string, typeof Lucide.Tag | undefined>;

function getIcon(name: string): typeof Lucide.Tag {
  return (Lucide as unknown as IconRecord)[name] ?? Lucide.Tag;
}

// --------------------------------------------------------------------- chip


export function TagChip({
  tag,
  size = "md",
  onClick,
}: {
  tag: TagRow;
  size?: "sm" | "md";
  onClick?: () => void;
}) {
  const Icon = getIcon(tag.icon);
  const px =
    size === "sm" ? "px-2 py-0.5 text-xs gap-1" : "px-2.5 py-1 text-sm gap-1.5";
  const iconSize = size === "sm" ? 12 : 14;
  return (
    <button
      type="button"
      onClick={onClick}
      className={classNames(
        "inline-flex items-center rounded-full ring-1 ring-transparent hover:ring-neutral-300 dark:hover:ring-neutral-700 transition",
        px,
      )}
      style={{ backgroundColor: tag.bg_color, color: tag.color }}
      title={onClick ? "Edit / remove" : tag.name}
    >
      <Icon size={iconSize} />
      <span className="truncate max-w-[120px]">{tag.name}</span>
    </button>
  );
}

// --------------------------------------------------------------------- file row


export function FileTagsBar({ fileId }: { fileId: number }) {
  const allTags = useQuery({
    queryKey: ["tags"],
    queryFn: () => tagsApi.list(),
  });
  const fileTags = useQuery({
    queryKey: ["file-tags", fileId],
    queryFn: () => filesApi.getTags(fileId),
  });

  const [assignOpen, setAssignOpen] = useState(false);
  const [editingTag, setEditingTag] = useState<TagRow | null>(null);

  const tagsOnFile = fileTags.data ?? [];
  const noTagsYet = tagsOnFile.length === 0;

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {noTagsYet && (
        <button
          type="button"
          onClick={() => setAssignOpen(true)}
          className="inline-flex min-h-10 items-center gap-1 px-3 py-1 text-xs rounded-full border border-dashed border-neutral-300 dark:border-neutral-700 text-neutral-500 hover:border-blue-500 hover:text-blue-600 transition-[border-color,color,scale] duration-150 ease-out active:scale-[0.96]"
        >
          <Lucide.Plus size={12} />
          тег
        </button>
      )}
      {tagsOnFile.map((t) => (
        <TagChip key={t.id} tag={t} size="sm" onClick={() => setEditingTag(t)} />
      ))}
      {!noTagsYet && (
        <button
          type="button"
          onClick={() => setAssignOpen(true)}
          className="inline-flex h-10 w-10 items-center justify-center rounded-full text-neutral-400 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-950/30 transition-[background-color,color,scale] duration-150 ease-out active:scale-[0.96]"
          aria-label="Add tag"
          title="Добавить тег"
        >
          <Lucide.Plus size={14} />
        </button>
      )}

      <AssignTagsModal
        open={assignOpen}
        onClose={() => setAssignOpen(false)}
        fileId={fileId}
        currentTagIds={tagsOnFile.map((t) => t.id)}
        allTags={allTags.data ?? []}
      />
      <EditTagModal
        tag={editingTag}
        onClose={() => setEditingTag(null)}
      />
    </div>
  );
}

// --------------------------------------------------------------------- assign modal


function AssignTagsModal({
  open,
  onClose,
  fileId,
  currentTagIds,
  allTags,
}: {
  open: boolean;
  onClose: () => void;
  fileId: number;
  currentTagIds: number[];
  allTags: TagRow[];
}) {
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);

  const set = useMutation({
    mutationFn: (ids: number[]) => filesApi.setTags(fileId, ids),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["file-tags", fileId] });
      qc.invalidateQueries({ queryKey: ["files"] });
    },
  });

  function toggle(tagId: number) {
    const next = new Set(currentTagIds);
    if (next.has(tagId)) next.delete(tagId);
    else next.add(tagId);
    set.mutate(Array.from(next).sort((a, b) => a - b));
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Теги для файла"
      width="max-w-md"
    >
      {creating ? (
        <CreateTagInline
          onDone={() => setCreating(false)}
          onCancel={() => setCreating(false)}
        />
      ) : (
        <>
          {allTags.length === 0 ? (
            <p className="text-sm text-neutral-500 mb-4">
              У вас ещё нет тегов. Создайте первый.
            </p>
          ) : (
            <div className="flex flex-wrap gap-2 mb-4">
              {allTags.map((t) => {
                const selected = currentTagIds.includes(t.id);
                const Icon = getIcon(t.icon);
                return (
                  <button
                    key={t.id}
                    type="button"
                    onClick={() => toggle(t.id)}
                    className={classNames(
                      "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-sm transition ring-2",
                      selected
                        ? "ring-blue-500"
                        : "ring-transparent hover:ring-neutral-300 dark:hover:ring-neutral-700",
                    )}
                    style={{ backgroundColor: t.bg_color, color: t.color }}
                  >
                    <Icon size={14} />
                    <span className="truncate max-w-[140px]">{t.name}</span>
                    {selected && (
                      <Lucide.Check size={12} className="ml-0.5" />
                    )}
                  </button>
                );
              })}
            </div>
          )}
          <div className="flex justify-between items-center pt-2 border-t border-neutral-200 dark:border-neutral-800 -mx-5 -mb-5 px-5 py-3">
            <button
              type="button"
              onClick={() => setCreating(true)}
              className="text-sm text-blue-600 hover:text-blue-700"
            >
              <Lucide.Plus size={14} className="inline -mt-0.5 mr-1" />
              новый тег
            </button>
            <Button variant="ghost" onClick={onClose}>
              Готово
            </Button>
          </div>
        </>
      )}
    </Modal>
  );
}

// --------------------------------------------------------------------- edit / delete modal


function EditTagModal({
  tag,
  onClose,
}: {
  tag: TagRow | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [color, setColor] = useState("#3b82f6");
  const [bg, setBg] = useState("#fafafa");
  const [icon, setIcon] = useState("Tag");
  const [error, setError] = useState<string | null>(null);

  // Reset state when tag changes
  if (tag && name === "" && tag.name !== "") {
    // crude one-time init via render — fine because key={tag.id}
    setName(tag.name);
    setColor(tag.color);
    setBg(tag.bg_color);
    setIcon(tag.icon);
  }

  const save = useMutation({
    mutationFn: () =>
      tagsApi.patch(tag!.id, { name, color, bg_color: bg, icon }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tags"] });
      qc.invalidateQueries({ queryKey: ["file-tags"] });
      onClose();
    },
    onError: (e: unknown) => {
      setError(e instanceof ApiError ? e.reason : "save failed");
    },
  });

  const remove = useMutation({
    mutationFn: () => tagsApi.remove(tag!.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tags"] });
      qc.invalidateQueries({ queryKey: ["file-tags"] });
      onClose();
    },
  });

  if (!tag) return null;

  const Icon = getIcon(icon);
  return (
    <Modal
      key={tag.id}
      open={tag !== null}
      onClose={onClose}
      title="Редактировать тег"
      width="max-w-lg"
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
        className="flex flex-col gap-4"
      >
        <TextField
          label="Название"
          value={name}
          onChange={(e) => setName(e.target.value)}
          autoFocus
          error={error ?? undefined}
        />
        <PalettePicker label="Цвет иконки" value={color} onChange={setColor} />
        <div>
          <div className="text-xs font-medium text-neutral-600 dark:text-neutral-400 mb-2">
            Цвет фона
          </div>
          <input
            type="color"
            value={bg}
            onChange={(e) => setBg(e.target.value)}
            className="h-10 w-20 rounded cursor-pointer border border-neutral-300 dark:border-neutral-700"
          />
        </div>
        <IconPicker value={icon} onChange={setIcon} />
        <div className="flex items-center gap-3 p-3 rounded-lg bg-neutral-50 dark:bg-neutral-900">
          <span className="text-xs text-neutral-500">Превью:</span>
          <span
            className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-sm"
            style={{ backgroundColor: bg, color }}
          >
            <Icon size={14} />
            {name || "Tag name"}
          </span>
        </div>
        <div className="flex justify-between gap-2 pt-2 border-t border-neutral-200 dark:border-neutral-800 -mx-5 -mb-5 px-5 py-3">
          <Button
            type="button"
            variant="danger"
            size="sm"
            loading={remove.isPending}
            onClick={() => {
              if (window.confirm(`Удалить тег «${tag.name}»?`)) {
                remove.mutate();
              }
            }}
          >
            <Lucide.Trash2 size={14} />
            Удалить
          </Button>
          <div className="flex gap-2">
            <Button type="button" variant="ghost" onClick={onClose}>
              Отмена
            </Button>
            <Button
              type="submit"
              size="sm"
              loading={save.isPending}
              disabled={!name.trim()}
            >
              Сохранить
            </Button>
          </div>
        </div>
      </form>
    </Modal>
  );
}

// --------------------------------------------------------------------- inline create-new-tag form (inside AssignTagsModal)


function CreateTagInline({
  onDone,
  onCancel,
}: {
  onDone: () => void;
  onCancel: () => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [color, setColor] = useState(PALETTE[0]);
  const [bg, setBg] = useState("#fafafa");
  const [icon, setIcon] = useState(ICONS[0]);
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () =>
      tagsApi.create({
        name: name.trim(),
        color,
        icon,
        bg_color: bg,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tags"] });
      onDone();
    },
    onError: (e: unknown) => {
      setError(e instanceof ApiError ? e.reason : "create failed");
    },
  });

  const Icon = getIcon(icon);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate();
      }}
      className="flex flex-col gap-3"
    >
      <TextField
        label="Название"
        autoFocus
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Important"
        error={error ?? undefined}
      />
      <PalettePicker label="Цвет иконки" value={color} onChange={setColor} />
      <div>
        <div className="text-xs font-medium text-neutral-600 dark:text-neutral-400 mb-2">
          Цвет фона
        </div>
        <input
          type="color"
          value={bg}
          onChange={(e) => setBg(e.target.value)}
          className="h-10 w-20 rounded cursor-pointer border border-neutral-300 dark:border-neutral-700"
        />
      </div>
      <IconPicker value={icon} onChange={setIcon} />
      <div className="flex items-center gap-3 p-3 rounded-lg bg-neutral-50 dark:bg-neutral-900">
        <span className="text-xs text-neutral-500">Превью:</span>
        <span
          className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-sm"
          style={{ backgroundColor: bg, color }}
        >
          <Icon size={14} />
          {name || "Tag name"}
        </span>
      </div>
      <div className="flex gap-2 justify-end pt-2 border-t border-neutral-200 dark:border-neutral-800 -mx-5 -mb-5 px-5 py-3">
        <Button type="button" variant="ghost" onClick={onCancel}>
          Назад
        </Button>
        <Button type="submit" loading={create.isPending} disabled={!name.trim()}>
          Создать
        </Button>
      </div>
    </form>
  );
}

// --------------------------------------------------------------------- pickers


function PalettePicker({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (c: string) => void;
}) {
  return (
    <div>
      <div className="text-xs font-medium text-neutral-600 dark:text-neutral-400 mb-2">
        {label}
      </div>
      <div className="flex flex-wrap gap-2">
        {PALETTE.map((c) => (
          <button
            key={c}
            type="button"
            onClick={() => onChange(c)}
            style={{ backgroundColor: c }}
            className={classNames(
              "w-8 h-8 rounded-full ring-2 ring-offset-2",
              value === c
                ? "ring-blue-500"
                : "ring-transparent ring-offset-transparent",
            )}
          />
        ))}
      </div>
    </div>
  );
}

function IconPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (i: string) => void;
}) {
  return (
    <div>
      <div className="text-xs font-medium text-neutral-600 dark:text-neutral-400 mb-2">
        Иконка
      </div>
      <div className="grid grid-cols-10 gap-2">
        {ICONS.map((n) => {
          const I = getIcon(n);
          return (
            <button
              key={n}
              type="button"
              onClick={() => onChange(n)}
              className={classNames(
                "w-8 h-8 rounded-md flex items-center justify-center",
                value === n
                  ? "bg-blue-100 dark:bg-blue-900 text-blue-600"
                  : "hover:bg-neutral-100 dark:hover:bg-neutral-800",
              )}
              title={n}
            >
              <I size={16} />
            </button>
          );
        })}
      </div>
    </div>
  );
}
