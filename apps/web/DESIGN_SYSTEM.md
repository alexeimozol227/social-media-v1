# Design System — social-media-v1 web

> Source of truth for the production frontend. Generated from the
> `ui-ux-pro-max` skill (`.claude/skills/ui-ux-pro-max`). Read this
> before building or changing any UI; keep it in sync with
> `src/app/globals.css` and `src/components/ui/`.

## Product framing

- **Type**: SaaS productivity tool — AI operating system for managing
  social networks (channels, brands, scheduling, analytics).
- **Audience**: content creators, SMM managers, agencies. Professional
  but creative; expects a polished, fast, focused product.
- **Style**: modern minimal, **dark-first**, with subtle glass/aurora
  brand accents. No skeuomorphism, no emoji as icons.

## Tokens (defined in `globals.css` via Tailwind v4 `@theme`)

Always consume **semantic** utilities, never raw palette colours.

| Token | Utility | Use |
|---|---|---|
| `--color-background` | `bg-background` | App/page background |
| `--color-surface` / `--color-card` | `bg-surface` `bg-card` | Cards, panels |
| `--color-surface-2` | `bg-surface-2` | Raised/hover surface |
| `--color-input` | `bg-input` | Form controls |
| `--color-border` | `border-border` | Hairlines (default border) |
| `--color-foreground` | `text-foreground` | Primary text |
| `--color-muted-foreground` | `text-muted-foreground` | Secondary text |
| `--color-primary` (+`-hover`/`-active`) | `bg-primary` … | Primary CTA, links |
| `--color-primary-foreground` | `text-primary-foreground` | Text on primary |
| `--color-destructive` | `text-destructive` … | Errors, danger |
| `--color-success` / `--color-info` | `*-success` `*-info` | Status |
| `--color-ring` | focus ring | Keyboard focus (2px, offset 2px) |
| `--radius*` | `rounded-lg/xl/2xl` | 12/16/20px scale |

Light theme is intentionally deferred — tokens are structured so it is
a future override block, not a rewrite. Do not hardcode hex in
components.

## Primitives (`src/components/ui/`)

- `Button` — variants `primary | secondary | ghost`, sizes `md | sm`,
  `loading` (spinner + `aria-busy`), `fullWidth`. Min height 44px.
- `field.tsx` — `Label`, `Input`, `TextField`, `PasswordField`
  (show/hide toggle, ≥44px hit area), `CodeInput` (OTP), `Checkbox`.
  `TextField`/`PasswordField` wire `aria-invalid`,
  `aria-describedby`, `role="alert"` errors automatically.
- `Alert` — `error` (assertive `role="alert"`) / `success` / `info`.
- `Spinner`, `Logo` / `LogoMark`, `AuthHeading`.

## Layout patterns

- **Auth** (`src/app/(auth)/layout.tsx`): split screen — branded
  aurora panel (desktop ≥1024px only) + centred form column
  (`max-w-[400px]`). Mobile shows a compact logo, form only
  (content-priority). `min-h-dvh`, safe paddings.

## Non-negotiable rules (from skill Quick Reference)

1. Accessibility: 4.5:1 text contrast, visible focus rings, labelled
   inputs, icon buttons get `aria-label`, single `<h1>` per screen.
2. Touch: targets ≥44px, password/icon toggles included.
3. Forms: visible labels (not placeholder-only), error below field +
   `role="alert"`, loading state on submit, helper text for
   constraints (e.g. password length), semantic input types +
   `autocomplete`, password show/hide.
4. Motion: 150–300ms, transform/opacity only, respect
   `prefers-reduced-motion` (handled globally in `globals.css`).
5. Icons: inline SVG only (no emoji, no icon-font dependency added).

## Status

- ✅ Auth flow (login, register, forgot/reset password, verify email,
  MFA) rebuilt on the system.
- ⬜ Landing, dashboard, settings, channels/brands — still on the
  legacy test UI; migrate incrementally to these primitives.
