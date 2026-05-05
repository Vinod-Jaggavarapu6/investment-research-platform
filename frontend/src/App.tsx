import { useState, useEffect, useCallback } from "react";
import { useResearchStream } from "./useResearchStream";
import { useConversations } from "./useConversations";
import { ConversationSidebar } from "./components/ConversationSidebar";
import { ChatWindow } from "./components/ChatWindow";
import { colors } from "./theme";
import type { Message } from "./types";

export default function App() {
  const { state, start, reset, sessionId } = useResearchStream();
  const { conversations, refresh, loadMessages, deleteConversation, networkError, clearNetworkError } =
    useConversations(sessionId);

  const [activeConversationId, setActiveConversationId] = useState<
    string | null
  >(null);
  const [historyMessages, setHistoryMessages] = useState<Message[]>([]);
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);

  const isStreaming = state?.phase === "streaming";

  // Eager: as soon as conversation_ready fires, pin the active conversation and refresh sidebar
  useEffect(() => {
    if (!state?.conversationId) return;
    setActiveConversationId(state.conversationId);
    refresh();
  }, [state?.conversationId, refresh]);

  // Done: reload messages from DB once the full exchange has been persisted
  useEffect(() => {
    if (state?.phase !== "done" || !state.conversationId) return;
    const convId = state.conversationId;
    loadMessages(convId).then((msgs) => {
      setHistoryMessages(msgs);
      setPendingQuestion(null);
    });
  }, [state?.phase, state?.conversationId, loadMessages]);

  const handleSelectConversation = useCallback(
    async (id: string) => {
      setActiveConversationId(id);
      setPendingQuestion(null);
      const msgs = await loadMessages(id);
      setHistoryMessages(msgs);
    },
    [loadMessages],
  );

  const handleNewConversation = useCallback(() => {
    reset();
    setActiveConversationId(null);
    setHistoryMessages([]);
    setPendingQuestion(null);
  }, [reset]);

  const handleDelete = useCallback(
    async (id: string) => {
      await deleteConversation(id);
      // Also check state.conversationId: if conversation_ready hasn't fired yet,
      // activeConversationId is still null but the stream is already tied to this id.
      if (id === activeConversationId || id === state?.conversationId) {
        reset();
        setActiveConversationId(null);
        setHistoryMessages([]);
        setPendingQuestion(null);
      }
    },
    [deleteConversation, activeConversationId, state?.conversationId, reset],
  );

  const handleStart = useCallback(
    (question: string) => {
      setPendingQuestion(question);
      start(question, activeConversationId);
    },
    [start, activeConversationId],
  );

  return (
    <div style={styles.page}>
      <header style={styles.header}>
        <h1 style={styles.title}>Investment Research</h1>
      </header>

      {networkError && (
        <ErrorToast message={networkError} onDismiss={clearNetworkError} />
      )}

      <div style={styles.body}>
        <ConversationSidebar
          conversations={conversations}
          activeId={activeConversationId}
          onSelect={handleSelectConversation}
          onNew={handleNewConversation}
          onDelete={handleDelete}
        />

        <div style={styles.content}>
          <ChatWindow
            messages={historyMessages}
            pendingQuestion={pendingQuestion}
            streamingState={state}
            onSubmit={handleStart}
            isStreaming={isStreaming}
          />
        </div>
      </div>
    </div>
  );
}

function ErrorToast({ message, onDismiss }: { message: string; onDismiss: () => void }) {
  return (
    <div style={toastStyles.toast}>
      <span style={toastStyles.icon}>⚠</span>
      <span style={toastStyles.text}>{message}</span>
      <button style={toastStyles.close} onClick={onDismiss} aria-label="Dismiss">✕</button>
    </div>
  );
}

const toastStyles: Record<string, React.CSSProperties> = {
  toast: {
    display: "flex",
    alignItems: "center",
    gap: "10px",
    padding: "10px 16px",
    background: colors.errorBg,
    borderBottom: `1px solid ${colors.errorBorder}`,
    flexShrink: 0,
  },
  icon: { color: colors.error, fontSize: "14px", flexShrink: 0 },
  text: { flex: 1, fontSize: "13px", color: colors.errorText },
  close: {
    background: "none",
    border: "none",
    cursor: "pointer",
    color: colors.errorText,
    fontSize: "13px",
    padding: "0 2px",
    lineHeight: 1,
    flexShrink: 0,
  },
};

const styles: Record<string, React.CSSProperties> = {
  page: {
    height: "100vh",
    display: "flex",
    flexDirection: "column",
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    color: colors.textPrimary,
    background: colors.bgPage,
  },
  header: {
    padding: "14px 24px",
    borderBottom: `1px solid ${colors.border}`,
    background: colors.white,
    flexShrink: 0,
    display: "flex",
    alignItems: "center",
    gap: "16px",
  },
  title: {
    margin: 0,
    fontSize: "17px",
    fontWeight: "700",
    letterSpacing: "-0.01em",
  },
  body: {
    display: "flex",
    flex: 1,
    overflow: "hidden",
  },
  content: {
    flex: 1,
    overflow: "hidden",
    display: "flex",
    flexDirection: "column",
  },
};
