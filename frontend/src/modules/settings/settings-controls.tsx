import type { ChangeEvent, ReactNode } from "react";

export const SECTION_STYLE = { marginBottom: 20 } as const;
export const HEADING_STYLE = { fontSize: 15, margin: "0 0 8px" } as const;
export const HINT_STYLE = { fontSize: 12, color: "#777", margin: "2px 0 0" } as const;
export const ROW_STYLE = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  padding: "6px 0",
  fontSize: 14,
} as const;
export const BUTTON_STYLE = { padding: "4px 12px" } as const;
export const INPUT_STYLE = { padding: "3px 6px", width: 96 } as const;

export function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <section style={SECTION_STYLE}>
      <h2 style={HEADING_STYLE}>{title}</h2>
      {hint && <p style={HINT_STYLE}>{hint}</p>}
      <div>{children}</div>
    </section>
  );
}

interface ToggleRowProps {
  label: string;
  hint?: string;
  checked: boolean;
  disabled: boolean;
  onChange: (next: boolean) => void;
}

export function ToggleRow({ label, hint, checked, disabled, onChange }: ToggleRowProps) {
  return (
    <label style={ROW_STYLE}>
      <span>
        {label}
        {hint && <small style={{ color: "#777", marginLeft: 8 }}>{hint}</small>}
      </span>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event: ChangeEvent<HTMLInputElement>) => onChange(event.target.checked)}
      />
    </label>
  );
}

interface FieldRowProps {
  label: string;
  hint?: string;
  value: string;
  disabled: boolean;
  error?: string | null;
  inputMode?: "text" | "numeric";
  onChange: (next: string) => void;
}

function FieldRow({
  label,
  hint,
  value,
  disabled,
  error,
  inputMode = "text",
  onChange,
}: FieldRowProps) {
  return (
    <label style={ROW_STYLE}>
      <span>
        {label}
        {hint && <small style={{ color: "#777", marginLeft: 8 }}>{hint}</small>}
        {error && (
          <small role="alert" style={{ color: "#991b1b", marginLeft: 8 }}>
            {error}
          </small>
        )}
      </span>
      <input
        style={INPUT_STYLE}
        value={value}
        disabled={disabled}
        inputMode={inputMode}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

export function NumberRow(props: FieldRowProps) {
  return <FieldRow {...props} inputMode="numeric" />;
}

export function TextRow(props: FieldRowProps) {
  return <FieldRow {...props} inputMode="text" />;
}

export function StatusLine({ error, notice }: { error: string | null; notice: string | null }) {
  return (
    <div aria-live="polite" style={{ minHeight: 20, fontSize: 13 }}>
      {error && (
        <p role="alert" style={{ color: "#991b1b", margin: 0 }}>
          {error}
        </p>
      )}
      {!error && notice && <p style={{ color: "#166534", margin: 0 }}>{notice}</p>}
    </div>
  );
}

export function SaveButton({
  disabled,
  onSave,
  label = "保存",
}: {
  disabled: boolean;
  onSave: () => void;
  label?: string;
}) {
  return (
    <button type="button" style={BUTTON_STYLE} disabled={disabled} onClick={onSave}>
      {label}
    </button>
  );
}
