import type { ReactNode } from "react";

/** Consistent page heading for the auth flow: h1 + optional
 * supporting copy. Keeps a single h1 per screen (heading-hierarchy). */
export function AuthHeading({
  title,
  description,
}: {
  title: string;
  description?: ReactNode;
}) {
  return (
    <div className="mb-7 flex flex-col gap-2">
      <h1 className="text-2xl font-semibold tracking-tight text-foreground">{title}</h1>
      {description && (
        <p className="text-sm leading-relaxed text-muted-foreground">{description}</p>
      )}
    </div>
  );
}
