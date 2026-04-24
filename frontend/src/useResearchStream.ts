import { useState, useRef, useCallback } from "react";
import type { SSEEvent, ResearchState, NodeName } from "./types";
import {
  makeInitialResearchState,
  DEFAULT_VISIBLE_NODES,
  ROUTE_NODES,
} from "./types";

export function useResearchStream() {
  const [state, setState] = useState<ResearchState | null>(null);
  const sourceRef = useRef<EventSource | null>(null);

  const close = useCallback(() => {
    sourceRef.current?.close();
    sourceRef.current = null;
  }, []);

  const start = useCallback(
    (question: string) => {
      // Close any in-flight stream before starting a new one
      close();

      // Set optimistic initial state — all nodes queued immediately
      setState(makeInitialResearchState());

      const params = new URLSearchParams({ question });
      const source = new EventSource(`/research/stream?${params}`);
      sourceRef.current = source;

      source.onmessage = (e: MessageEvent) => {
        let event: SSEEvent;
        try {
          event = JSON.parse(e.data);
        } catch {
          return;
        }

        setState((prev) => {
          if (!prev) return prev;
          return applyEvent(prev, event);
        });

        // Close the connection cleanly on terminal events
        if (event.type === "done" || event.type === "error") {
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
    [close],
  );

  return { state, start, stop: close };
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

    case "node_complete": {
      const node = event.node as NodeName;
      if (!state.nodes[node]) return state;
      const updatedNodes = {
        ...state.nodes,
        [node]: { ...state.nodes[node], status: "done", data: event.data },
      };

      // When router completes, we know the route — reveal the right nodes
      let visibleNodes = state.visibleNodes;
      let route = state.route;

      if (node === "router" && event.data?.route) {
        route = event.data.route as string;
        visibleNodes = ROUTE_NODES[route] ?? DEFAULT_VISIBLE_NODES;
      }

      return { ...state, nodes: updatedNodes, route, visibleNodes };
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

    case "done": {
      return { ...state, phase: "done", finalReport: event.report };
    }

    case "error": {
      return { ...state, phase: "error", errorMsg: event.message };
    }

    default:
      return state;
  }
}
