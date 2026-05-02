import { useState } from "react";
import type { Conversation } from "../types";
import { colors } from "../theme";

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
                background: isActive
                  ? colors.brandActiveBg
                  : hoveredId === conv.id
                  ? colors.bgPage
                  : "transparent",
                borderLeft: `3px solid ${isActive ? colors.brand : "transparent"}`,
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
    borderRight: `1px solid ${colors.border}`,
    background: colors.white,
    display: "flex",
    flexDirection: "column",
    overflowY: "auto",
  },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "16px 14px 12px",
    borderBottom: `1px solid ${colors.bgLight}`,
    position: "sticky",
    top: 0,
    background: colors.white,
    zIndex: 1,
  },
  headerLabel: {
    fontSize: "12px",
    fontWeight: "600",
    color: colors.textMuted,
    textTransform: "uppercase",
    letterSpacing: "0.05em",
  },
  newBtn: {
    fontSize: "12px",
    padding: "4px 10px",
    border: `1px solid ${colors.borderMuted}`,
    borderRadius: "5px",
    background: colors.white,
    cursor: "pointer",
    color: colors.textSecondary,
    fontFamily: "inherit",
  },
  list: {
    flex: 1,
    padding: "8px 0",
  },
  empty: {
    fontSize: "13px",
    color: colors.textFaint,
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
    color: colors.textPrimary,
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
    color: colors.brand,
    background: colors.brandBg,
    padding: "1px 5px",
    borderRadius: "3px",
  },
  time: {
    fontSize: "11px",
    color: colors.textFaint,
  },
  deleteBtn: {
    background: "none",
    border: "none",
    cursor: "pointer",
    color: colors.textFaint,
    fontSize: "12px",
    padding: "2px 4px",
    borderRadius: "3px",
    flexShrink: 0,
  },
};
