import { cn } from "@/lib/cn";

/** Brand mark — geometric "broadcast / network" glyph in a rounded
 * tile. Vector only, themeable via currentColor. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "grid place-items-center rounded-xl bg-primary text-primary-foreground shadow-sm",
        className,
      )}
      aria-hidden="true"
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        className="size-[58%]"
        aria-hidden="true"
        focusable="false"
      >
        <circle cx="12" cy="12" r="2.4" fill="currentColor" />
        <path
          d="M12 4.2v3M12 16.8v3M4.2 12h3M16.8 12h3"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        />
        <circle cx="12" cy="3" r="1.6" fill="currentColor" />
        <circle cx="12" cy="21" r="1.6" fill="currentColor" />
        <circle cx="3" cy="12" r="1.6" fill="currentColor" />
        <circle cx="21" cy="12" r="1.6" fill="currentColor" />
      </svg>
    </span>
  );
}

export function Logo({
  className,
  wordmark = true,
}: {
  className?: string;
  wordmark?: boolean;
}) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <LogoMark className="size-9" />
      {wordmark && (
        <span className="text-lg font-semibold tracking-tight text-foreground">
          social<span className="text-muted-foreground">·</span>media
        </span>
      )}
    </span>
  );
}
