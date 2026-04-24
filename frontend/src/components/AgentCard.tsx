import type { AgentState, NodeStatus } from "../types";

interface Props {
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

export function AgentCard({ label, state }: Props) {
  const { status, data } = state;
  const color = STATUS_COLOR[status];

  return (
    <div style={styles.card}>
      {/* Header row */}
      <div style={styles.header}>
        <span style={{ ...styles.icon, color }}>{STATUS_ICON[status]}</span>
        <span style={styles.label}>{label}</span>
        <span style={{ ...styles.badge, color }}>{status}</span>
      </div>

      {/* Structured data from node_complete */}
      {data && (
        <div style={styles.data}>
          {Object.entries(data)
            .filter(([, v]) => v !== null && v !== undefined)
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
  tokens: {
    fontSize: "13px",
    lineHeight: "1.6",
    color: "#374151",
    whiteSpace: "pre-wrap",
  },
  cursor: {
    animation: "blink 1s step-end infinite",
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
