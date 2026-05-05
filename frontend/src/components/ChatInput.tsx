import { useState, type FormEvent, type KeyboardEvent } from "react";
import { colors } from "../theme";

interface Props {
  onSubmit: (question: string) => void;
  disabled: boolean;
  placeholder?: string;
}

export function ChatInput({
  onSubmit,
  disabled,
  placeholder = "Ask a follow-up question…",
}: Props) {
  const [value, setValue] = useState("");

  function submit() {
    const q = value.trim();
    if (q && !disabled) {
      onSubmit(q);
      setValue("");
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    submit();
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <form onSubmit={handleSubmit} style={styles.form}>
      <div
        style={{
          ...styles.inputRow,
          opacity: disabled ? 0.7 : 1,
        }}
      >
        <input
          style={styles.input}
          placeholder={disabled ? "Researching…" : placeholder}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          autoComplete="off"
        />
        <button
          style={{
            ...styles.sendBtn,
            opacity: disabled || !value.trim() ? 0.35 : 1,
            cursor: disabled || !value.trim() ? "default" : "pointer",
          }}
          type="submit"
          disabled={disabled || !value.trim()}
          aria-label="Send"
        >
          ↑
        </button>
      </div>
    </form>
  );
}

const styles: Record<string, React.CSSProperties> = {
  form: {
    width: "100%",
  },
  inputRow: {
    display: "flex",
    alignItems: "center",
    border: `1.5px solid ${colors.borderMuted}`,
    borderRadius: "12px",
    background: colors.white,
    padding: "6px 6px 6px 16px",
    gap: "8px",
    transition: "border-color 0.15s",
    boxShadow: "0 1px 4px rgba(0,0,0,0.06)",
  },
  input: {
    flex: 1,
    border: "none",
    outline: "none",
    fontSize: "15px",
    background: "transparent",
    fontFamily: "inherit",
    color: colors.textPrimary,
    lineHeight: "1.5",
  },
  sendBtn: {
    width: "34px",
    height: "34px",
    borderRadius: "8px",
    background: colors.textPrimary,
    color: colors.white,
    border: "none",
    fontSize: "16px",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
    transition: "opacity 0.15s",
    fontFamily: "inherit",
  },
};
