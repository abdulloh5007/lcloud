import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, clouds } from "@/api/client";
import { Button } from "./ui/Button";
import { TextField } from "./ui/TextField";
import { Modal } from "./ui/Modal";
import { Cloud as CloudIcon, Plus, Settings, Trash2, X } from "lucide-react";
import { classNames } from "@/lib/format";

interface Props {
  selectedCloudId: number | null;
  onSelect: (id: number | null) => void;
  /** Mobile drawer open state — `undefined` means desktop (always open). */
  mobileOpen?: boolean;
  onMobileClose?: () => void;
  onOpenSettings?: () => void;
}

export function Sidebar({
  selectedCloudId,
  onSelect,
  mobileOpen,
  onMobileClose,
  onOpenSettings,
}: Props) {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["clouds"], queryFn: () => clouds.list() });
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: (name: string) => clouds.create(name),
    onSuccess: (cloud) => {
      qc.invalidateQueries({ queryKey: ["clouds"] });
      setCreating(false);
      setNewName("");
      setCreateError(null);
      onSelect(cloud.id);
      onMobileClose?.();
    },
    onError: (e: unknown) => {
      setCreateError(
        e instanceof ApiError ? `${e.reason} (${e.status})` : "create failed",
      );
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => clouds.remove(id),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: ["clouds"] });
      if (selectedCloudId === id) onSelect(null);
    },
  });

  // Backdrop on mobile when drawer is open
  const showBackdrop = mobileOpen && onMobileClose;

  return (
    <>
      {showBackdrop && (
        <div
          className="fixed inset-0 z-30 bg-black/40 md:hidden"
          onClick={onMobileClose}
          aria-hidden="true"
        />
      )}
      <aside
        className={classNames(
          // Mobile: fixed off-canvas drawer; Desktop: static column
          "fixed inset-y-0 left-0 z-40 w-72 max-w-[85vw] shrink-0 border-r border-neutral-200 dark:border-neutral-800 bg-panel dark:bg-panel-dark flex flex-col transition-transform md:static md:translate-x-0 md:max-w-none md:w-72",
          mobileOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0",
        )}
      >
        <div className="px-4 pt-5 pb-3 flex items-center gap-2">
          <span className="text-xl">☁️</span>
          <span className="font-semibold">LCloud</span>
          {onMobileClose && (
            <button
              type="button"
              onClick={onMobileClose}
              className="ml-auto md:hidden p-1 text-neutral-500"
              aria-label="Close menu"
            >
              <X size={18} />
            </button>
          )}
        </div>
        <button
          type="button"
          onClick={() => {
            onSelect(null);
            onMobileClose?.();
          }}
          className={classNames(
            "mx-2 rounded-lg px-3 py-2 text-sm text-left flex items-center gap-2",
            selectedCloudId === null
              ? "bg-neutral-100 dark:bg-neutral-800 font-medium"
              : "hover:bg-neutral-50 dark:hover:bg-neutral-900",
          )}
        >
          <span>🔍</span>
          Все файлы / поиск
        </button>
        <div className="px-4 pt-4 pb-1 flex items-center justify-between">
          <span className="text-xs uppercase tracking-wide text-neutral-500">
            Clouds
          </span>
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="text-blue-600 hover:text-blue-700 p-1"
            aria-label="New cloud"
            title="New cloud"
          >
            <Plus size={16} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto thin-scroll px-2 pb-2">
          {list.isLoading && (
            <div className="px-3 py-2 text-sm text-neutral-500">…</div>
          )}
          {list.isError && (
            <div className="px-3 py-2 text-sm text-red-600">
              ошибка: {String(list.error)}
            </div>
          )}
          {(list.data ?? []).map((c) => (
            <div
              key={c.id}
              className={classNames(
                "group rounded-lg flex items-center gap-2 px-2 py-2",
                selectedCloudId === c.id
                  ? "bg-neutral-100 dark:bg-neutral-800"
                  : "hover:bg-neutral-50 dark:hover:bg-neutral-900",
              )}
            >
              <button
                type="button"
                onClick={() => {
                  onSelect(c.id);
                  onMobileClose?.();
                }}
                className="flex-1 flex items-center gap-2 text-sm text-left min-w-0"
              >
                <CloudIcon
                  size={16}
                  className="text-neutral-400 shrink-0"
                />
                <span className="truncate">{c.name}</span>
              </button>
              <button
                type="button"
                className="md:opacity-0 md:group-hover:opacity-100 text-neutral-400 hover:text-red-600 p-1 transition"
                onClick={() => {
                  if (window.confirm(`Отвязать «${c.name}»?`)) {
                    remove.mutate(c.id);
                  }
                }}
                aria-label={`Disconnect ${c.name}`}
                title="Disconnect"
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
          {list.data?.length === 0 && (
            <div className="px-3 py-2 text-sm text-neutral-400">
              Нет cloud-ов. Создайте первый.
            </div>
          )}
        </div>

        {/* Settings button at the bottom of the sidebar */}
        {onOpenSettings && (
          <button
            type="button"
            onClick={onOpenSettings}
            className="m-2 px-3 py-2 rounded-lg flex items-center gap-2 text-sm text-neutral-600 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-900 border-t border-neutral-200 dark:border-neutral-800 -mt-px"
          >
            <Settings size={16} />
            Настройки
          </button>
        )}

        <Modal
          open={creating}
          onClose={() => setCreating(false)}
          title="Новый cloud"
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate(newName.trim());
            }}
            className="flex flex-col gap-3"
          >
            <TextField
              label="Название"
              autoFocus
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="My photos"
              error={createError ?? undefined}
              required
            />
            <p className="text-xs text-neutral-500">
              Создаётся новая Telegram-супергруппа с маркером LCLOUD1.
            </p>
            <div className="flex gap-2 justify-end">
              <Button
                type="button"
                variant="ghost"
                onClick={() => setCreating(false)}
              >
                Отмена
              </Button>
              <Button
                type="submit"
                loading={create.isPending}
                disabled={!newName.trim()}
              >
                Создать
              </Button>
            </div>
          </form>
        </Modal>
      </aside>
    </>
  );
}
