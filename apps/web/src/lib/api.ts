/** Backend API client.
 *
 * The browser sets the access / refresh / csrf cookies during the
 * sign-in / refresh flows. We always include credentials so the
 * cookies travel back; on a 401 we let the page-level redirect take
 * over (App Router's middleware will handle this in a follow-up PR).
 */

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

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
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...rest,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...(headers ?? {}),
    },
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
