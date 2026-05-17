import { ThemeSwitcher } from "@/components/theme-switcher";
import { QueryProvider } from "@/lib/query-provider";
import type { Metadata } from "next";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getMessages } from "next-intl/server";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "social-media-v1",
  description: "AI Operating System for Social Networks",
};

// TEMPORARY: applies the saved palette before paint (no FOUC). Remove
// together with ThemeSwitcher once a palette is chosen.
const THEME_BOOTSTRAP = `try{var t=localStorage.getItem("sm.theme");if(t)document.documentElement.dataset.theme=t}catch(e){}`;

export default async function RootLayout({
  children,
}: {
  children: ReactNode;
}) {
  const locale = await getLocale();
  const messages = await getMessages();
  return (
    <html lang={locale}>
      <body>
        {/* biome-ignore lint/security/noDangerouslySetInnerHtml: tiny static theme bootstrap, no user input */}
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
        <NextIntlClientProvider locale={locale} messages={messages}>
          <QueryProvider>{children}</QueryProvider>
        </NextIntlClientProvider>
        <ThemeSwitcher />
      </body>
    </html>
  );
}
