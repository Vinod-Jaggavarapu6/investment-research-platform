// Agent node names — must match backend TRACKED_NODES
export type NodeName =
  | "router"
  | "market_agent"
  | "filings_agent"
  | "news_agent"
  | "synthesizer";

// Per-node status in the UI
export type NodeStatus = "queued" | "running" | "done" | "error";

// SSE event shapes from the backend
export type SSEEvent =
  | { type: "node_start"; node: NodeName }
  | { type: "node_complete"; node: NodeName; data: Record<string, unknown> }
  | { type: "token"; text: string }
  | { type: "done"; report: string | null }
  | { type: "error"; message: string };

// UI state for a single agent node
export interface AgentState {
  status: NodeStatus;
  data: Record<string, unknown> | null; // from node_complete
  tokens: string; // accumulated synthesizer tokens
}

// Full research session state
export interface ResearchState {
  phase: "idle" | "streaming" | "done" | "error";
  nodes: Record<NodeName, AgentState>;
  finalReport: string | null;
  errorMsg: string | null;
}

// Display labels for each node
export const NODE_LABELS: Record<NodeName, string> = {
  router: "Router",
  market_agent: "Market Data",
  filings_agent: "SEC Filings",
  news_agent: "News Sentiment",
  synthesizer: "Synthesizer",
};

// Ordered list for timeline rendering
export const NODE_ORDER: NodeName[] = [
  "router",
  "market_agent",
  "filings_agent",
  "news_agent",
  "synthesizer",
];

export const INITIAL_NODE_STATE: AgentState = {
  status: "queued",
  data: null,
  tokens: "",
};

export function makeInitialResearchState(): ResearchState {
  return {
    phase: "streaming",
    errorMsg: null,
    finalReport: null,
    nodes: {
      router: { ...INITIAL_NODE_STATE },
      market_agent: { ...INITIAL_NODE_STATE },
      filings_agent: { ...INITIAL_NODE_STATE },
      news_agent: { ...INITIAL_NODE_STATE },
      synthesizer: { ...INITIAL_NODE_STATE },
    },
  };
}
