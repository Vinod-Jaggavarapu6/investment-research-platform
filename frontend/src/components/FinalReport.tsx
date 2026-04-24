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

  // Show streaming tokens as they arrive, then swap to final report
  const content = report ?? streamingTokens;
  if (!content) return null;

  const isStreaming = !report && !!streamingTokens;

  return (
    <div
      style={{
        ...styles.box,
        borderColor: isStreaming ? "#a5b4fc" : "#e5e7eb",
        transition: "border-color 0.3s ease",
      }}
    >
      <div style={styles.headingRow}>
        <h2 style={styles.heading}>Research Report</h2>
        {isStreaming && <span style={styles.streamingBadge}>● Live</span>}
      </div>
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
  headingRow: {
    display: "flex",
    alignItems: "center",
    gap: "10px",
    marginBottom: "16px",
  },
  heading: {
    margin: 0,
    fontSize: "16px",
    fontWeight: "600",
  },
  streamingBadge: {
    fontSize: "11px",
    fontWeight: "500",
    color: "#6366f1",
    animation: "blink 1.5s ease infinite",
    letterSpacing: "0.05em",
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
