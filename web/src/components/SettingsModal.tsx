import { useState, type ReactNode } from "react";
import { Modal } from "./ui/Modal";
import { classNames } from "@/lib/format";
import { ApiKeysSection } from "./ApiKeysSection";
import { AccountSection } from "./AccountSection";
import type { ThumbSize } from "@/api/types";
import type { AuthMe } from "@/api/v2_client";

interface Props {
  open: boolean;
  onClose: () => void;
  imageQuality: ThumbSize;
  videoQuality: ThumbSize;
  onChangeImageQuality: (q: ThumbSize) => void;
  onChangeVideoQuality: (q: ThumbSize) => void;
  me?: AuthMe;
  onLogout: () => void;
}

type TabKey = "general" | "api_keys" | "account";

const TABS: Array<{ key: TabKey; label: string; icon: string }> = [
  { key: "general", label: "Общие", icon: "⚙️" },
  { key: "api_keys", label: "API-ключи", icon: "🔑" },
  { key: "account", label: "Аккаунт", icon: "👤" },
];

const QUALITY_OPTIONS: Array<{ value: ThumbSize; label: string; hint: string }> =
  [
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
  me,
  onLogout,
}: Props) {
  const [tab, setTab] = useState<TabKey>("general");
  return (
    <Modal open={open} onClose={onClose} title="Настройки" width="max-w-2xl">
      <div className="flex border-b border-neutral-200 dark:border-neutral-800 -mx-5 mb-4 px-3 overflow-x-auto">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            className={classNames(
              "px-4 py-2 text-sm border-b-2 transition whitespace-nowrap",
              tab === t.key
                ? "border-blue-600 text-blue-600 font-medium"
                : "border-transparent text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200"
            )}
          >
            <span className="mr-1">{t.icon}</span>
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
            description="Влияет на превью видео. Сейчас в проде доступно только Original."
            value={videoQuality}
            onChange={onChangeVideoQuality}
          />
        </div>
      )}
      {tab === "api_keys" && <ApiKeysSection />}
      {tab === "account" && me && (
        <AccountSection me={me} onLogout={onLogout} />
      )}
      {tab === "account" && !me && (
        <div className="text-sm text-neutral-500">Загрузка…</div>
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
    <div>
      <h3 className="font-medium mb-1">{label}</h3>
      <p className="text-xs text-neutral-500 mb-3">{description}</p>
      <div className="grid grid-cols-3 gap-2">
        {QUALITY_OPTIONS.map((opt) => (
          <Choice
            key={opt.value}
            selected={value === opt.value}
            onClick={() => onChange(opt.value)}
          >
            <div className="font-medium text-sm">{opt.label}</div>
            <div className="text-xs text-neutral-500 mt-1">{opt.hint}</div>
          </Choice>
        ))}
      </div>
    </div>
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
      onClick={onClick}
      className={classNames(
        "text-left p-3 rounded-lg border-2 transition",
        selected
          ? "border-blue-600 bg-blue-50 dark:bg-blue-950/30"
          : "border-neutral-200 dark:border-neutral-800 hover:border-neutral-300 dark:hover:border-neutral-700"
      )}
    >
      {children}
    </button>
  );
}
