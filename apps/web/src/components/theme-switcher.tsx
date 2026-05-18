"use client";

import { cn } from "@/lib/cn";
import { useTranslations } from "next-intl";
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

type Theme = { id: string; key: string; bg: string; primary: string };

const THEMES: Theme[] = [
  {
    id: "",
    key: "indigo",
    bg: "oklch(0.215 0 0)",
    primary: "#063932",
  },
  { id: "light", key: "light", bg: "oklch(0.98 0 0)", primary: "#063932" },
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
  const t = useTranslations("theme");
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
            <p className="text-sm font-semibold text-foreground">{t("title")}</p>
            <p className="mt-0.5 text-xs text-muted-foreground">{t("subtitle")}</p>
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
                  <span className="flex-1">{t(th.key)}</span>
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
        aria-label={t("toggle")}
        aria-expanded={open}
        className="flex h-12 items-center gap-2 rounded-full border border-border bg-card px-5 text-sm font-semibold text-foreground shadow-pop transition-colors hover:bg-secondary"
      >
        <svg className="size-5 text-primary" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2" />
          <path d="M12 3a9 9 0 0 1 0 18Z" fill="currentColor" />
        </svg>
        {t("button")}
      </button>
    </div>
  );
}
