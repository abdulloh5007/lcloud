import type { ButtonHTMLAttributes, ReactNode } from "react";
import { classNames } from "@/lib/format";

type Variant = "primary" | "ghost" | "danger";
type Size = "sm" | "md";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  static?: boolean;
  children?: ReactNode;
}

const VARIANT: Record<Variant, string> = {
  primary:
    "bg-blue-600 text-white hover:bg-blue-700 disabled:bg-blue-400",
  ghost:
    "bg-transparent text-neutral-700 dark:text-neutral-200 hover:bg-neutral-100 dark:hover:bg-neutral-800",
  danger:
    "bg-red-600 text-white hover:bg-red-700 disabled:bg-red-400",
};

const SIZE: Record<Size, string> = {
  sm: "px-3 py-1.5 text-sm",
  md: "px-4 py-2 text-sm",
};

export function Button({
  variant = "primary",
  size = "md",
  loading,
  static: isStatic,
  className,
  disabled,
  children,
  ...rest
}: Props) {
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      className={classNames(
        "inline-flex min-h-10 min-w-10 items-center justify-center gap-2 rounded-lg font-medium transition-[scale,background-color,color,box-shadow] duration-150 ease-out disabled:cursor-not-allowed disabled:opacity-70",
        !isStatic && "active:scale-[0.96] disabled:active:scale-100",
        VARIANT[variant],
        SIZE[size],
        className,
      )}
    >
      {loading ? "…" : children}
    </button>
  );
}
