import { useRef, useCallback } from "react";

const INGEST_POLL_INTERVAL_MS = 30_000;
const INGEST_MAX_NOT_FOUND_STREAK = 4;

interface PollerCallbacks {
  onReady: () => void;
  onGiveUp: () => void;
}

export function useIngestPoller() {
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Ref so callbacks set at startPolling-time are always fresh when the interval fires
  const callbacksRef = useRef<PollerCallbacks>({ onReady: () => {}, onGiveUp: () => {} });

  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback(
    (ticker: string, callbacks: PollerCallbacks) => {
      stopPolling();
      callbacksRef.current = callbacks;

      // Only count "not_found" responses toward the give-up limit.
      // While status === "ingesting" we keep polling indefinitely —
      // large companies (e.g. CVX) can take 5-10 min to fully index.
      let notFoundStreak = 0;
      console.log(
        `[ingest] filings not ready for ${ticker} — polling every ${INGEST_POLL_INTERVAL_MS / 1000}s`,
      );

      pollRef.current = setInterval(async () => {
        try {
          const res = await fetch(`/ingest/status/${ticker}`);
          const data: { status: string } = await res.json();
          console.log(`[ingest] status for ${ticker}: ${data.status}`);

          if (data.status === "ready") {
            console.log(`[ingest] ${ticker} filings ready — re-running research`);
            stopPolling();
            callbacksRef.current.onReady();
          } else if (data.status === "ingesting") {
            notFoundStreak = 0;
          } else {
            notFoundStreak += 1;
            console.warn(
              `[ingest] ${ticker} not found (streak ${notFoundStreak}/${INGEST_MAX_NOT_FOUND_STREAK})`,
            );
            if (notFoundStreak >= INGEST_MAX_NOT_FOUND_STREAK) {
              console.warn(`[ingest] giving up on ${ticker} — no active ingest job found`);
              stopPolling();
              callbacksRef.current.onGiveUp();
            }
          }
        } catch (err) {
          console.warn(`[ingest] poll request failed for ${ticker}:`, err);
        }
      }, INGEST_POLL_INTERVAL_MS);
    },
    [stopPolling],
  );

  return { startPolling, stopPolling };
}
