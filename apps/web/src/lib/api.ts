/** Backend API client.
 *
 * The browser sets the access / refresh / csrf cookies during the
 * sign-in / refresh flows. We always include credentials so the
 * cookies travel back; on a 401 we let the page-level redirect take
 * over (App Router's middleware will handle this in a follow-up PR).
 *
 * Every request also carries an ``Accept-Language`` header whose
 * value is the locale next-intl is currently rendering — i.e. the
 * user's in-product language toggle wins over the browser default,
 * so a Spanish-locale browser whose user clicked "English" gets
 * English emails from the backend.
 */

import { getActiveBrandId } from "@/lib/active-brand-store";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

const SUPPORTED_LOCALES = new Set(["ru", "en"]);
const DEFAULT_LOCALE = "ru";
const LOCALE_COOKIE = "NEXT_LOCALE";
const ACTIVE_BRAND_HEADER = "X-Active-Brand-Id";

/** Read the active UI locale on the client side.
 *
 * Source of truth: the ``NEXT_LOCALE`` cookie that next-intl sets
 * whenever the user picks a language. Falls back to the value
 * exposed by ``<html lang>`` (rendered by next-intl on the server)
 * and finally to the project default ``ru``.
 *
 * Returns ``null`` on the server — server components that need to
 * forward a locale should set ``Accept-Language`` explicitly.
 */
function readClientLocale(): string | null {
  if (typeof document === "undefined") {
    return null;
  }
  const match = document.cookie.match(/(?:^|;\s*)NEXT_LOCALE=([^;]+)/);
  const raw = match?.[1];
  if (raw) {
    const candidate = decodeURIComponent(raw);
    if (SUPPORTED_LOCALES.has(candidate)) {
      return candidate;
    }
  }
  const htmlLang = document.documentElement.lang;
  if (htmlLang && SUPPORTED_LOCALES.has(htmlLang)) {
    return htmlLang;
  }
  return DEFAULT_LOCALE;
}

// Re-export for completeness and so tests can pin against the
// canonical cookie name without touching the implementation.
export { LOCALE_COOKIE };

export class ApiError extends Error {
  errorCode: string;
  status: number;
  details?: Record<string, unknown>;
  retryAfter?: number;

  constructor(opts: {
    errorCode: string;
    message: string;
    status: number;
    details?: Record<string, unknown>;
    retryAfter?: number;
  }) {
    super(opts.message);
    this.errorCode = opts.errorCode;
    this.status = opts.status;
    this.details = opts.details;
    this.retryAfter = opts.retryAfter;
  }
}

type RequestInitOpts = Omit<RequestInit, "body"> & {
  json?: unknown;
};

export async function apiFetch<T>(path: string, opts: RequestInitOpts = {}): Promise<T> {
  const { json, headers, ...rest } = opts;
  const locale = readClientLocale();
  const merged: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
  };
  if (locale) {
    // Force the backend to render emails / system copy in the
    // UI-selected locale, NOT the browser default — see the file
    // header for the rationale.
    merged["Accept-Language"] = locale;
  }
  const activeBrandId = getActiveBrandId();
  if (activeBrandId) {
    merged[ACTIVE_BRAND_HEADER] = activeBrandId;
  }
  if (headers) {
    for (const [k, v] of Object.entries(headers as Record<string, string | undefined>)) {
      if (v !== undefined) merged[k] = v;
    }
  }
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...rest,
    credentials: "include",
    headers: merged,
    body: json !== undefined ? JSON.stringify(json) : undefined,
  });
  if (!res.ok) {
    let body: {
      error_code?: string;
      message?: string;
      details?: Record<string, unknown>;
      retry_after_seconds?: number;
    } = {};
    try {
      body = (await res.json()) as typeof body;
    } catch {
      // ignore - body wasn't JSON
    }
    throw new ApiError({
      errorCode: body.error_code ?? "UNKNOWN",
      message: body.message ?? `HTTP ${res.status}`,
      status: res.status,
      details: body.details,
      retryAfter: body.retry_after_seconds,
    });
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

export interface AccessTokenResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
}

export interface UserPublic {
  id: string;
  email: string;
  full_name: string | null;
  avatar_url: string | null;
  locale: string;
  timezone: string;
  preferred_currency: string;
  status: "active" | "blocked" | "deleted";
  platform_role: "user" | "support" | "moderator" | "admin";
  email_verified_at: string | null;
  created_at: string;
}

export interface WorkspaceSummary {
  id: string;
  name: string;
  slug: string;
  type: "solo" | "agency" | "network";
  preferred_currency: string;
}

export interface MeResponse {
  user: UserPublic;
  active_workspace: WorkspaceSummary | null;
}

// ---- MFA (PR #4) ----

export interface LoginMFARequiredResponse {
  mfa_required: true;
  mfa_token: string;
  expires_in: number;
}

export function isMFARequiredResponse(
  body: AccessTokenResponse | LoginMFARequiredResponse,
): body is LoginMFARequiredResponse {
  return (body as LoginMFARequiredResponse).mfa_required === true;
}

export interface MFAStatusResponse {
  enabled: boolean;
  enrolled_at: string | null;
  recovery_codes_remaining: number;
}

export interface MFAEnrollStartResponse {
  secret: string;
  provisioning_uri: string;
}

export interface MFAEnrollConfirmResponse {
  recovery_codes: string[];
}

export interface MFARecoveryRegenerateResponse {
  recovery_codes: string[];
}

// ---- Channels (PR #14) ----

export interface ChannelView {
  id: string;
  channel_id: string;
  platform: string;
  external_id: number;
  username: string | null;
  title: string | null;
  role: string;
  bot_admin_rights: Record<string, unknown>;
  connected_at: string;
  disconnected_at: string | null;
}

export interface ChannelListResponse {
  items: ChannelView[];
  total: number;
}

export interface ConnectChannelRequest {
  platform?: "telegram";
  identifier: string | number;
}
