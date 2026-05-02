import { useState, useCallback, useEffect } from "react";
import type { Conversation, Message } from "./types";

export function useConversations(sessionId: string) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [networkError, setNetworkError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`/conversations?session_id=${sessionId}`);
      if (res.ok) setConversations(await res.json());
      else setNetworkError("Failed to load conversation history");
    } catch {
      setNetworkError("Failed to load conversation history");
    }
  }, [sessionId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const loadMessages = useCallback(
    async (conversationId: string): Promise<Message[]> => {
      try {
        const res = await fetch(`/conversations/${conversationId}/messages`);
        if (!res.ok) { setNetworkError("Failed to load messages"); return []; }
        const data = await res.json();
        return data.messages ?? [];
      } catch {
        setNetworkError("Failed to load messages");
        return [];
      }
    },
    [],
  );

  const deleteConversation = useCallback(
    async (conversationId: string) => {
      try {
        await fetch(`/conversations/${conversationId}`, { method: "DELETE" });
        setConversations((prev) => prev.filter((c) => c.id !== conversationId));
      } catch {
        setNetworkError("Failed to delete conversation");
      }
    },
    [],
  );

  return {
    conversations,
    refresh,
    loadMessages,
    deleteConversation,
    networkError,
    clearNetworkError: () => setNetworkError(null),
  };
}
