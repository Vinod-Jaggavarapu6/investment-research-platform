import Markdown from "react-markdown";

interface Props {
  report: string | null;
  errorMsg: string | null;
  streamingTokens?: string;
}

export function FinalReport({ report, errorMsg, streamingTokens }: Props) {
  if (errorMsg) {
    return (
      <div
        style={{ ...styles.box, borderColor: "#fca5a5", background: "#fef2f2" }}
      >
        <p style={{ color: "#dc2626", margin: 0 }}>Error: {errorMsg}</p>
      </div>
    );
  }

  const content = report ?? streamingTokens;
  if (!content) return null;

  const isStreaming = !report && !!streamingTokens;

  return (
    <div style={styles.box}>
      <h2 style={styles.heading}>Research Report</h2>
      <div style={styles.markdown}>
        <Markdown>{content}</Markdown>
        {isStreaming && <span style={styles.cursor}>▋</span>}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  box: {
    padding: "24px",
    border: "1px solid #e5e7eb",
    borderRadius: "8px",
    background: "#fff",
  },
  heading: {
    margin: "0 0 16px",
    fontSize: "16px",
    fontWeight: "600",
  },
  markdown: {
    fontSize: "14px",
    lineHeight: "1.7",
    color: "#374151",
  },
  cursor: {
    display: "inline-block",
    animation: "blink 1s step-end infinite",
    color: "#6b7280",
    verticalAlign: "text-bottom",
  },
};
