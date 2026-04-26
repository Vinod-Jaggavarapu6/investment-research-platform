import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import type { ResearchState, NodeName, NodeStatus } from "../types";

interface Props {
  research: ResearchState;
}

export function AgentTimeline({ research }: Props) {
  const { visibleNodes, nodes, phase, ticker, startedAt, completedAt } =
    research;
  const elapsed = useElapsed(startedAt, completedAt);

  return (
    <div style={styles.log}>
      {visibleNodes.map((node) => (
        <LogLine
          key={node}
          node={node}
          status={nodes[node].status}
          data={nodes[node].data}
          tokens={nodes[node].tokens}
          ticker={ticker}
        />
      ))}

      {phase === "done" && elapsed > 0 && (
        <div style={styles.completeLine}>
          <span style={{ ...styles.icon, color: "#10b981" }}>✓</span>
          <span style={styles.completeText}>
            Research complete · {(elapsed / 1000).toFixed(1)}s
          </span>
        </div>
      )}
    </div>
  );
}

// ── Single log line ────────────────────────────────────────────────────────

interface LineProps {
  node: NodeName;
  status: NodeStatus;
  data: Record<string, unknown> | null;
  tokens: string;
  ticker: string | null;
}

function LogLine({ node, status, data, tokens, ticker }: LineProps) {
  const isRunning = status === "running";
  const isDone    = status === "done";

  // Strip trailing ellipsis — ThinkingDots replaces it when running
  const raw     = activityMessage(node, status, ticker, data);
  const message = isRunning ? raw.replace(/…$/, "") : raw;

  return (
    <div style={styles.line}>
      <div style={styles.lineHeader}>
        <span
          style={{
            ...styles.icon,
            color: isRunning ? "#f59e0b" : isDone ? "#10b981" : "#d1d5db",
          }}
        >
          {isRunning ? <PulsingDot /> : isDone ? "✓" : "○"}
        </span>
        <span
          style={{
            ...styles.lineText,
            color: isDone ? "#9ca3af" : "#111827",
            animation: isRunning && !tokens ? "pulse 2s ease-in-out infinite" : "none",
          }}
        >
          {message}
          {isRunning && !tokens && <ThinkingDots />}
        </span>
      </div>

      {node === "synthesizer" && tokens && (
        <div style={styles.reportArea}>
          <Markdown>{tokens}</Markdown>
          {isRunning && <span style={styles.cursor}>▋</span>}
        </div>
      )}
    </div>
  );
}

// ── Pulsing dot (icon for running state) ──────────────────────────────────

function PulsingDot() {
  return (
    <span
      style={{
        display:      "inline-block",
        width:        "8px",
        height:       "8px",
        borderRadius: "50%",
        background:   "#f59e0b",
        animation:    "pulseScale 1.2s ease-in-out infinite",
      }}
    />
  );
}

// ── Cycling dots (appended to running message text) ────────────────────────

function ThinkingDots() {
  const [frame, setFrame] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setFrame((f) => (f + 1) % 4), 450);
    return () => clearInterval(id);
  }, []);
  const dots = ["", ".", "..", "..."][frame];
  return (
    <span style={{ color: "#9ca3af", letterSpacing: "0.05em", marginLeft: "1px" }}>
      {dots}
    </span>
  );
}

// ── Elapsed timer hook ─────────────────────────────────────────────────────

function useElapsed(
  startedAt: number | null,
  completedAt: number | null,
): number {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!startedAt) return;
    if (completedAt) {
      setElapsed(completedAt - startedAt);
      return;
    }
    const id = setInterval(() => setElapsed(Date.now() - startedAt), 100);
    return () => clearInterval(id);
  }, [startedAt, completedAt]);

  return elapsed;
}

// ── Message copy ───────────────────────────────────────────────────────────

function activityMessage(
  node: NodeName,
  status: NodeStatus,
  ticker: string | null,
  data?: Record<string, unknown> | null,
): string {
  const t = ticker?.toUpperCase() ?? "your stock";

  switch (node) {
    case "router": {
      if (status !== "done") return "Analyzing your question…";
      const routeLabels: Record<string, string> = {
        market: "market analysis",
        filings: "SEC filing research",
        news: "news sentiment",
        both: "market + SEC filings",
        comprehensive: "comprehensive research",
      };
      const route = data?.route as string | undefined;
      return ticker
        ? `Identified ${t} · ${routeLabels[route ?? ""] ?? "analysis"}`
        : "Question analyzed";
    }
    case "market_agent":
      return status === "done"
        ? `${t} market data collected`
        : `Fetching ${t}'s live market data…`;
    case "filings_agent":
      return status === "done"
        ? `${t} SEC filings retrieved`
        : `Scanning ${t}'s SEC filings…`;
    case "news_agent":
      return status === "done"
        ? `News sentiment scored for ${t}`
        : `Analyzing recent news for ${t}…`;
    case "synthesizer":
      return status === "done" ? "Report ready" : "Synthesizing your report…";
  }
}

// ── Styles ─────────────────────────────────────────────────────────────────

const styles: Record<string, React.CSSProperties> = {
  log: {
    display: "flex",
    flexDirection: "column",
    gap: "10px",
  },
  line: {
    display: "flex",
    flexDirection: "column",
    gap: "12px",
  },
  lineHeader: {
    display: "flex",
    alignItems: "center",
    gap: "10px",
  },
  icon: {
    fontSize: "13px",
    width: "16px",
    flexShrink: 0,
    lineHeight: 1,
    textAlign: "center",
  },
  lineText: {
    fontSize: "14px",
    lineHeight: 1.5,
  },
  reportArea: {
    marginLeft: "26px",
    paddingLeft: "16px",
    borderLeft: "2px solid #e5e7eb",
    fontSize: "14px",
    lineHeight: "1.7",
    color: "#374151",
  },
  cursor: {
    display: "inline-block",
    animation: "blink 1s step-end infinite",
    color: "#6b7280",
    verticalAlign: "text-bottom",
  },
  completeLine: {
    display: "flex",
    alignItems: "center",
    gap: "10px",
    paddingTop: "12px",
    marginTop: "4px",
    borderTop: "1px solid #f3f4f6",
  },
  completeText: {
    fontSize: "13px",
    color: "#6b7280",
    fontWeight: 500,
  },
};
