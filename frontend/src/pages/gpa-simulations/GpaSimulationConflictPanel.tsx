import { TriangleAlert } from "lucide-react";
import { formatNumber } from "../../lib/format";
import type { SimulationDraftEntry } from "../../lib/simulations";

export function GpaSimulationConflictPanel({
  entry,
  disabled,
  pending,
  onResolve,
}: {
  entry: SimulationDraftEntry;
  disabled: boolean;
  pending: boolean;
  onResolve: (resolution: "source" | "simulation") => void;
}) {
  const baseline = entry.server?.baseline;
  if (!baseline || entry.server?.source?.status !== "conflict" || !entry.id) return null;
  return (
    <section className="gpa-source-conflict" aria-label="Conflit avec la source officielle">
      <header>
        <TriangleAlert size={18} />
        <div>
          <strong>La source officielle a changé</strong>
          <p>Choisis explicitement la valeur à conserver dans cette simulation.</p>
        </div>
      </header>
      <div className="gpa-conflict-values">
        <div>
          <span>Valeur officielle</span>
          <strong>{baseline.grade ?? "Grade en attente"}</strong>
          <small>{formatNumber(baseline.credits_ects, " ECTS")}</small>
        </div>
        <div>
          <span>Hypothèse simulée</span>
          <strong>{entry.grade ?? "Grade en attente"}</strong>
          <small>{entry.credits_ects ? `${entry.credits_ects} ECTS` : "ECTS non renseignés"}</small>
        </div>
      </div>
      <div className="gpa-conflict-actions">
        <button
          type="button"
          className="secondary-button"
          onClick={() => onResolve("source")}
          disabled={disabled || pending}
        >
          Utiliser la source
        </button>
        <button
          type="button"
          className="primary-button"
          onClick={() => onResolve("simulation")}
          disabled={disabled || pending}
        >
          Garder l’hypothèse
        </button>
      </div>
    </section>
  );
}
