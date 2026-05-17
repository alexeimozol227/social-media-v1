"use client";

import { cn } from "@/lib/cn";
import { useEffect, useState } from "react";

/**
 * TEMPORARY palette picker.
 *
 * Lets us preview color themes at runtime (sets `html[data-theme]`,
 * persisted in localStorage). Once a palette is chosen, fold its
 * values into `@theme` in globals.css and delete: this component,
 * its mount in app/layout.tsx, the data-theme blocks in globals.css,
 * and the bootstrap script.
 */
const STORAGE_KEY = "sm.theme";

type Theme = { id: string; label: string; bg: string; primary: string };

const THEMES: Theme[] = [
  {
    id: "",
    label: "Indigo (default)",
    bg: "oklch(0.215 0.018 280)",
    primary: "oklch(0.62 0.2 277)",
  },
  { id: "violet", label: "Violet", bg: "oklch(0.21 0.03 305)", primary: "oklch(0.62 0.22 312)" },
  { id: "ocean", label: "Ocean", bg: "oklch(0.2 0.03 245)", primary: "oklch(0.64 0.16 230)" },
  { id: "emerald", label: "Emerald", bg: "oklch(0.2 0.025 165)", primary: "oklch(0.66 0.15 158)" },
  { id: "teal", label: "Teal", bg: "oklch(0.2 0.025 200)", primary: "oklch(0.66 0.13 190)" },
  { id: "rose", label: "Rose", bg: "oklch(0.21 0.028 350)", primary: "oklch(0.62 0.22 5)" },
  { id: "amber", label: "Amber", bg: "oklch(0.21 0.022 70)", primary: "oklch(0.76 0.15 72)" },
  { id: "crimson", label: "Crimson", bg: "oklch(0.2 0.02 20)", primary: "oklch(0.58 0.23 25)" },
  {
    id: "graphite",
    label: "Graphite",
    bg: "oklch(0.22 0.004 270)",
    primary: "oklch(0.72 0.05 255)",
  },
  {
    id: "midnight",
    label: "Midnight",
    bg: "oklch(0.17 0.03 265)",
    primary: "oklch(0.68 0.15 215)",
  },
  { id: "sunset", label: "Sunset", bg: "oklch(0.22 0.03 35)", primary: "oklch(0.66 0.2 38)" },
  { id: "light", label: "Light", bg: "oklch(0.98 0.004 280)", primary: "oklch(0.55 0.22 277)" },
  {
    id: "light-warm",
    label: "Light warm",
    bg: "oklch(0.98 0.012 75)",
    primary: "oklch(0.58 0.18 42)",
  },
];

function applyTheme(id: string) {
  const root = document.documentElement;
  if (id) {
    root.dataset.theme = id;
  } else {
    delete root.dataset.theme;
  }
}

export function ThemeSwitcher() {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState("");

  useEffect(() => {
    setActive(localStorage.getItem(STORAGE_KEY) ?? "");
  }, []);

  function select(id: string) {
    applyTheme(id);
    if (id) {
      localStorage.setItem(STORAGE_KEY, id);
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
    setActive(id);
  }

  return (
    <div className="fixed bottom-4 right-4 z-[100] print:hidden">
      {open && (
        <div className="mb-3 w-64 overflow-hidden rounded-2xl border border-border bg-card shadow-pop">
          <div className="border-b border-border px-4 py-3">
            <p className="text-sm font-semibold text-foreground">Theme (temporary)</p>
            <p className="mt-0.5 text-xs text-muted-foreground">Palette preview — removed later</p>
          </div>
          <ul className="max-h-80 overflow-y-auto p-2">
            {THEMES.map((th) => (
              <li key={th.id || "default"}>
                <button
                  type="button"
                  onClick={() => select(th.id)}
                  className={cn(
                    "flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm transition-colors",
                    active === th.id
                      ? "bg-secondary text-foreground"
                      : "text-muted-foreground hover:bg-secondary hover:text-foreground",
                  )}
                >
                  <span
                    className="flex size-7 shrink-0 items-center overflow-hidden rounded-full border border-border"
                    aria-hidden="true"
                  >
                    <span className="h-full w-1/2" style={{ background: th.bg }} />
                    <span className="h-full w-1/2" style={{ background: th.primary }} />
                  </span>
                  <span className="flex-1">{th.label}</span>
                  {active === th.id && (
                    <svg
                      className="size-4 text-primary"
                      viewBox="0 0 24 24"
                      fill="none"
                      aria-hidden="true"
                    >
                      <path
                        d="m5 13 4 4L19 7"
                        stroke="currentColor"
                        strokeWidth="2.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Toggle theme"
        aria-expanded={open}
        className="flex h-12 items-center gap-2 rounded-full border border-border bg-card px-5 text-sm font-semibold text-foreground shadow-pop transition-colors hover:bg-secondary"
      >
        <svg className="size-5 text-primary" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2" />
          <path d="M12 3a9 9 0 0 1 0 18Z" fill="currentColor" />
        </svg>
        Theme
      </button>
    </div>
  );
}
