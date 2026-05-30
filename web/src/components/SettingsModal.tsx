import { useState, type ReactNode } from "react";
import { Modal } from "./ui/Modal";
import { classNames } from "@/lib/format";
import type { ThumbSize } from "@/api/types";

interface Props {
  open: boolean;
  onClose: () => void;
  imageQuality: ThumbSize;
  videoQuality: ThumbSize;
  onChangeImageQuality: (q: ThumbSize) => void;
  onChangeVideoQuality: (q: ThumbSize) => void;
}

type TabKey = "general";

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: "general", label: "Общие" },
];

const QUALITY_OPTIONS: Array<{ value: ThumbSize; label: string; hint: string }> = [
  { value: "low", label: "Low", hint: "Самое лёгкое — быстро на медленном интернете" },
  { value: "med", label: "Medium", hint: "По умолчанию — серверный ресайз до 800px" },
  { value: "high", label: "HD", hint: "Оригинал — без ресайза" },
];

export function SettingsModal({
  open,
  onClose,
  imageQuality,
  videoQuality,
  onChangeImageQuality,
  onChangeVideoQuality,
}: Props) {
  const [tab, setTab] = useState<TabKey>("general");
  return (
    <Modal open={open} onClose={onClose} title="Настройки" width="max-w-xl">
      <div className="flex border-b border-neutral-200 dark:border-neutral-800 -mx-5 mb-4 px-3">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            className={classNames(
              "px-4 py-2 text-sm border-b-2 transition",
              tab === t.key
                ? "border-blue-600 text-blue-600 font-medium"
                : "border-transparent text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === "general" && (
        <div className="space-y-6">
          <QualitySection
            label="Качество картинок"
            description="Применяется к сетке файлов и в превью. По умолчанию: Low."
            value={imageQuality}
            onChange={onChangeImageQuality}
          />
          <QualitySection
            label="Качество видео"
            description="Влияет на превью видео. Сейчас в проде доступно только Original (стрим из Telegram)."
            value={videoQuality}
            onChange={onChangeVideoQuality}
          />
        </div>
      )}
    </Modal>
  );
}

function QualitySection({
  label,
  description,
  value,
  onChange,
}: {
  label: string;
  description: string;
  value: ThumbSize;
  onChange: (q: ThumbSize) => void;
}) {
  return (
    <section>
      <div className="text-sm font-medium mb-1">{label}</div>
      <p className="text-xs text-neutral-500 dark:text-neutral-400 mb-3">
        {description}
      </p>
      <div className="grid grid-cols-3 gap-2" role="radiogroup" aria-label={label}>
        {QUALITY_OPTIONS.map((o) => (
          <Choice
            key={o.value}
            selected={value === o.value}
            onClick={() => onChange(o.value)}
          >
            <div className="text-sm font-medium">{o.label}</div>
            <div className="text-[10px] text-neutral-500 mt-0.5 leading-tight">
              {o.hint}
            </div>
          </Choice>
        ))}
      </div>
    </section>
  );
}

function Choice({
  selected,
  onClick,
  children,
}: {
  selected: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={onClick}
      className={classNames(
        "rounded-lg border p-2 text-left transition",
        selected
          ? "border-blue-500 bg-blue-50/40 dark:bg-blue-950/20"
          : "border-neutral-200 dark:border-neutral-700 hover:border-neutral-300 dark:hover:border-neutral-600",
      )}
    >
      {children}
    </button>
  );
}
