import { useState, useEffect, useCallback } from "react";
import { useResearchStream } from "./useResearchStream";
import { useConversations } from "./useConversations";
import { ConversationSidebar } from "./components/ConversationSidebar";
import { ChatWindow } from "./components/ChatWindow";
import type { Message } from "./types";

export default function App() {
  const { state, start, reset, sessionId } = useResearchStream();
  const { conversations, refresh, loadMessages, deleteConversation } =
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
      if (id === activeConversationId) {
        reset();
        setActiveConversationId(null);
        setHistoryMessages([]);
        setPendingQuestion(null);
      }
    },
    [deleteConversation, activeConversationId, reset],
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
        {/* <p style={styles.sub}>
          Multi-agent · SEC filings · Live market data · News sentiment
        </p> */}
      </header>

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

const styles: Record<string, React.CSSProperties> = {
  page: {
    height: "100vh",
    display: "flex",
    flexDirection: "column",
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    color: "#111827",
    background: "#f9fafb",
  },
  header: {
    padding: "14px 24px",
    borderBottom: "1px solid #e5e7eb",
    background: "#fff",
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
  sub: {
    margin: 0,
    fontSize: "12px",
    color: "#9ca3af",
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
