// Which nodes run for each route — matches graph.py logic exactly
export const ROUTE_NODES: Record<string, NodeName[]> = {
  market: ["router", "market_agent", "synthesizer"],
  filings: ["router", "filings_agent", "synthesizer"],
  news: ["router", "news_agent", "synthesizer"],
  both: ["router", "market_agent", "filings_agent", "synthesizer"],
  comprehensive: [
    "router",
    "market_agent",
    "filings_agent",
    "news_agent",
    "synthesizer",
  ],
};

// Before route is known, show only router as running
export const DEFAULT_VISIBLE_NODES: NodeName[] = ["router"];

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
  data: Record<string, unknown> | null;
  tokens: string;
}

// Full research session state
export interface ResearchState {
  phase: "idle" | "streaming" | "done" | "error";
  nodes: Record<NodeName, AgentState>;
  finalReport: string | null;
  errorMsg: string | null;
  route: string | null;
  ticker: string | null;
  visibleNodes: NodeName[];
  startedAt: number | null;
  completedAt: number | null;
}

// Display labels for each node
export const NODE_LABELS: Record<NodeName, string> = {
  router: "Router",
  market_agent: "Market Data",
  filings_agent: "SEC Filings",
  news_agent: "News Sentiment",
  synthesizer: "Synthesizer",
};

// Loading messages shown while each node is running
export const NODE_LOADING_MESSAGES: Record<NodeName, string[]> = {
  router: [
    "Analyzing your question…",
    "Identifying ticker…",
    "Selecting research route…",
  ],
  market_agent: [
    "Fetching live market data…",
    "Pulling price and ratios…",
    "Analyzing market metrics…",
  ],
  filings_agent: [
    "Searching SEC filings…",
    "Scanning 10-K documents…",
    "Retrieving relevant excerpts…",
  ],
  news_agent: [
    "Scanning recent headlines…",
    "Scoring news sentiment…",
    "Weighing source quality…",
  ],
  synthesizer: ["Synthesizing research…", "Drafting your report…"],
};

// Ordered list for timeline rendering — defines display order
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
    route: null,
    ticker: null,
    visibleNodes: ["router"],
    startedAt: Date.now(),
    completedAt: null,
    nodes: {
      router: { ...INITIAL_NODE_STATE },
      market_agent: { ...INITIAL_NODE_STATE },
      filings_agent: { ...INITIAL_NODE_STATE },
      news_agent: { ...INITIAL_NODE_STATE },
      synthesizer: { ...INITIAL_NODE_STATE },
    },
  };
}
