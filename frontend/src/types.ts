// Which nodes run for each route — matches graph.py logic exactly
export const ROUTE_NODES: Record<string, NodeName[]> = {
  market: ["router", "market_agent", "synthesizer"],
  filings: ["router", "filings_agent", "synthesizer"],
  filings_recent: ["router", "filings_agent", "synthesizer"],
  news: ["router", "news_agent", "synthesizer"],
  both: ["router", "market_agent", "filings_agent", "synthesizer"],
  comprehensive: [
    "router",
    "market_agent",
    "filings_agent",
    "news_agent",
    "synthesizer",
  ],
  compare: ["router", "compare_agent"],
};

// Before route is known, show only router as running
export const DEFAULT_VISIBLE_NODES: NodeName[] = ["router"];

// Agent node names — must match backend TRACKED_NODES
export type NodeName =
  | "router"
  | "market_agent"
  | "filings_agent"
  | "news_agent"
  | "synthesizer"
  | "compare_agent";

// Per-node status in the UI
export type NodeStatus = "queued" | "running" | "done" | "error";

// A single SEC filing source returned by the filings agent.
// MUST stay in sync with CitationOut in backend/app/sse_types.py.
export interface Citation {
  ticker: string;
  year: number;
  section: string;
  filing_type: string;
  score: number;
  text: string;
}

// SSE event shapes emitted by the backend streaming pipeline.
// MUST stay in sync with the models in backend/app/sse_types.py.
export type SSEEvent =
  | { type: "node_start"; node: NodeName }
  | { type: "node_complete"; node: NodeName; data: Record<string, unknown> }
  | { type: "node_error"; node: NodeName; reason: string }
  | { type: "token"; text: string }
  | { type: "done"; report: string | null; ingesting_ticker?: string | null; citations?: Citation[]; conversation_id?: string }
  | { type: "conversation_ready"; conversation_id: string }
  | { type: "error"; message: string };

// Conversation and message types from backend
export interface Conversation {
  id: string;
  session_id: string;
  title: string;
  ticker: string | null;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

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
  citations: Citation[];
  errorMsg: string | null;
  route: string | null;
  ticker: string | null;
  visibleNodes: NodeName[];
  startedAt: number | null;
  completedAt: number | null;
  ingestPending: boolean;
  ingestTicker: string | null;
  conversationId: string | null;
}

// Display labels for each node
export const NODE_LABELS: Record<NodeName, string> = {
  router: "Router",
  market_agent: "Market Data",
  filings_agent: "SEC Filings",
  news_agent: "News Sentiment",
  synthesizer: "Synthesizer",
  compare_agent: "Comparison",
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
  compare_agent: [
    "Retrieving filings for each company…",
    "Running parallel searches…",
    "Comparing side-by-side…",
  ],
};

// Ordered list for timeline rendering — defines display order
export const NODE_ORDER: NodeName[] = [
  "router",
  "market_agent",
  "filings_agent",
  "news_agent",
  "synthesizer",
  "compare_agent",
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
    citations: [],
    ingestPending: false,
    ingestTicker: null,
    conversationId: null,
    nodes: {
      router: { ...INITIAL_NODE_STATE },
      market_agent: { ...INITIAL_NODE_STATE },
      filings_agent: { ...INITIAL_NODE_STATE },
      news_agent: { ...INITIAL_NODE_STATE },
      synthesizer: { ...INITIAL_NODE_STATE },
      compare_agent: { ...INITIAL_NODE_STATE },
    },
  };
}
