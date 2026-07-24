import { FlaskConical } from "lucide-react";
import { EmptyState } from "../../components/EmptyState";
import type { SimulationDraftEntry } from "../../lib/simulations";
import { GpaSimulationUeCard } from "./GpaSimulationUeCard";

export function GpaSimulationUeList({
  entries,
  selectedKey,
  compact,
  disabled,
  emptyTitle,
  emptyDetail,
  onOpen,
}: {
  entries: SimulationDraftEntry[];
  selectedKey: string | null;
  compact: boolean;
  disabled: boolean;
  emptyTitle: string;
  emptyDetail: string;
  onOpen: (entry: SimulationDraftEntry) => void;
}) {
  if (!entries.length) {
    return (
      <div className="gpa-ue-empty">
        <EmptyState icon={<FlaskConical size={20} />} title={emptyTitle} detail={emptyDetail} />
      </div>
    );
  }
  return (
    <div className="gpa-ue-list" aria-label="Unités d’enseignement du scénario">
      {entries.map((entry) => (
        <GpaSimulationUeCard
          key={entry.clientKey}
          entry={entry}
          selected={selectedKey === entry.clientKey}
          compact={compact}
          disabled={disabled}
          onOpen={() => onOpen(entry)}
        />
      ))}
    </div>
  );
}
