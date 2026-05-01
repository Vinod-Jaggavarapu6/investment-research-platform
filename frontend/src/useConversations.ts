import { useState, useCallback, useEffect } from "react";
import type { Conversation, Message } from "./types";

export function useConversations(sessionId: string) {
  const [conversations, setConversations] = useState<Conversation[]>([]);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`/conversations?session_id=${sessionId}`);
      if (res.ok) setConversations(await res.json());
    } catch {
      // sidebar stays empty on network error
    }
  }, [sessionId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const loadMessages = useCallback(
    async (conversationId: string): Promise<Message[]> => {
      try {
        const res = await fetch(`/conversations/${conversationId}/messages`);
        if (!res.ok) return [];
        const data = await res.json();
        return data.messages ?? [];
      } catch {
        return [];
      }
    },
    [],
  );

  const deleteConversation = useCallback(
    async (conversationId: string) => {
      await fetch(`/conversations/${conversationId}`, { method: "DELETE" });
      setConversations((prev) => prev.filter((c) => c.id !== conversationId));
    },
    [],
  );

  return { conversations, refresh, loadMessages, deleteConversation };
}
