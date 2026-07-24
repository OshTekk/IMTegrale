import { ArrowLeftRight, Copy, EllipsisVertical, RotateCcw, Trash2 } from "lucide-react";
import { SimulationSaveIndicator } from "../../components/simulations/SimulationSaveIndicator";
import type { SimulationConfirmation } from "../../components/simulations/SimulationConfirmationModal";
import type { SimulationSaveState } from "../../components/simulations/SimulationSaveIndicator";
import { formatDate, relativeDate } from "../../lib/format";
import type { NoteSimulationScenarioSummary } from "../../types";

export function NoteSimulationHeader({
  scenario,
  name,
  saveState,
  valid,
  canCompare,
  actionPending,
  onNameChange,
  onCompare,
  onDuplicate,
  onConfirm,
}: {
  scenario: NoteSimulationScenarioSummary;
  name: string;
  saveState: SimulationSaveState;
  valid: boolean;
  canCompare: boolean;
  actionPending: boolean;
  onNameChange: (name: string) => void;
  onCompare: () => void;
  onDuplicate: () => void;
  onConfirm: (confirmation: SimulationConfirmation) => void;
}) {
  const actionsDisabled = saveState !== "saved" || actionPending;
  return (
    <header className="note-workbench-header">
      <div className="note-workbench-title">
        <label>
          <span className="sr-only">Nom de la simulation</span>
          <input
            value={name}
            onChange={(event) => onNameChange(event.target.value)}
            maxLength={80}
            disabled={saveState === "conflict"}
          />
        </label>
        <SimulationSaveIndicator state={saveState} valid={valid} />
        <small>
          {scenario.source_captured_at
            ? `Base PASS + COMPETENCES du ${formatDate(scenario.source_captured_at, false)}`
            : "Scénario manuel"}{" "}
          · modifié {relativeDate(scenario.updated_at)}
        </small>
      </div>
      <div className="note-workbench-header-actions">
        <button
          className="secondary-button"
          type="button"
          onClick={onCompare}
          disabled={!canCompare || saveState !== "saved"}
        >
          <ArrowLeftRight size={17} />
          Comparer
        </button>
        <details className="simulation-overflow">
          <summary className="icon-button" role="button" aria-label="Actions sur la simulation" title="Plus d’actions">
            <EllipsisVertical size={19} />
          </summary>
          <div>
            <button
              type="button"
              onClick={(event) => {
                event.currentTarget.closest("details")?.removeAttribute("open");
                onDuplicate();
              }}
              disabled={actionsDisabled}
            >
              <Copy size={16} /> Dupliquer
            </button>
            <button
              type="button"
              onClick={(event) => {
                event.currentTarget.closest("details")?.removeAttribute("open");
                onConfirm("reset");
              }}
              disabled={actionsDisabled}
            >
              <RotateCcw size={16} /> Réinitialiser
            </button>
            <button
              className="danger"
              type="button"
              onClick={(event) => {
                event.currentTarget.closest("details")?.removeAttribute("open");
                onConfirm("delete");
              }}
              disabled={actionsDisabled}
            >
              <Trash2 size={16} /> Supprimer
            </button>
          </div>
        </details>
      </div>
    </header>
  );
}
