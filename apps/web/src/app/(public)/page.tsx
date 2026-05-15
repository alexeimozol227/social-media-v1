import { getTranslations } from "next-intl/server";
import Link from "next/link";

export default async function LandingPage() {
  const t = await getTranslations("landing");
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-8 p-8 text-center">
      <div className="flex flex-col items-center gap-3">
        <div className="rounded-full bg-blue-600 px-5 py-2 text-2xl font-bold">SM</div>
        <h1 className="text-3xl font-bold">{t("title")}</h1>
        <p className="max-w-md text-gray-400">{t("subtitle")}</p>
      </div>
      <Link
        href="/login"
        className="rounded-md bg-blue-600 px-6 py-3 font-semibold text-white transition hover:bg-blue-500"
      >
        {t("cta")}
      </Link>
    </main>
  );
}
