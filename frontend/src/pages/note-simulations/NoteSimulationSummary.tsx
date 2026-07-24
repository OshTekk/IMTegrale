import { CheckCircle2, CircleDashed } from "lucide-react";
import { formatNumber } from "../../lib/format";

interface ProjectionSummary {
  average: number | null;
  gpa: number | null;
  creditsIncluded: number;
  ueCount: number;
  calculatedUeCount: number;
  assessmentCount: number;
  scoredCount: number;
  pendingCount: number;
}

export function NoteSimulationSummary({
  projection,
  completionRate,
  semester,
}: {
  projection: ProjectionSummary;
  completionRate: number;
  semester: string;
}) {
  return (
    <section className="note-workbench-summary" aria-label="Projection du scénario">
      <div className="note-summary-primary">
        <span>{semester === "all" ? "Moyenne générale simulée" : `Moyenne simulée · ${semester}`}</span>
        <strong>{formatNumber(projection.average)}</strong>
        <small>sur 20</small>
      </div>
      <dl className="note-summary-metrics">
        <div>
          <dt>GPA potentiel</dt>
          <dd>
            <span className="note-summary-metric-value">{formatNumber(projection.gpa)}</span>
            <small>sur 4,00</small>
          </dd>
        </div>
        <div>
          <dt>UE calculées</dt>
          <dd>
            <span className="note-summary-metric-value">
              {projection.calculatedUeCount}
              <i>/{projection.ueCount}</i>
            </span>
            <small>{formatNumber(projection.creditsIncluded)} ECTS pondérés</small>
          </dd>
        </div>
        <div>
          <dt>Notes en attente</dt>
          <dd>
            <span className="note-summary-metric-value">{projection.pendingCount}</span>
            <small>
              {projection.scoredCount}/{projection.assessmentCount} renseignées
            </small>
          </dd>
        </div>
        <div>
          <dt>Progression</dt>
          <dd>
            <span className="note-summary-metric-value note-summary-progress-value">
              {completionRate} %
              {completionRate === 100 ? (
                <CheckCircle2 size={17} aria-hidden="true" />
              ) : (
                <CircleDashed size={17} aria-hidden="true" />
              )}
            </span>
            <progress value={completionRate} max="100" aria-label={`Simulation complétée à ${completionRate} %`} />
          </dd>
        </div>
      </dl>
    </section>
  );
}
