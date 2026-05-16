/** Active-brand Zustand store (PR #14).
 *
 * Tracks which brand the user is currently acting on. The store is
 * the single source of truth for the ``X-Active-Brand-Id`` header
 * the API client injects on every authenticated request (see
 * ``lib/api.ts``). Hydration order:
 *
 * 1. on app boot the brand switcher fetches ``/v1/users/me/brands``
 *    and calls ``setBrand`` with the default brand (``is_default``).
 * 2. when the user picks another brand from the dropdown the store
 *    is updated synchronously so subsequent fetches carry the new
 *    header.
 * 3. the active brand id is mirrored to ``localStorage`` so it
 *    survives reloads.
 */

import { create } from "zustand";

const STORAGE_KEY = "sm.activeBrandId";

export interface BrandSummary {
  id: string;
  workspace_id: string;
  name: string;
  is_default: boolean;
  content_language: string;
  timezone: string;
}

interface ActiveBrandState {
  activeBrandId: string | null;
  brands: BrandSummary[];
  setBrand: (id: string | null) => void;
  setBrands: (brands: BrandSummary[]) => void;
  /** Replace both at once — typically called once after the
   * initial ``/v1/users/me/brands`` fetch returns. */
  hydrate: (brands: BrandSummary[]) => void;
}

function readStoredBrandId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredBrandId(id: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (id === null) {
      window.localStorage.removeItem(STORAGE_KEY);
    } else {
      window.localStorage.setItem(STORAGE_KEY, id);
    }
  } catch {
    // ignore - storage may be disabled
  }
}

export const useActiveBrandStore = create<ActiveBrandState>((set) => ({
  activeBrandId: readStoredBrandId(),
  brands: [],
  setBrand: (id) => {
    writeStoredBrandId(id);
    set({ activeBrandId: id });
  },
  setBrands: (brands) => set({ brands }),
  hydrate: (brands) => {
    const stored = readStoredBrandId();
    const found = brands.find((b) => b.id === stored);
    const fallback = brands.find((b) => b.is_default) ?? brands[0] ?? null;
    const next = found ? found.id : (fallback?.id ?? null);
    writeStoredBrandId(next);
    set({ brands, activeBrandId: next });
  },
}));

/** Synchronous getter used by ``apiFetch`` — Zustand stores are
 * usable outside of React components. */
export function getActiveBrandId(): string | null {
  return useActiveBrandStore.getState().activeBrandId;
}
