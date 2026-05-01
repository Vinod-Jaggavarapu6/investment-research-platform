import { useState } from "react";
import type { Conversation } from "../types";

interface Props {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}

function relativeTime(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export function ConversationSidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
}: Props) {
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  return (
    <aside style={styles.sidebar}>
      <div style={styles.header}>
        <span style={styles.headerLabel}>Research History</span>
        <button style={styles.newBtn} onClick={onNew} title="New research">
          + New
        </button>
      </div>

      <div style={styles.list}>
        {conversations.length === 0 && (
          <p style={styles.empty}>No conversations yet</p>
        )}
        {conversations.map((conv) => {
          const isActive = conv.id === activeId;
          return (
            <div
              key={conv.id}
              style={{
                ...styles.item,
                background: isActive ? "#f0f4ff" : hoveredId === conv.id ? "#f9fafb" : "transparent",
                borderLeft: isActive ? "3px solid #6366f1" : "3px solid transparent",
              }}
              onClick={() => onSelect(conv.id)}
              onMouseEnter={() => setHoveredId(conv.id)}
              onMouseLeave={() => setHoveredId(null)}
            >
              <div style={styles.itemBody}>
                <span style={styles.title} title={conv.title}>
                  {conv.title}
                </span>
                <div style={styles.meta}>
                  {conv.ticker && (
                    <span style={styles.ticker}>{conv.ticker}</span>
                  )}
                  <span style={styles.time}>{relativeTime(conv.updated_at)}</span>
                </div>
              </div>
              {hoveredId === conv.id && (
                <button
                  style={styles.deleteBtn}
                  title="Delete"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDelete(conv.id);
                  }}
                >
                  ✕
                </button>
              )}
            </div>
          );
        })}
      </div>
    </aside>
  );
}

const styles: Record<string, React.CSSProperties> = {
  sidebar: {
    width: "260px",
    flexShrink: 0,
    borderRight: "1px solid #e5e7eb",
    background: "#fff",
    display: "flex",
    flexDirection: "column",
    overflowY: "auto",
  },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "16px 14px 12px",
    borderBottom: "1px solid #f3f4f6",
    position: "sticky",
    top: 0,
    background: "#fff",
    zIndex: 1,
  },
  headerLabel: {
    fontSize: "12px",
    fontWeight: "600",
    color: "#6b7280",
    textTransform: "uppercase",
    letterSpacing: "0.05em",
  },
  newBtn: {
    fontSize: "12px",
    padding: "4px 10px",
    border: "1px solid #d1d5db",
    borderRadius: "5px",
    background: "#fff",
    cursor: "pointer",
    color: "#374151",
    fontFamily: "inherit",
  },
  list: {
    flex: 1,
    padding: "8px 0",
  },
  empty: {
    fontSize: "13px",
    color: "#9ca3af",
    textAlign: "center",
    marginTop: "24px",
  },
  item: {
    display: "flex",
    alignItems: "center",
    padding: "10px 14px",
    cursor: "pointer",
    transition: "background 0.1s",
    gap: "8px",
  },
  itemBody: {
    flex: 1,
    minWidth: 0,
  },
  title: {
    display: "block",
    fontSize: "13px",
    fontWeight: "500",
    color: "#111827",
    whiteSpace: "nowrap",
    overflow: "hidden",
    textOverflow: "ellipsis",
  },
  meta: {
    display: "flex",
    alignItems: "center",
    gap: "6px",
    marginTop: "3px",
  },
  ticker: {
    fontSize: "11px",
    fontWeight: "600",
    color: "#6366f1",
    background: "#eef2ff",
    padding: "1px 5px",
    borderRadius: "3px",
  },
  time: {
    fontSize: "11px",
    color: "#9ca3af",
  },
  deleteBtn: {
    background: "none",
    border: "none",
    cursor: "pointer",
    color: "#9ca3af",
    fontSize: "12px",
    padding: "2px 4px",
    borderRadius: "3px",
    flexShrink: 0,
  },
};
