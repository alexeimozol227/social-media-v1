import { cn } from "@/lib/cn";
import type { ReactNode } from "react";

type Tone = "error" | "success" | "info";

const TONES: Record<Tone, { box: string; icon: ReactNode }> = {
  error: {
    box: "border-destructive/40 bg-destructive/10 text-destructive-foreground",
    icon: (
      <path
        d="M12 8v5m0 3h.01M10.3 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.7 3.86a2 2 0 0 0-3.42 0Z"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    ),
  },
  success: {
    box: "border-success/40 bg-success/10 text-foreground",
    icon: (
      <path
        d="m5 13 4 4L19 7"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    ),
  },
  info: {
    box: "border-info/40 bg-info/10 text-foreground",
    icon: (
      <path
        d="M12 16v-5m0-4h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    ),
  },
};

/** Inline status banner. `error` uses role="alert" so screen readers
 * announce it immediately; success/info use polite live regions so
 * they don't interrupt. */
export function Alert({
  tone = "info",
  children,
  className,
}: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
}) {
  const t = TONES[tone];
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      aria-live={tone === "error" ? "assertive" : "polite"}
      className={cn(
        "flex items-start gap-2.5 rounded-lg border px-3.5 py-3 text-sm",
        t.box,
        className,
      )}
    >
      <svg className="mt-0.5 size-4 shrink-0" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        {t.icon}
      </svg>
      <span className="min-w-0">{children}</span>
    </div>
  );
}
