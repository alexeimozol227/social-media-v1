"use client";

import { cn } from "@/lib/cn";
import { type InputHTMLAttributes, type ReactNode, forwardRef, useId, useState } from "react";

export function Label({
  htmlFor,
  children,
  required,
}: {
  htmlFor: string;
  children: ReactNode;
  required?: boolean;
}) {
  return (
    <label htmlFor={htmlFor} className="text-sm font-medium text-foreground">
      {children}
      {required && (
        <span className="ml-0.5 text-destructive" aria-hidden="true">
          *
        </span>
      )}
    </label>
  );
}

type InputProps = InputHTMLAttributes<HTMLInputElement> & {
  invalid?: boolean;
};

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { invalid, className, ...rest },
  ref,
) {
  return (
    <input
      ref={ref}
      aria-invalid={invalid || undefined}
      className={cn(
        "h-11 w-full rounded-lg border bg-input px-3.5 text-sm text-foreground",
        "placeholder:text-muted-foreground/60",
        "transition-[border-color,box-shadow] duration-150 outline-none",
        "focus:border-ring focus:ring-2 focus:ring-ring/25",
        "disabled:cursor-not-allowed disabled:opacity-50",
        invalid && "border-destructive focus:border-destructive focus:ring-destructive/25",
        className,
      )}
      {...rest}
    />
  );
});

function FieldError({ id, children }: { id: string; children: ReactNode }) {
  return (
    <p id={id} role="alert" className="text-sm text-destructive">
      {children}
    </p>
  );
}

function FieldHint({ id, children }: { id: string; children: ReactNode }) {
  return (
    <p id={id} className="text-xs text-muted-foreground">
      {children}
    </p>
  );
}

type TextFieldProps = Omit<InputProps, "id"> & {
  label: string;
  hint?: string;
  error?: string | null;
};

/** Label + input + hint/error with full aria wiring (aria-invalid,
 * aria-describedby, role="alert"). Covers the common text/email case. */
export function TextField({
  label,
  hint,
  error,
  required,
  className,
  ...inputProps
}: TextFieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errId = `${id}-err`;
  const describedBy =
    [error ? errId : null, hint ? hintId : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <Label htmlFor={id} required={required}>
        {label}
      </Label>
      <Input
        id={id}
        required={required}
        invalid={Boolean(error)}
        aria-describedby={describedBy}
        {...inputProps}
      />
      {hint && !error && <FieldHint id={hintId}>{hint}</FieldHint>}
      {error && <FieldError id={errId}>{error}</FieldError>}
    </div>
  );
}

type PasswordFieldProps = TextFieldProps & {
  showLabel: string;
  hideLabel: string;
};

/** Password input with a show/hide toggle (Forms best practice:
 * password-toggle). Toggle is a real button with aria-label +
 * aria-pressed and a ≥44px hit area. */
export function PasswordField({
  label,
  hint,
  error,
  required,
  className,
  showLabel,
  hideLabel,
  ...inputProps
}: PasswordFieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errId = `${id}-err`;
  const [visible, setVisible] = useState(false);
  const describedBy =
    [error ? errId : null, hint ? hintId : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <Label htmlFor={id} required={required}>
        {label}
      </Label>
      <div className="relative">
        <Input
          id={id}
          type={visible ? "text" : "password"}
          required={required}
          invalid={Boolean(error)}
          aria-describedby={describedBy}
          className="pr-12"
          {...inputProps}
        />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          aria-label={visible ? hideLabel : showLabel}
          aria-pressed={visible}
          className="absolute inset-y-0 right-0 grid w-11 place-items-center rounded-r-lg text-muted-foreground transition-colors hover:text-foreground"
        >
          <svg className="size-5" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            {visible ? (
              <>
                <path
                  d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="2" />
              </>
            ) : (
              <>
                <path
                  d="M3 3l18 18M10.6 6.2A9.8 9.8 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 3.9M6.2 7.2A17 17 0 0 0 2 12s3.5 7 10 7a9.8 9.8 0 0 0 4.1-.9M9.9 9.9a3 3 0 0 0 4.2 4.2"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </>
            )}
          </svg>
        </button>
      </div>
      {hint && !error && <FieldHint id={hintId}>{hint}</FieldHint>}
      {error && <FieldError id={errId}>{error}</FieldError>}
    </div>
  );
}

/** OTP / verification code input — large, centred, tabular digits.
 * Filtering is left to the caller (keeps numeric-only vs alnum
 * recovery codes flexible). */
export const CodeInput = forwardRef<HTMLInputElement, InputProps>(function CodeInput(
  { className, ...rest },
  ref,
) {
  return (
    <Input
      ref={ref}
      className={cn("h-14 text-center font-mono text-2xl tracking-[0.4em]", className)}
      {...rest}
    />
  );
});

export function Checkbox({
  id: idProp,
  label,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { label: ReactNode }) {
  const generated = useId();
  const id = idProp ?? generated;
  return (
    <label
      htmlFor={id}
      className="flex cursor-pointer items-start gap-2.5 text-sm text-muted-foreground"
    >
      <input
        id={id}
        type="checkbox"
        className="mt-0.5 size-4 shrink-0 accent-[var(--color-primary)]"
        {...rest}
      />
      <span>{label}</span>
    </label>
  );
}
