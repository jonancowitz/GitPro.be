import type { ReactNode } from "react";

export const panelClass =
  "min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.03)] sm:p-5";

export const primaryButtonClass =
  "inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-accent px-4 text-sm font-semibold text-accent-ink transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-45";

export const secondaryButtonClass =
  "inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-line bg-raised px-4 text-sm font-medium text-paper transition hover:border-muted disabled:cursor-not-allowed disabled:opacity-45";

export const labelClass = "mb-1.5 text-xs font-medium uppercase tracking-[0.14em] text-muted";

export function FieldLabel({ children, htmlFor }: { children: ReactNode; htmlFor?: string }) {
  return (
    <label htmlFor={htmlFor} className={labelClass}>
      {children}
    </label>
  );
}
