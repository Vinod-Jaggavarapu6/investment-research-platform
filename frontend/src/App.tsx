import { useResearchStream } from "./useResearchStream";
import { SearchBar } from "./components/SearchBar";
import { AgentTimeline } from "./components/AgentTimeline";

export default function App() {
  const { state, start } = useResearchStream();

  const isStreaming = state?.phase === "streaming";

  return (
    <div style={styles.page}>
      <header style={styles.header}>
        <h1 style={styles.title}>Investment Research</h1>
        <p style={styles.sub}>
          Multi-agent analysis · SEC filings · Live market data · News sentiment
        </p>
      </header>

      <main style={styles.main}>
        <SearchBar onSubmit={(q) => start(q)} disabled={isStreaming} />

        {state?.errorMsg && (
          <div style={styles.error}>Error: {state.errorMsg}</div>
        )}

        {state && (
          <div style={styles.results}>
            <AgentTimeline research={state} />
          </div>
        )}
      </main>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  page: {
    minHeight: "100vh",
    background: "#f9fafb",
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    color: "#111827",
  },
  header: {
    padding: "40px 48px 24px",
    borderBottom: "1px solid #e5e7eb",
    background: "#fff",
  },
  title: {
    margin: "0 0 4px",
    fontSize: "22px",
    fontWeight: "700",
  },
  sub: {
    margin: 0,
    fontSize: "13px",
    color: "#6b7280",
  },
  main: {
    maxWidth: "860px",
    margin: "0 auto",
    padding: "32px 24px",
    display: "flex",
    flexDirection: "column",
    gap: "24px",
  },
  results: {
    display: "flex",
    flexDirection: "column",
    gap: "12px",
  },
  error: {
    padding: "12px 16px",
    borderRadius: "8px",
    background: "#fef2f2",
    border: "1px solid #fca5a5",
    color: "#dc2626",
    fontSize: "14px",
  },
};
