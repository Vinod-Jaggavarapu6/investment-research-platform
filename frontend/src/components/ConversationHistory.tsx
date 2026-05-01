import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message } from "../types";

interface Props {
  messages: Message[];
}

export function ConversationHistory({ messages }: Props) {
  if (messages.length === 0) return null;

  // Group into user/assistant pairs
  const pairs: { question: string; answer: string; key: string }[] = [];
  for (let i = 0; i < messages.length - 1; i += 2) {
    const user = messages[i];
    const assistant = messages[i + 1];
    if (user?.role === "user" && assistant?.role === "assistant") {
      pairs.push({ question: user.content, answer: assistant.content, key: user.id });
    }
  }

  if (pairs.length === 0) return null;

  return (
    <div style={styles.container}>
      {pairs.map((pair) => (
        <div key={pair.key} style={styles.exchange}>
          <div style={styles.question}>
            <span style={styles.questionLabel}>You</span>
            <p style={styles.questionText}>{pair.question}</p>
          </div>
          <div style={styles.answer}>
            <div style={styles.answerLabel}>Research</div>
            <div style={styles.markdown}>
              <Markdown remarkPlugins={[remarkGfm]}>{pair.answer}</Markdown>
            </div>
          </div>
        </div>
      ))}
      <div style={styles.divider}>
        <span style={styles.dividerLabel}>New question</span>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: "flex",
    flexDirection: "column",
    gap: "0",
  },
  exchange: {
    borderBottom: "1px solid #f3f4f6",
    paddingBottom: "24px",
    marginBottom: "24px",
  },
  question: {
    marginBottom: "12px",
  },
  questionLabel: {
    display: "inline-block",
    fontSize: "11px",
    fontWeight: "600",
    color: "#6b7280",
    textTransform: "uppercase",
    letterSpacing: "0.05em",
    marginBottom: "6px",
  },
  questionText: {
    margin: 0,
    fontSize: "15px",
    fontWeight: "500",
    color: "#111827",
    lineHeight: "1.5",
  },
  answer: {
    background: "#fff",
    border: "1px solid #e5e7eb",
    borderRadius: "8px",
    padding: "16px 20px",
  },
  answerLabel: {
    fontSize: "11px",
    fontWeight: "600",
    color: "#6366f1",
    textTransform: "uppercase",
    letterSpacing: "0.05em",
    marginBottom: "10px",
  },
  markdown: {
    fontSize: "14px",
    lineHeight: "1.7",
    color: "#374151",
  },
  divider: {
    display: "flex",
    alignItems: "center",
    gap: "10px",
    marginBottom: "8px",
  },
  dividerLabel: {
    fontSize: "12px",
    fontWeight: "500",
    color: "#9ca3af",
    whiteSpace: "nowrap",
  },
};
