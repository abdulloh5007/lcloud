import type { InputHTMLAttributes } from "react";
import { classNames } from "@/lib/format";

interface Props extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
}

export function TextField({ label, error, className, id, ...rest }: Props) {
  const fieldId = id ?? `f-${rest.name ?? Math.random().toString(36).slice(2, 8)}`;
  return (
    <div className="flex flex-col gap-1">
      {label && (
        <label htmlFor={fieldId} className="text-xs font-medium text-neutral-600 dark:text-neutral-400">
          {label}
        </label>
      )}
      <input
        {...rest}
        id={fieldId}
        className={classNames(
          "rounded-lg border px-3 py-2 text-sm bg-panel dark:bg-panel-dark",
          "border-neutral-200 dark:border-neutral-700",
          "focus:border-blue-500 dark:focus:border-blue-400",
          error && "border-red-500",
          className,
        )}
      />
      {error && <span className="text-xs text-red-600">{error}</span>}
    </div>
  );
}
