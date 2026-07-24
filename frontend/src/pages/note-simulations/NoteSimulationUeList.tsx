import { BarChart3, ChevronsUp } from "lucide-react";
import { EmptyState } from "../../components/EmptyState";
import type { NoteSimulationAssessmentDraft, NoteSimulationUeDraft } from "../../lib/noteSimulations";
import type { NoteSimulationResolution } from "./NoteSimulationConflictPanel";
import { NoteSimulationUeCard } from "./NoteSimulationUeCard";

export function NoteSimulationUeList({
  ues,
  openUes,
  compact,
  disabled,
  emptyTitle,
  emptyDetail,
  onToggle,
  onCollapseAll,
  onEditUe,
  onEditAssessment,
  onAddAssessment,
  onResolveUe,
  onResolveAssessment,
}: {
  ues: NoteSimulationUeDraft[];
  openUes: Set<string>;
  compact: boolean;
  disabled: boolean;
  emptyTitle: string;
  emptyDetail: string;
  onToggle: (ue: NoteSimulationUeDraft) => void;
  onCollapseAll: () => void;
  onEditUe: (ue: NoteSimulationUeDraft) => void;
  onEditAssessment: (ue: NoteSimulationUeDraft, assessment: NoteSimulationAssessmentDraft) => void;
  onAddAssessment: (ue: NoteSimulationUeDraft) => void;
  onResolveUe: (ue: NoteSimulationUeDraft, resolution: NoteSimulationResolution) => void;
  onResolveAssessment: (assessment: NoteSimulationAssessmentDraft, resolution: NoteSimulationResolution) => void;
}) {
  return (
    <section className="note-workbench-ue-list" aria-labelledby="note-ue-list-title">
      <header className="note-ue-list-heading">
        <div>
          <span>Projection détaillée</span>
          <h3 id="note-ue-list-title">Unités d’enseignement</h3>
        </div>
        {!compact && openUes.size > 1 && (
          <button className="text-button" type="button" onClick={onCollapseAll}>
            <ChevronsUp size={16} />
            Tout replier
          </button>
        )}
      </header>
      {ues.length ? (
        <div className="note-ue-cards">
          {ues.map((ue) => (
            <NoteSimulationUeCard
              key={ue.clientKey}
              ue={ue}
              open={openUes.has(ue.clientKey)}
              disabled={disabled}
              onToggle={() => onToggle(ue)}
              onEditUe={() => onEditUe(ue)}
              onEditAssessment={(assessment) => onEditAssessment(ue, assessment)}
              onAddAssessment={() => onAddAssessment(ue)}
              onResolveUe={(resolution) => onResolveUe(ue, resolution)}
              onResolveAssessment={onResolveAssessment}
            />
          ))}
        </div>
      ) : (
        <EmptyState icon={<BarChart3 size={21} />} title={emptyTitle} detail={emptyDetail} />
      )}
    </section>
  );
}
