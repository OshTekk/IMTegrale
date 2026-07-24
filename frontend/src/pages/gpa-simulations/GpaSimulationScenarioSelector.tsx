import { Plus } from "lucide-react";
import type { SimulationSaveState } from "../../components/simulations/SimulationSaveIndicator";
import { formatNumber } from "../../lib/format";
import type { SimulationScenarioSummary } from "../../types";

function optionLabel(item: SimulationScenarioSummary): string {
  const gpa = item.result.gpa === null ? "GPA indisponible" : `GPA ${formatNumber(item.result.gpa)}`;
  return `${item.name} · ${gpa} · ${item.result.ue_count} UE`;
}

export function GpaSimulationScenarioSelector({
  scenarios,
  activeId,
  compact,
  saveState,
  limit,
  activeGpa,
  activeUeCount,
  onSelect,
  onCreate,
}: {
  scenarios: SimulationScenarioSummary[];
  activeId: string | null;
  compact: boolean;
  saveState: SimulationSaveState;
  limit: number;
  activeGpa: number | null;
  activeUeCount: number;
  onSelect: (id: string) => void;
  onCreate: () => void;
}) {
  const locked = saveState !== "saved";
  const limitReached = scenarios.length >= limit;
  return (
    <section className="gpa-scenarios" aria-label="Scénarios GPA">
      {compact ? (
        <label className="gpa-scenario-select">
          <span>Scénario actif</span>
          <select
            value={activeId ?? ""}
            onChange={(event) => onSelect(event.target.value)}
            disabled={locked}
            aria-describedby="gpa-scenario-help"
          >
            {scenarios.map((item) => (
              <option key={item.id} value={item.id}>
                {optionLabel(item)}
              </option>
            ))}
          </select>
          <small id="gpa-scenario-help">
            <strong>GPA {formatNumber(activeGpa)}</strong>
            <span>
              {activeUeCount} UE · {locked ? "changement verrouillé" : "enregistré"}
            </span>
          </small>
        </label>
      ) : (
        <div className="gpa-scenario-tabs" role="tablist" aria-label="Choisir une simulation GPA">
          {scenarios.map((item) => {
            const active = item.id === activeId;
            return (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={active}
                className={active ? "active" : ""}
                onClick={() => onSelect(item.id)}
                disabled={!active && locked}
                title={!active && locked ? "Attends la fin de l’enregistrement" : undefined}
              >
                <span title={item.name}>{item.name}</span>
                <small>
                  GPA {formatNumber(active ? activeGpa : item.result.gpa)} ·{" "}
                  {active ? activeUeCount : item.result.ue_count} UE
                </small>
                {item.rebase_available && <i aria-label="Source actualisée" title="Source actualisée" />}
              </button>
            );
          })}
        </div>
      )}
      <button
        className="secondary-button gpa-create-scenario"
        type="button"
        onClick={onCreate}
        disabled={locked || limitReached}
        title={limitReached ? "Limite de cinq simulations atteinte" : undefined}
      >
        <Plus size={17} />
        Nouveau scénario
      </button>
      <span className="gpa-scenario-limit" aria-label={`${scenarios.length} scénarios sur ${limit}`}>
        {scenarios.length}/{limit}
      </span>
    </section>
  );
}
