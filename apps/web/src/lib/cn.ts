/** Tiny class-name joiner. Filters falsy values so conditional
 * classes (`cond && "..."`) collapse cleanly. No dependency on
 * clsx/tailwind-merge — we don't compose conflicting utilities. */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
