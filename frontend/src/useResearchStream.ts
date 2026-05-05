import { useState, useRef, useCallback, useEffect } from "react";
import type { SSEEvent, ResearchState, NodeName } from "./types";
import {
  makeInitialResearchState,
  DEFAULT_VISIBLE_NODES,
  ROUTE_NODES,
} from "./types";
import { useIngestPoller } from "./useIngestPoller";

function getOrCreateSessionId(): string {
  const key = "irp_session_id";
  let id = localStorage.getItem(key);
  if (!id) {
    id =
      typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID()
        : Math.random().toString(36).slice(2) + Date.now().toString(36);
    localStorage.setItem(key, id);
  }
  return id;
}

export function useResearchStream() {
  const [state, setState] = useState<ResearchState | null>(null);
  const [sessionId] = useState<string>(() => getOrCreateSessionId());
  const sourceRef = useRef<EventSource | null>(null);
  // Ref so the ingest onReady callback always calls the latest `start` without stale closure
  const startFnRef = useRef<(q: string, cid?: string | null) => void>(() => {});

  const { startPolling, stopPolling } = useIngestPoller();

  const close = useCallback(() => {
    sourceRef.current?.close();
    sourceRef.current = null;
    stopPolling();
  }, [stopPolling]);

  const reset = useCallback(() => {
    close();
    setState(null);
  }, [close]);

  const start = useCallback(
    (question: string, conversationId?: string | null) => {
      close();
      setState(makeInitialResearchState());

      const params = new URLSearchParams({ question, session_id: sessionId });
      if (conversationId) params.set("conversation_id", conversationId);
      const source = new EventSource(`/research/stream?${params}`);
      sourceRef.current = source;

      source.onmessage = (e: MessageEvent) => {
        let event: SSEEvent;
        try {
          event = JSON.parse(e.data);
        } catch {
          return;
        }

        setState((prev) => (prev ? applyEvent(prev, event) : prev));

        if (event.type === "done") {
          console.log(
            `[stream] done — ingesting_ticker=${event.ingesting_ticker ?? "none"} has_report=${Boolean(event.report)}`,
          );
          source.close();
          sourceRef.current = null;

          if (event.ingesting_ticker) {
            const ticker = event.ingesting_ticker;
            startPolling(ticker, {
              onReady: () => {
                // Use current state's conversationId so retry reuses the same conversation
                setState((prev) => {
                  const resolvedConvId = prev?.conversationId ?? conversationId ?? null;
                  setTimeout(() => startFnRef.current(question, resolvedConvId), 0);
                  return prev ? { ...prev, ingestPending: false } : prev;
                });
              },
              onGiveUp: () => {
                setState((prev) =>
                  prev
                    ? {
                        ...prev,
                        ingestPending: false,
                        phase: "error",
                        errorMsg: `Could not index ${ticker} filings. Try asking again manually.`,
                      }
                    : prev,
                );
              },
            });
          }
        }

        if (event.type === "error") {
          source.close();
          sourceRef.current = null;
        }
      };

      source.onerror = () => {
        setState((prev) =>
          prev
            ? { ...prev, phase: "error", errorMsg: "Connection lost" }
            : prev,
        );
        source.close();
        sourceRef.current = null;
      };
    },
    [close, startPolling, sessionId],
  );

  // Keep ref in sync so the ingest onReady callback never captures a stale start
  useEffect(() => {
    startFnRef.current = start;
  }, [start]);

  return { state, start, stop: close, reset, sessionId };
}

// Pure state reducer — applies one SSE event to ResearchState

function applyEvent(state: ResearchState, event: SSEEvent): ResearchState {
  switch (event.type) {
    case "node_start": {
      const node = event.node as NodeName;
      if (!state.nodes[node]) return state;
      const visibleNodes = state.visibleNodes.includes(node)
        ? state.visibleNodes
        : [...state.visibleNodes, node];
      return {
        ...state,
        visibleNodes,
        nodes: {
          ...state.nodes,
          [node]: { ...state.nodes[node], status: "running" },
        },
      };
    }

    case "node_error": {
      const node = event.node as NodeName;
      if (!state.nodes[node]) return state;
      return {
        ...state,
        nodes: {
          ...state.nodes,
          [node]: { ...state.nodes[node], status: "error", data: { reason: event.reason } },
        },
      };
    }

    case "node_complete": {
      const node = event.node as NodeName;
      if (!state.nodes[node]) return state;
      const updatedNodes = {
        ...state.nodes,
        [node]: { ...state.nodes[node], status: "done", data: event.data },
      };

      let visibleNodes = state.visibleNodes;
      let route = state.route;

      if (node === "router" && event.data?.route) {
        route = event.data.route as string;
        visibleNodes = ROUTE_NODES[route] ?? DEFAULT_VISIBLE_NODES;
      }

      const ticker =
        node === "router" && event.data?.ticker
          ? (event.data.ticker as string)
          : state.ticker;

      return { ...state, nodes: updatedNodes, route, ticker, visibleNodes };
    }

    case "token": {
      return {
        ...state,
        nodes: {
          ...state.nodes,
          synthesizer: {
            ...state.nodes.synthesizer,
            tokens: state.nodes.synthesizer.tokens + event.text,
          },
        },
      };
    }

    case "conversation_ready": {
      return { ...state, conversationId: event.conversation_id };
    }

    case "done": {
      const isIngestPending = Boolean(event.ingesting_ticker);
      return {
        ...state,
        // Stay "streaming" during ingest to avoid "Research complete" flash
        phase: isIngestPending ? "streaming" : "done",
        ingestPending: isIngestPending,
        ingestTicker: event.ingesting_ticker ?? state.ingestTicker,
        finalReport: event.report,
        citations: event.citations ?? [],
        completedAt: isIngestPending ? 0 : Date.now(),
        conversationId: event.conversation_id ?? state.conversationId,
      };
    }

    case "error": {
      return { ...state, phase: "error", errorMsg: event.message };
    }

    default:
      return state;
  }
}
