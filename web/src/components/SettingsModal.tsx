import { useState, type ReactNode } from "react";
import { Modal } from "./ui/Modal";
import { classNames } from "@/lib/format";
import { ApiKeysSection } from "./ApiKeysSection";
import { AccountSection } from "./AccountSection";
import { PaymentRequestsAdmin } from "./PaymentRequestsAdmin";
import type { AuthMe } from "@/api/v2_client";
import {
  CreditCard,
  Gem,
  KeyRound,
  Package,
  Settings,
  User,
  type LucideIcon,
} from "lucide-react";

interface Props {
  open: boolean;
  onClose: () => void;
  compressUploads: boolean;
  onChangeCompressUploads: (v: boolean) => void;
  me?: AuthMe;
  onLogout: () => void;
}

type TabKey = "general" | "api_keys" | "account" | "payments";

export function SettingsModal({
  open,
  onClose,
  compressUploads,
  onChangeCompressUploads,
  me,
  onLogout,
}: Props) {
  // Build tabs list dynamically — admin gets an extra "Заявки" tab.
  const tabs: Array<{ key: TabKey; label: string; icon: LucideIcon }> = [
    { key: "general", label: "Общие", icon: Settings },
    { key: "api_keys", label: "API-ключи", icon: KeyRound },
    { key: "account", label: "Аккаунт", icon: User },
  ];
  if (me?.role === "admin") {
    tabs.push({ key: "payments", label: "Заявки", icon: CreditCard });
  }

  const [tab, setTab] = useState<TabKey>("general");
  return (
    <Modal open={open} onClose={onClose} title="Настройки" width="max-w-2xl">
      <div className="flex border-b border-neutral-200 dark:border-neutral-800 -mx-5 mb-4 px-3 overflow-x-auto">
        {tabs.map((t) => {
          const Icon = t.icon;
          return (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            className={classNames(
              "px-4 py-2 text-sm border-b-2 transition-[border-color,color] duration-150 ease-out whitespace-nowrap",
              tab === t.key
                ? "border-blue-600 text-blue-600 font-medium"
                : "border-transparent text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200"
            )}
          >
            <Icon size={14} className="mr-1.5 inline -mt-0.5" />
            {t.label}
          </button>
          );
        })}
      </div>
      {tab === "general" && (
        <div className="space-y-5">
          <CompressionSection
            value={compressUploads}
            onChange={onChangeCompressUploads}
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
      {tab === "payments" && <PaymentRequestsAdmin />}
    </Modal>
  );
}

function CompressionSection({
  value,
  onChange,
}: {
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div>
      <h3 className="font-medium mb-1">Загрузка файлов</h3>
      <p className="text-xs text-neutral-500 mb-3">
        Применяется к изображениям и видео — форматам, которые можно сжать.
        Простые файлы (документы, архивы) загружаются как есть в любом случае.
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <Choice
          selected={value === true}
          onClick={() => onChange(true)}
        >
          <div className="flex items-center gap-2 font-medium text-sm">
            <Package size={16} className="text-blue-600" />
            Сжимать (по умолчанию)
          </div>
          <div className="text-xs text-neutral-500 mt-1">
            Экономит место (~70% для фото). Минимальная потеря качества (JPEG q=85).
          </div>
        </Choice>
        <Choice
          selected={value === false}
          onClick={() => onChange(false)}
        >
          <div className="flex items-center gap-2 font-medium text-sm">
            <Gem size={16} className="text-violet-600" />
            Оригинал
          </div>
          <div className="text-xs text-neutral-500 mt-1">
            Без сжатия. Файл сохраняется байт-в-байт. Больше места.
          </div>
        </Choice>
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
        "text-left p-3 rounded-xl border-2 transition-[border-color,background-color,scale] duration-150 ease-out active:scale-[0.96]",
        selected
          ? "border-blue-600 bg-blue-50 dark:bg-blue-950/30"
          : "border-neutral-200 dark:border-neutral-800 hover:border-neutral-300 dark:hover:border-neutral-700"
      )}
    >
      {children}
    </button>
  );
}
