import { useState, useRef, useCallback, useEffect } from "react";
import type { SSEEvent, ResearchState, NodeName } from "./types";
import {
  makeInitialResearchState,
  DEFAULT_VISIBLE_NODES,
  ROUTE_NODES,
} from "./types";

export function useResearchStream() {
  const [state, setState] = useState<ResearchState | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Ref so the poll callback always calls the latest `start` without stale closure
  const startFnRef = useRef<(q: string) => void>(() => {});

  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const close = useCallback(() => {
    sourceRef.current?.close();
    sourceRef.current = null;
    stopPolling();
  }, [stopPolling]);

  const start = useCallback(
    (question: string) => {
      close();
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

        setState((prev) => (prev ? applyEvent(prev, event) : prev));

        if (event.type === "done") {
          console.log(
            `[stream] done event received — ingesting_ticker=${event.ingesting_ticker ?? "none"} has_report=${Boolean(event.report)}`,
          );
          source.close();
          sourceRef.current = null;

          if (event.ingesting_ticker) {
            const ticker = event.ingesting_ticker;
            const POLL_INTERVAL_MS = 30_000;
            // Only count "not_found" responses toward the give-up limit.
            // While status === "ingesting" we keep polling indefinitely —
            // large companies (e.g. CVX) can take 5-10 min to fully index.
            let notFoundStreak = 0;
            const MAX_NOT_FOUND = 4; // give up after ~2 min of no trace of the job
            console.log(
              `[ingest] filings not ready for ${ticker} — polling every ${POLL_INTERVAL_MS / 1000}s`,
            );
            setState((prev) =>
              prev
                ? { ...prev, ingestPending: true, ingestTicker: ticker }
                : prev,
            );
            pollRef.current = setInterval(async () => {
              try {
                const res = await fetch(`/ingest/status/${ticker}`);
                const data: { status: string } = await res.json();
                console.log(`[ingest] status for ${ticker}: ${data.status}`);

                if (data.status === "ready") {
                  console.log(`[ingest] ${ticker} filings ready — re-running research`);
                  stopPolling();
                  setState((prev) =>
                    prev ? { ...prev, ingestPending: false } : prev,
                  );
                  startFnRef.current(question);

                } else if (data.status === "ingesting") {
                  // Still running — reset the not-found streak and keep waiting
                  notFoundStreak = 0;

                } else {
                  // "not_found" — ingest may have failed or the server restarted
                  notFoundStreak += 1;
                  console.warn(
                    `[ingest] ${ticker} not found (streak ${notFoundStreak}/${MAX_NOT_FOUND})`,
                  );
                  if (notFoundStreak >= MAX_NOT_FOUND) {
                    console.warn(`[ingest] giving up on ${ticker} — no active ingest job found`);
                    stopPolling();
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
                  }
                }
              } catch (err) {
                console.warn(`[ingest] poll request failed for ${ticker}:`, err);
              }
            }, POLL_INTERVAL_MS);
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
    [close, stopPolling],
  );

  // Keep ref in sync so the poll interval never captures a stale start
  useEffect(() => {
    startFnRef.current = start;
  }, [start]);

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

    case "done": {
      return {
        ...state,
        phase: "done",
        finalReport: event.report,
        completedAt: Date.now(),
      };
    }

    case "error": {
      return { ...state, phase: "error", errorMsg: event.message };
    }

    default:
      return state;
  }
}
