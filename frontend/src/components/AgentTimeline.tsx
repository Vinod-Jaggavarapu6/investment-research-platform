import { AgentCard } from "./AgentCard";
import type { ResearchState } from "../types";
import { NODE_ORDER, NODE_LABELS } from "../types";

interface Props {
  research: ResearchState;
}

export function AgentTimeline({ research }: Props) {
  return (
    <div style={styles.timeline}>
      {NODE_ORDER.map((node) => (
        <AgentCard
          key={node}
          label={NODE_LABELS[node]}
          state={research.nodes[node]}
        />
      ))}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  timeline: {
    display: "flex",
    flexDirection: "column",
    gap: "8px",
  },
};
