import { AgentCard } from "./AgentCard";
import type { ResearchState, NodeName } from "../types";
import { NODE_LABELS } from "../types";

interface Props {
  research: ResearchState;
}

export function AgentTimeline({ research }: Props) {
  const { visibleNodes, nodes } = research;

  return (
    <div style={styles.timeline}>
      {visibleNodes.map((node) => (
        <AgentCard
          key={node}
          name={node}
          label={NODE_LABELS[node]}
          state={nodes[node]}
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
