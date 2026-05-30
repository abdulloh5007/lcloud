import type { ThumbSize } from "@/api/types";
import { classNames } from "@/lib/format";

interface Props {
  value: ThumbSize;
  onChange: (q: ThumbSize) => void;
}

const LABEL: Record<ThumbSize, string> = {
  low: "Low",
  med: "Med",
  high: "HD",
};

const TITLE: Record<ThumbSize, string> = {
  low: "Низкое (быстро, экономит трафик)",
  med: "Среднее (по умолчанию)",
  high: "Высокое — оригинал",
};

export function QualitySwitcher({ value, onChange }: Props) {
  return (
    <div
      className="flex items-center rounded-lg border border-neutral-200 dark:border-neutral-700 bg-panel dark:bg-panel-dark text-xs"
      role="radiogroup"
      aria-label="Preview quality"
    >
      {(["low", "med", "high"] as ThumbSize[]).map((q, i, arr) => (
        <button
          key={q}
          type="button"
          role="radio"
          aria-checked={value === q}
          onClick={() => onChange(q)}
          title={TITLE[q]}
          className={classNames(
            "px-2 py-1 sm:px-3 sm:py-1.5",
            i === 0 && "rounded-l-lg",
            i === arr.length - 1 && "rounded-r-lg",
            value === q
              ? "bg-neutral-100 dark:bg-neutral-800 font-medium"
              : "hover:bg-neutral-50 dark:hover:bg-neutral-900",
          )}
        >
          {LABEL[q]}
        </button>
      ))}
    </div>
  );
}
