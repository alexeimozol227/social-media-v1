import { getRequestConfig } from "next-intl/server";

// MVP ships RU-only (docs/04-architecture.md §22 D63). EN is loaded
// for system pages (404 / 500 / legal stubs) but never selected by
// negotiation yet.
const SUPPORTED_LOCALES = ["ru", "en"] as const;
const DEFAULT_LOCALE = "ru";

export default getRequestConfig(async () => {
  const locale = DEFAULT_LOCALE;
  if (!SUPPORTED_LOCALES.includes(locale as (typeof SUPPORTED_LOCALES)[number])) {
    throw new Error(`Unsupported locale: ${locale}`);
  }
  return {
    locale,
    messages: (await import(`../messages/${locale}.json`)).default,
  };
});
