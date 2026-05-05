import { useEffect, useRef } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message, ResearchState } from "../types";
import { AgentTimeline } from "./AgentTimeline";
import { ChatInput } from "./ChatInput";
import { colors } from "../theme";

interface Props {
  messages: Message[];
  pendingQuestion: string | null;
  streamingState: ResearchState | null;
  onSubmit: (question: string) => void;
  isStreaming: boolean;
}

const EXAMPLE_PROMPTS = [
  "What is Apple's revenue trend over the last 3 years?",
  "Compare NVDA and AMD on profit margins and growth",
  "What risks did Microsoft disclose in their latest 10-K?",
  "What's the recent news sentiment around Tesla?",
];

export function ChatWindow({
  messages,
  pendingQuestion,
  streamingState,
  onSubmit,
  isStreaming,
}: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const isEmpty = messages.length === 0 && !pendingQuestion && !streamingState;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, pendingQuestion, streamingState?.phase]);

  return (
    <div style={styles.container}>
      {/* ── Scrollable message area ── */}
      <div style={styles.messages}>
        {isEmpty ? (
          <EmptyState onSubmit={onSubmit} />
        ) : (
          <div style={styles.feed}>
            {/* History from DB */}
            {messages.map((msg) =>
              msg.role === "user" ? (
                <UserMessage key={msg.id} text={msg.content} />
              ) : (
                <AssistantMessage key={msg.id} content={msg.content} />
              ),
            )}

            {/* Current in-flight exchange */}
            {pendingQuestion && <UserMessage text={pendingQuestion} live />}

            {/* AgentTimeline: show while streaming, on error, or while waiting for DB messages to load.
                pendingQuestion is cleared in the same batch as setHistoryMessages, so hiding
                on pendingQuestion===null guarantees history is already populated — no flash. */}
            {streamingState && (streamingState.phase === "streaming" || streamingState.phase === "error" || pendingQuestion !== null) && (
              <div style={styles.agentBlock}>
                <AgentTimeline research={streamingState} />
              </div>
            )}

            {/* Timing footer: rendered after history loads, persists until next query */}
            {streamingState?.phase === "done" && pendingQuestion === null &&
              streamingState.completedAt && streamingState.startedAt && (
              <div style={styles.completeLine}>
                <span style={{ color: colors.success, fontSize: "13px" }}>✓</span>
                <span style={styles.completeText}>
                  Research completed in {((streamingState.completedAt - streamingState.startedAt) / 1000).toFixed(1)}s
                </span>
              </div>
            )}

            <div ref={bottomRef} style={{ height: "1px" }} />
          </div>
        )}
      </div>

      {/* ── Sticky input bar (hidden during empty state — EmptyState has its own) ── */}
      {!isEmpty && (
        <div style={styles.inputBar}>
          <div style={styles.inputWrap}>
            <ChatInput onSubmit={onSubmit} disabled={isStreaming} />
          </div>
        </div>
      )}
    </div>
  );
}

// ── Empty / landing state ────────────────────────────────────────────────────

function EmptyState({ onSubmit }: { onSubmit: (q: string) => void }) {
  return (
    <div style={styles.empty}>
      <div style={styles.emptyInner}>
        <h2 style={styles.emptyTitle}>What would you like to research?</h2>
        <p style={styles.emptySub}>
          Ask about any stock, SEC filing, market data, or compare companies.
        </p>

        <div style={styles.emptyInput}>
          <ChatInput
            onSubmit={onSubmit}
            disabled={false}
            placeholder="Ask a research question…"
          />
        </div>

        <div style={styles.suggestions}>
          {EXAMPLE_PROMPTS.map((p) => (
            <button
              key={p}
              style={styles.suggestionChip}
              onClick={() => onSubmit(p)}
            >
              {p}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Message bubbles ──────────────────────────────────────────────────────────

function UserMessage({ text, live }: { text: string; live?: boolean }) {
  return (
    <div style={styles.userMsg}>
      <span style={styles.roleLabel}>You</span>
      <div
        style={{
          ...styles.userBubble,
          border: `1px solid ${live ? colors.brandBorder : colors.border}`,
          background: live ? colors.brandBgSoft : colors.bgPage,
        }}
      >
        {text}
      </div>
    </div>
  );
}

function AssistantMessage({ content }: { content: string }) {
  return (
    <div style={styles.assistantMsg}>
      <span style={{ ...styles.roleLabel, color: colors.brand }}>Research</span>
      <div style={styles.assistantBubble}>
        <Markdown
          remarkPlugins={[remarkGfm]}
          components={markdownComponents}
        >
          {content}
        </Markdown>
      </div>
    </div>
  );
}

// ── Markdown table renderers (same as AgentTimeline) ────────────────────────

const markdownComponents = {
  table: ({ children }: { children?: React.ReactNode }) => (
    <div style={{ overflowX: "auto", margin: "12px 0" }}>
      <table style={{ borderCollapse: "collapse", width: "100%", fontSize: "13px" }}>
        {children}
      </table>
    </div>
  ),
  thead: ({ children }: { children?: React.ReactNode }) => (
    <thead style={{ background: colors.bgLight }}>{children}</thead>
  ),
  th: ({ children }: { children?: React.ReactNode }) => (
    <th style={{ padding: "8px 12px", border: `1px solid ${colors.border}`, fontWeight: 600, textAlign: "left" }}>
      {children}
    </th>
  ),
  td: ({ children }: { children?: React.ReactNode }) => (
    <td style={{ padding: "7px 12px", border: `1px solid ${colors.border}`, verticalAlign: "top" }}>
      {children}
    </td>
  ),
  tr: ({ children }: { children?: React.ReactNode }) => (
    <tr style={{ borderBottom: `1px solid ${colors.border}` }}>{children}</tr>
  ),
};

// ── Styles ───────────────────────────────────────────────────────────────────

const styles: Record<string, React.CSSProperties> = {
  container: {
    height: "100%",
    display: "flex",
    flexDirection: "column",
    background: colors.bgPage,
  },
  messages: {
    flex: 1,
    overflowY: "auto",
  },
  feed: {
    maxWidth: "780px",
    margin: "0 auto",
    padding: "32px 24px 16px",
    display: "flex",
    flexDirection: "column",
    gap: "28px",
  },
  agentBlock: {
    background: colors.white,
    border: `1px solid ${colors.border}`,
    borderRadius: "12px",
    padding: "20px 24px",
  },
  completeLine: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    paddingTop: "4px",
  },
  completeText: {
    fontSize: "13px",
    color: colors.textMuted,
    fontWeight: 500,
  },
  inputBar: {
    flexShrink: 0,
    borderTop: `1px solid ${colors.border}`,
    background: colors.white,
    padding: "14px 24px 18px",
  },
  inputWrap: {
    maxWidth: "780px",
    margin: "0 auto",
  },

  // ── Empty state ──
  empty: {
    height: "100%",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    padding: "24px",
  },
  emptyInner: {
    width: "100%",
    maxWidth: "620px",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: "16px",
    textAlign: "center",
  },
  emptyTitle: {
    margin: 0,
    fontSize: "22px",
    fontWeight: "700",
    color: colors.textPrimary,
  },
  emptySub: {
    margin: 0,
    fontSize: "14px",
    color: colors.textMuted,
    lineHeight: 1.6,
  },
  emptyInput: {
    width: "100%",
    marginTop: "8px",
  },
  suggestions: {
    display: "flex",
    flexWrap: "wrap",
    gap: "8px",
    justifyContent: "center",
    marginTop: "4px",
  },
  suggestionChip: {
    fontSize: "13px",
    padding: "7px 13px",
    border: `1px solid ${colors.border}`,
    borderRadius: "20px",
    background: colors.white,
    color: colors.textSecondary,
    cursor: "pointer",
    fontFamily: "inherit",
    lineHeight: 1.4,
    textAlign: "left",
  },

  // ── Messages ──
  userMsg: {
    display: "flex",
    flexDirection: "column",
    gap: "6px",
    alignItems: "flex-start",
  },
  userBubble: {
    borderRadius: "10px",
    padding: "12px 16px",
    fontSize: "15px",
    lineHeight: "1.55",
    color: colors.textPrimary,
    fontWeight: "500",
    maxWidth: "100%",
  },
  assistantMsg: {
    display: "flex",
    flexDirection: "column",
    gap: "6px",
  },
  assistantBubble: {
    background: colors.white,
    border: `1px solid ${colors.border}`,
    borderRadius: "12px",
    padding: "20px 24px",
    fontSize: "14px",
    lineHeight: "1.75",
    color: colors.textSecondary,
  },
  roleLabel: {
    fontSize: "11px",
    fontWeight: "600",
    textTransform: "uppercase" as const,
    letterSpacing: "0.06em",
    color: colors.textFaint,
    paddingLeft: "4px",
  },
};
