import { FlaskConical, Info } from "lucide-react";
import type { SimulationConfirmation } from "../../components/simulations/SimulationConfirmationModal";
import type { SimulationSaveState } from "../../components/simulations/SimulationSaveIndicator";
import type { DraftProjection, SimulationDraft, SimulationDraftEntry } from "../../lib/simulations";
import type { SimulationScenarioSummary, SimulationSemester } from "../../types";
import { GpaSimulationFormula } from "./GpaSimulationFormula";
import { GpaSimulationHeader } from "./GpaSimulationHeader";
import { GpaSimulationSemesterFilter } from "./GpaSimulationSemesterFilter";
import { GpaSimulationSummary, type GpaSelectedProjection } from "./GpaSimulationSummary";
import { GpaSimulationUeEditor } from "./GpaSimulationUeEditor";
import { GpaSimulationUeList } from "./GpaSimulationUeList";
import type { GpaSimulationEditorState } from "./gpaSimulationState";

export function GpaSimulationWorkbench({
  scenario,
  draft,
  saveState,
  validDraft,
  canCompare,
  compact,
  semester,
  semesters,
  projection,
  selectedProjection,
  visibleEntries,
  editor,
  editedEntry,
  editorDisabled,
  actionPending,
  conflictPending,
  onNameChange,
  onCompare,
  onDuplicate,
  onConfirm,
  onSemesterChange,
  onAdd,
  onOpen,
  onCloseEditor,
  onApplyEntry,
  onDeleteEntry,
  onResolve,
}: {
  scenario: SimulationScenarioSummary;
  draft: SimulationDraft;
  saveState: SimulationSaveState;
  validDraft: boolean;
  canCompare: boolean;
  compact: boolean;
  semester: "all" | SimulationSemester;
  semesters: SimulationSemester[];
  projection: DraftProjection;
  selectedProjection: GpaSelectedProjection;
  visibleEntries: SimulationDraftEntry[];
  editor: GpaSimulationEditorState;
  editedEntry: SimulationDraftEntry | null;
  editorDisabled: boolean;
  actionPending: boolean;
  conflictPending: boolean;
  onNameChange: (name: string) => void;
  onCompare: () => void;
  onDuplicate: () => void;
  onConfirm: (confirmation: SimulationConfirmation) => void;
  onSemesterChange: (semester: "all" | SimulationSemester) => void;
  onAdd: () => void;
  onOpen: (entry: SimulationDraftEntry) => void;
  onCloseEditor: () => void;
  onApplyEntry: (entry: SimulationDraftEntry) => void;
  onDeleteEntry: (entry: SimulationDraftEntry) => void;
  onResolve: (entry: SimulationDraftEntry, resolution: "source" | "simulation") => void;
}) {
  const editorProps = {
    open: true,
    entry: editedEntry,
    defaultSemester: semester === "all" ? null : semester,
    disabled: editorDisabled,
    conflictPending,
    onClose: onCloseEditor,
    onApply: onApplyEntry,
    onDelete: editedEntry ? onDeleteEntry : undefined,
    onResolve,
  };

  return (
    <section className="gpa-workbench">
      <GpaSimulationHeader
        scenario={scenario}
        name={draft.name}
        saveState={saveState}
        valid={validDraft}
        canCompare={canCompare}
        actionPending={actionPending}
        onNameChange={onNameChange}
        onCompare={onCompare}
        onDuplicate={onDuplicate}
        onConfirm={onConfirm}
      />
      <GpaSimulationSummary global={projection} selected={selectedProjection} semester={semester} />
      <GpaSimulationSemesterFilter
        semester={semester}
        semesters={semesters}
        compact={compact}
        visibleCount={visibleEntries.length}
        disabled={editorDisabled}
        limitReached={draft.entries.length >= 120}
        onChange={onSemesterChange}
        onAdd={onAdd}
      />

      <div className="gpa-editor-workspace">
        <GpaSimulationUeList
          entries={visibleEntries}
          selectedKey={editor?.mode === "edit" ? editor.entryKey : null}
          compact={compact}
          disabled={false}
          emptyTitle={semester === "all" ? "Scénario vide" : `Aucune UE en ${semester}`}
          emptyDetail={
            semester === "all"
              ? "Ajoute une UE pour commencer ta projection."
              : "Ajoute une UE, elle sera directement placée dans ce semestre."
          }
          onOpen={onOpen}
        />
        {!compact &&
          (editor ? (
            <GpaSimulationUeEditor {...editorProps} compact={false} />
          ) : (
            <aside className="gpa-editor-placeholder">
              <FlaskConical size={22} />
              <strong>Sélectionne une UE</strong>
              <p>Ses hypothèses s’ouvrent ici sans allonger toute la page.</p>
            </aside>
          ))}
      </div>

      <div className="gpa-workbench-note">
        <Info size={15} />
        Une UE sans grade ou sans ECTS est exclue du calcul, jamais comptée comme zéro.
      </div>
      <GpaSimulationFormula version={scenario.formula_version} />
      {compact && editor && <GpaSimulationUeEditor {...editorProps} compact />}
    </section>
  );
}
