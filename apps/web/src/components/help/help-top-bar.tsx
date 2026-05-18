import { Logo } from "@/components/ui/logo";
import Link from "next/link";

export function HelpTopBar({ backHome }: { backHome: string }) {
  return (
    <header className="border-b border-border">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-5 sm:px-8">
        <Link href="/" className="rounded-lg focus-visible:outline-2" aria-label="social-media-v1">
          <Logo />
        </Link>
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
        >
          <span aria-hidden="true">&larr;</span> {backHome}
        </Link>
      </div>
    </header>
  );
}
