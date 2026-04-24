import { useState, type FormEvent } from "react";

interface Props {
  onSubmit: (question: string) => void;
  disabled: boolean;
}

export function SearchBar({ onSubmit, disabled }: Props) {
  const [question, setQuestion] = useState("");

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (q) onSubmit(q);
  }

  return (
    <form onSubmit={handleSubmit} style={styles.form}>
      <input
        style={{ ...styles.input, flex: 1 }}
        placeholder="Enter a Research question like  What is Apple's revenue trend?"
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        disabled={disabled}
      />
      <button
        style={styles.button}
        type="submit"
        disabled={disabled || !question}
      >
        {disabled ? "Streaming…" : "Research"}
      </button>
    </form>
  );
}

const styles = {
  form: {
    display: "flex",
    gap: "10px",
    alignItems: "center",
  } as React.CSSProperties,
  input: {
    padding: "12px 16px",
    fontSize: "16px",
    border: "1px solid #d1d5db",
    borderRadius: "6px",
    outline: "none",
    fontFamily: "inherit",
  } as React.CSSProperties,
  button: {
    padding: "12px 16px",
    fontSize: "16px",
    background: "#111",
    color: "#fff",
    border: "none",
    borderRadius: "6px",
    cursor: "pointer",
    fontFamily: "inherit",
    opacity: 1,
  } as React.CSSProperties,
};
