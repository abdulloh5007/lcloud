import { useEffect, type ReactNode } from "react";
import { classNames } from "@/lib/format";

interface Props {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
  width?: string;
}

export function Modal({ open, onClose, title, children, width = "max-w-md" }: Props) {
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className={classNames(
          "w-full mx-4 rounded-2xl bg-panel dark:bg-panel-dark p-5 surface-shadow motion-safe:animate-[modal-panel-in_180ms_cubic-bezier(0.2,0,0,1)]",
          width,
        )}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        {title && <h2 className="text-lg font-semibold mb-4 text-balance">{title}</h2>}
        {children}
      </div>
    </div>
  );
}
