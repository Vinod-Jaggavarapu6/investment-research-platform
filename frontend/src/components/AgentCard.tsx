import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import type { AgentState, NodeStatus, NodeName } from "../types";
import { NODE_LOADING_MESSAGES } from "../types";

interface Props {
  name: NodeName;
  label: string;
  state: AgentState;
}

const STATUS_ICON: Record<NodeStatus, string> = {
  queued: "○",
  running: "◉",
  done: "●",
  error: "✕",
};

const STATUS_COLOR: Record<NodeStatus, string> = {
  queued: "#9ca3af",
  running: "#f59e0b",
  done: "#10b981",
  error: "#ef4444",
};

export function AgentCard({ name, label, state }: Props) {
  const { status, data, tokens } = state;
  const color = STATUS_COLOR[status];

  // Cycle through loading messages while running
  const [msgIndex, setMsgIndex] = useState(0);
  const messages = NODE_LOADING_MESSAGES[name];

  useEffect(() => {
    if (status !== "running") {
      setMsgIndex(0);
      return;
    }
    // Only cycle if there's more than one message and no tokens yet
    if (messages.length <= 1 || tokens) return;

    const interval = setInterval(() => {
      setMsgIndex((i) => (i + 1) % messages.length);
    }, 1800);

    return () => clearInterval(interval);
  }, [status, messages.length, tokens]);

  const loadingMessage =
    status === "running" && !tokens ? messages[msgIndex] : null;

  return (
    <div
      style={{
        ...styles.card,
        borderColor: status === "running" ? "#f59e0b" : "#e5e7eb",
        transition: "border-color 0.3s ease",
      }}
    >
      {/* Header */}
      <div style={styles.header}>
        <span style={{ ...styles.icon, color }}>
          {status === "running" ? <SpinnerDot /> : STATUS_ICON[status]}
        </span>
        <span style={styles.label}>{label}</span>
        <span style={{ ...styles.badge, color }}>{status}</span>
      </div>

      {/* Animated loading message — shown while running, before tokens arrive */}
      {loadingMessage && (
        <div style={styles.loadingMsg}>
          <span style={styles.loadingText}>{loadingMessage}</span>
        </div>
      )}

      {/* Live token stream — plain text for agents, markdown for synthesizer */}
      {tokens && name !== "synthesizer" && (
        <div style={styles.tokenArea}>
          <span style={styles.tokenText}>{tokens}</span>
          {status === "running" && <span style={styles.cursor}>▋</span>}
        </div>
      )}

      {tokens && name === "synthesizer" && (
        <div style={styles.markdownArea}>
          <Markdown>{tokens}</Markdown>
          {status === "running" && <span style={styles.cursor}>▋</span>}
        </div>
      )}

      {/* Structured completion data — shown after done, no tokens */}
      {status === "done" && !tokens && data && (
        <div style={styles.data}>
          {Object.entries(data)
            .filter(
              ([, v]) => v !== null && v !== undefined && v !== "complete",
            )
            .map(([k, v]) => (
              <span key={k} style={styles.pill}>
                {k}: <strong>{String(v)}</strong>
              </span>
            ))}
        </div>
      )}
    </div>
  );
}

// Simple animated dot spinner using CSS animation
function SpinnerDot() {
  return <span style={styles.spinner}>◉</span>;
}

const styles: Record<string, React.CSSProperties> = {
  card: {
    padding: "14px 16px",
    border: "1px solid #e5e7eb",
    borderRadius: "8px",
    background: "#fff",
    display: "flex",
    flexDirection: "column",
    gap: "8px",
  },
  header: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
  },
  icon: {
    fontSize: "16px",
    lineHeight: "1",
  },
  spinner: {
    display: "inline-block",
    animation: "spin 1.2s linear infinite",
    fontSize: "16px",
    color: "#f59e0b",
  },
  label: {
    fontWeight: "600",
    fontSize: "14px",
    flex: 1,
  },
  badge: {
    fontSize: "11px",
    fontWeight: "500",
    textTransform: "uppercase",
    letterSpacing: "0.05em",
  },
  loadingMsg: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    padding: "6px 0 2px",
  },
  loadingText: {
    fontSize: "13px",
    color: "#6b7280",
    fontStyle: "italic",
    animation: "fadeIn 0.4s ease",
  },
  tokenArea: {
    fontSize: "13px",
    lineHeight: "1.7",
    color: "#374151",
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    padding: "4px 0",
  },
  tokenText: {
    fontFamily: "inherit",
  },
  cursor: {
    display: "inline-block",
    animation: "blink 1s step-end infinite",
    color: "#6b7280",
    marginLeft: "1px",
    verticalAlign: "text-bottom",
  },
  markdownArea: {
    fontSize: "14px",
    lineHeight: "1.7",
    color: "#374151",
    paddingTop: "4px",
  },
  data: {
    display: "flex",
    flexWrap: "wrap",
    gap: "6px",
  },
  pill: {
    fontSize: "12px",
    padding: "2px 8px",
    background: "#f3f4f6",
    borderRadius: "4px",
    color: "#6b7280",
  },
};
