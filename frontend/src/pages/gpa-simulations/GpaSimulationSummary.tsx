import { formatNumber } from "../../lib/format";
import type { DraftProjection } from "../../lib/simulations";
import type { SimulationSemester } from "../../types";

export type GpaSelectedProjection = {
  gpa: number | null;
  creditsIncluded: number;
  ueCount: number;
  gradedCount: number;
  pendingCount: number;
};

export function GpaSimulationSummary({
  global,
  selected,
  semester,
}: {
  global: DraftProjection;
  selected: GpaSelectedProjection;
  semester: "all" | SimulationSemester;
}) {
  const completion = selected.ueCount ? Math.round((selected.gradedCount / selected.ueCount) * 100) : 0;
  return (
    <section className="gpa-summary" aria-label="Projection du scénario">
      <div className="gpa-summary-primary">
        <span>{semester === "all" ? "GPA global simulé" : `GPA simulé · ${semester}`}</span>
        <strong>{formatNumber(selected.gpa)}</strong>
        <small>sur 4,00</small>
      </div>
      <div className="gpa-summary-metrics">
        <div>
          <span>ECTS pondérés</span>
          <strong>{formatNumber(selected.creditsIncluded)}</strong>
          <small>inclus dans le calcul</small>
        </div>
        <div>
          <span>UE gradées</span>
          <strong>
            {selected.gradedCount}
            <i>/{selected.ueCount}</i>
          </strong>
          <small>{completion} % complété</small>
        </div>
        <div>
          <span>En attente</span>
          <strong>{selected.pendingCount}</strong>
          <small>exclue{selected.pendingCount === 1 ? "" : "s"} du calcul</small>
        </div>
        <div>
          <span>Scénario complet</span>
          <strong>{global.completionRate} %</strong>
          <progress max="100" value={global.completionRate}>
            {global.completionRate} %
          </progress>
        </div>
      </div>
    </section>
  );
}
