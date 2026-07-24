import { Plus } from "lucide-react";
import { formatNumber } from "../../lib/format";
import type { NoteSimulationScenarioSummary } from "../../types";
import type { SimulationSaveState } from "../../components/simulations/SimulationSaveIndicator";

function scenarioOptionLabel(item: NoteSimulationScenarioSummary): string {
  const average = item.result.average === null ? "moyenne indisponible" : `${formatNumber(item.result.average)}/20`;
  return `${item.name} · ${average} · ${item.result.ue_count} UE`;
}

export function NoteSimulationScenarioSelector({
  scenarios,
  activeId,
  compact,
  saveState,
  limit,
  activeAverage,
  activeUeCount,
  onSelect,
  onCreate,
}: {
  scenarios: NoteSimulationScenarioSummary[];
  activeId: string | null;
  compact: boolean;
  saveState: SimulationSaveState;
  limit: number;
  activeAverage: number | null;
  activeUeCount: number;
  onSelect: (id: string) => void;
  onCreate: () => void;
}) {
  const active = scenarios.find((item) => item.id === activeId);
  const selectionLocked = saveState !== "saved";
  const limitReached = scenarios.length >= limit;

  return (
    <section className="note-workbench-scenarios" aria-label="Scénarios de notes">
      {compact ? (
        <label className="note-scenario-select">
          <span>Scénario actif</span>
          <select
            value={activeId ?? ""}
            onChange={(event) => onSelect(event.target.value)}
            disabled={selectionLocked}
            aria-describedby="note-scenario-selection-help"
          >
            {scenarios.map((item) => (
              <option key={item.id} value={item.id}>
                {scenarioOptionLabel(item)}
              </option>
            ))}
          </select>
          <small id="note-scenario-selection-help">
            <strong>{formatNumber(activeAverage)}/20</strong>
            <span>
              {activeUeCount} UE · {saveState === "saved" ? "enregistré" : "changement verrouillé"}
            </span>
          </small>
        </label>
      ) : (
        <div className="note-scenario-tabs" role="tablist" aria-label="Choisir une simulation">
          {scenarios.map((item) => {
            const isActive = item.id === activeId;
            const average = isActive ? activeAverage : item.result.average;
            const ueCount = isActive ? activeUeCount : item.result.ue_count;
            return (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={isActive}
                className={isActive ? "active" : ""}
                onClick={() => onSelect(item.id)}
                disabled={!isActive && selectionLocked}
                title={!isActive && selectionLocked ? "Attends la fin de l’enregistrement" : undefined}
              >
                <span>{item.name}</span>
                <small>
                  {average === null ? "Moyenne —" : `${formatNumber(average)}/20`} · {ueCount} UE
                </small>
                {item.rebase_available && <i aria-label="Source actualisée" title="Source actualisée" />}
              </button>
            );
          })}
        </div>
      )}
      <button
        className="secondary-button note-create-scenario"
        type="button"
        onClick={onCreate}
        disabled={limitReached || selectionLocked}
        title={limitReached ? "Limite de cinq simulations de notes atteinte" : undefined}
      >
        <Plus size={17} />
        <span>Nouveau scénario</span>
      </button>
      <span className="note-scenario-limit" aria-label={`${scenarios.length} scénarios sur ${limit}`}>
        {scenarios.length}/{limit}
      </span>
      {active?.rebase_available && <span className="sr-only">La source du scénario actif a évolué.</span>}
    </section>
  );
}
