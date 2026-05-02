import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ResearchState, NodeName, NodeStatus, Citation } from "../types";
import { colors } from "../theme";

const THINKING_DOTS_INTERVAL_MS = 450;
const INGEST_DOTS_INTERVAL_MS = 1000;
const ELAPSED_UPDATE_INTERVAL_MS = 100;

interface Props {
  research: ResearchState;
}

export function AgentTimeline({ research }: Props) {
  const {
    visibleNodes,
    nodes,
    phase,
    ticker,
    finalReport,
    startedAt,
    completedAt,
    ingestPending,
    ingestTicker,
    citations,
  } = research;
  const elapsed = useElapsed(startedAt, completedAt);

  // When ingest is pending, only show nodes that actually ran (not "queued" ones).
  // Queued nodes were never started — showing them with action messages is misleading.
  const displayNodes = ingestPending
    ? visibleNodes.filter((n) => nodes[n].status !== "queued")
    : visibleNodes;

  // For routes that bypass the synthesizer (e.g. compare), finalReport arrives via
  // the done event but synthesizer.tokens is empty — render it here directly.
  const synthTokens = nodes.synthesizer?.tokens ?? "";
  const showFinalReport =
    phase === "done" && !ingestPending && finalReport && !synthTokens;

  return (
    <div style={styles.log}>
      {displayNodes.map((node) => (
        <LogLine
          key={node}
          node={node}
          status={nodes[node].status}
          data={nodes[node].data}
          tokens={nodes[node].tokens}
          ticker={ticker}
        />
      ))}

      {ingestPending && ingestTicker && (
        <IngestPollingLine ticker={ingestTicker} />
      )}

      {showFinalReport && (
        <div style={styles.reportArea}>
          <Markdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
            {finalReport}
          </Markdown>
        </div>
      )}

      {phase === "done" && !ingestPending && citations.length > 0 && (
        <CitationsBlock citations={citations} />
      )}

      {phase === "done" && !ingestPending && elapsed > 0 && (
        <div style={styles.completeLine}>
          <span style={{ ...styles.icon, color: colors.success }}>✓</span>
          <span style={styles.completeText}>
            Research complete · {(elapsed / 1000).toFixed(1)}s
          </span>
        </div>
      )}

      {phase === "error" && (
        <div style={styles.errorLine}>
          <span style={{ ...styles.icon, color: colors.error }}>✕</span>
          <span style={styles.errorText}>
            {research.errorMsg ?? "Something went wrong. Please try again."}
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
  const isDone = status === "done";

  // Strip trailing ellipsis — ThinkingDots replaces it when running
  const raw = activityMessage(node, status, ticker, data);
  const message = isRunning ? raw.replace(/…$/, "") : raw;

  return (
    <div style={styles.line}>
      <div style={styles.lineHeader}>
        <span
          style={{
            ...styles.icon,
            color: isRunning ? colors.warning : isDone ? colors.success : colors.borderMuted,
          }}
        >
          {isRunning ? <PulsingDot /> : isDone ? "✓" : "○"}
        </span>
        <span
          style={{
            ...styles.lineText,
            color: isDone ? colors.textFaint : colors.textPrimary,
            animation:
              isRunning && !tokens ? "pulse 2s ease-in-out infinite" : "none",
          }}
        >
          {message}
          {isRunning && !tokens && <ThinkingDots />}
        </span>
      </div>

      {node === "synthesizer" && tokens && (
        <div style={styles.reportArea}>
          <Markdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
            {tokens}
          </Markdown>
          {isRunning && <span style={styles.cursor}>▋</span>}
        </div>
      )}
    </div>
  );
}

// ── Pulsing dot (icon for running state) ──────────────────────────────────

function PulsingDot({ color = colors.warning }: { color?: string }) {
  return (
    <span
      style={{
        display: "inline-block",
        width: "8px",
        height: "8px",
        borderRadius: "50%",
        background: color,
        animation: "pulseScale 1.2s ease-in-out infinite",
      }}
    />
  );
}

// ── Cycling dots (appended to running message text) ────────────────────────

function ThinkingDots() {
  const [frame, setFrame] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setFrame((f) => (f + 1) % 4), THINKING_DOTS_INTERVAL_MS);
    return () => clearInterval(id);
  }, []);
  const dots = ["", ".", "..", "..."][frame];
  return (
    <span
      style={{ color: colors.textFaint, letterSpacing: "0.05em", marginLeft: "1px" }}
    >
      {dots}
    </span>
  );
}

// ── Elapsed timer hook ─────────────────────────────────────────────────────

function useElapsed(
  startedAt: number | null,
  completedAt: number | null,
): number {
  const [runningElapsed, setRunningElapsed] = useState(0);

  useEffect(() => {
    if (!startedAt || completedAt) return;
    const id = setInterval(
      () => setRunningElapsed(Date.now() - startedAt),
      ELAPSED_UPDATE_INTERVAL_MS,
    );
    return () => clearInterval(id);
  }, [startedAt, completedAt]);

  // Synchronous calculation when complete — avoids a missed render frame
  if (completedAt && startedAt) return completedAt - startedAt;
  return runningElapsed;
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
        filings_recent: "recent filings",
        news: "news sentiment",
        both: "market + SEC filings",
        comprehensive: "comprehensive research",
        compare: "company comparison",
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
      return status === "done" ? "Report ready" : "Drafting your report…";
    case "compare_agent": {
      const tickers = (data?.tickers as string[] | undefined)?.join(" vs ");
      return status === "done"
        ? `Comparison ready${tickers ? ` · ${tickers}` : ""}`
        : "Comparing companies side-by-side…";
    }
  }
}

// ── Ingest pending notice ──────────────────────────────────────────────────

function IngestPollingLine({ ticker }: { ticker: string }) {
  const [dots, setDots] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setDots((d) => (d + 1) % 4), INGEST_DOTS_INTERVAL_MS);
    return () => clearInterval(id);
  }, []);
  const dotStr = ".".repeat(dots);

  return (
    <div style={styles.ingestNotice}>
      <div style={styles.ingestHeader}>
        <PulsingDot color={colors.brand} />
        <span style={styles.ingestTitle}>
          Indexing {ticker.toUpperCase()} SEC filings{dotStr}
        </span>
      </div>
      <p style={styles.ingestBody}>
        {ticker.toUpperCase()} filings haven't been indexed yet. Indexing is
        running in the background and your full analysis will start
        automatically once it's ready (typically 1–3 minutes).
      </p>
    </div>
  );
}

// ── Citations block ────────────────────────────────────────────────────────

function CitationsBlock({ citations }: { citations: Citation[] }) {
  // Deduplicate by ticker+year+filing_type+section
  const seen = new Set<string>();
  const unique = citations.filter((c) => {
    const key = `${c.ticker}-${c.year}-${c.filing_type}-${c.section}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  return (
    <div style={styles.citationsBlock}>
      <span style={styles.citationsLabel}>Sources</span>
      <div style={styles.citationsList}>
        {unique.map((c, i) => (
          <span key={i} style={styles.citationChip}>
            {c.ticker} {c.year} · {c.filing_type} · {c.section}
          </span>
        ))}
      </div>
    </div>
  );
}

// ── Markdown table renderers ───────────────────────────────────────────────

const markdownComponents = {
  table: ({ children }: { children?: React.ReactNode }) => (
    <div style={{ overflowX: "auto", margin: "12px 0" }}>
      <table
        style={{
          borderCollapse: "collapse",
          width: "100%",
          fontSize: "13px",
          lineHeight: "1.5",
        }}
      >
        {children}
      </table>
    </div>
  ),
  thead: ({ children }: { children?: React.ReactNode }) => (
    <thead style={{ background: colors.bgLight }}>{children}</thead>
  ),
  th: ({ children }: { children?: React.ReactNode }) => (
    <th
      style={{
        padding: "8px 12px",
        border: `1px solid ${colors.border}`,
        fontWeight: 600,
        textAlign: "left",
        color: colors.textPrimary,
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </th>
  ),
  td: ({ children }: { children?: React.ReactNode }) => (
    <td
      style={{
        padding: "7px 12px",
        border: `1px solid ${colors.border}`,
        color: colors.textSecondary,
        verticalAlign: "top",
      }}
    >
      {children}
    </td>
  ),
  tr: ({ children }: { children?: React.ReactNode }) => (
    <tr style={{ borderBottom: `1px solid ${colors.border}` }}>{children}</tr>
  ),
};

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
    borderLeft: `2px solid ${colors.border}`,
    fontSize: "14px",
    lineHeight: "1.7",
    color: colors.textSecondary,
  },
  cursor: {
    display: "inline-block",
    animation: "blink 1s step-end infinite",
    color: colors.textMuted,
    verticalAlign: "text-bottom",
  },
  completeLine: {
    display: "flex",
    alignItems: "center",
    gap: "10px",
    paddingTop: "12px",
    marginTop: "4px",
    borderTop: `1px solid ${colors.bgLight}`,
  },
  completeText: {
    fontSize: "13px",
    color: colors.textMuted,
    fontWeight: 500,
  },
  errorLine: {
    display: "flex",
    alignItems: "center",
    gap: "10px",
    paddingTop: "12px",
    marginTop: "4px",
    borderTop: `1px solid ${colors.errorDivider}`,
  },
  errorText: {
    fontSize: "13px",
    color: colors.errorText,
    fontWeight: 500,
  },
  ingestNotice: {
    marginTop: "8px",
    padding: "12px 14px",
    borderRadius: "8px",
    background: colors.brandBg,
    border: `1px solid ${colors.brandBorder}`,
  },
  ingestHeader: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    marginBottom: "6px",
  },
  ingestTitle: {
    fontSize: "13px",
    fontWeight: 600,
    color: colors.brandDark,
  },
  ingestBody: {
    margin: 0,
    fontSize: "13px",
    color: colors.textSecondary,
    lineHeight: 1.55,
  },
  citationsBlock: {
    marginLeft: "26px",
    paddingLeft: "16px",
    borderLeft: `2px solid ${colors.border}`,
    display: "flex",
    flexDirection: "column",
    gap: "8px",
  },
  citationsLabel: {
    fontSize: "11px",
    fontWeight: 600,
    textTransform: "uppercase" as const,
    letterSpacing: "0.06em",
    color: colors.textFaint,
  },
  citationsList: {
    display: "flex",
    flexWrap: "wrap" as const,
    gap: "6px",
  },
  citationChip: {
    fontSize: "12px",
    color: colors.textSecondary,
    background: colors.bgLight,
    border: `1px solid ${colors.border}`,
    borderRadius: "4px",
    padding: "2px 8px",
    whiteSpace: "nowrap" as const,
  },
};
