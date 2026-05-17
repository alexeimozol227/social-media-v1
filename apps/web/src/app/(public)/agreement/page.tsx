import { LegalDocument } from "@/components/legal/legal-document";
import { getLocale } from "next-intl/server";

// Dynamic so edits to content/legal/*.json (or a future admin/DB
// source) are picked up without a rebuild.
export const dynamic = "force-dynamic";

export default async function AgreementPage() {
  const locale = await getLocale();
  return <LegalDocument slug="agreement" locale={locale} />;
}
