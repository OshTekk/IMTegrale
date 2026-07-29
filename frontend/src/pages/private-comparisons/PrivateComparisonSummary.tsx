import { CalendarClock, GraduationCap } from "lucide-react";
import type { PrivateComparisonParticipantResponse } from "../../generated/api/types.gen";
import { formatDate, formatNumber } from "../../lib/format";
import { freshnessLabel, PRIVATE_COMPARISON_GRADES } from "./privateComparisonPresentation";

function ParticipantSummary({
  participant,
  label,
}: {
  participant: PrivateComparisonParticipantResponse;
  label: string;
}) {
  const summary = participant.summary;
  return (
    <article className="private-comparison-participant">
      <header>
        <span>{label}</span>
        <h3>{participant.identity.official_name}</h3>
      </header>
      <dl className="private-comparison-metrics">
        <div>
          <dt>Moyenne générale</dt>
          <dd>{formatNumber(summary.average, " / 20")}</dd>
        </div>
        <div>
          <dt>GPA général</dt>
          <dd>{formatNumber(summary.gpa, " / 4")}</dd>
        </div>
        <div>
          <dt>ECTS validés</dt>
          <dd>{formatNumber(summary.validated_ects)}</dd>
        </div>
        <div>
          <dt>UE prises en compte</dt>
          <dd>{summary.ue_count}</dd>
        </div>
      </dl>
      <div className="private-comparison-freshness">
        <CalendarClock size={17} aria-hidden="true" />
        <span>
          <strong>{freshnessLabel(summary.freshness)}</strong>
          <small>Vérifiée le {formatDate(summary.academic_verified_at, false)}</small>
        </span>
      </div>
      <section className="private-comparison-grades" aria-label={`Répartition des grades de ${label}`}>
        <h4>Répartition des grades</h4>
        <dl>
          {PRIVATE_COMPARISON_GRADES.map((grade) => (
            <div key={grade}>
              <dt>{grade}</dt>
              <dd>{summary.grade_distribution[grade] ?? 0}</dd>
            </div>
          ))}
        </dl>
      </section>
    </article>
  );
}

export function PrivateComparisonSummary({
  current,
  other,
}: {
  current: PrivateComparisonParticipantResponse;
  other: PrivateComparisonParticipantResponse;
}) {
  return (
    <section className="private-comparison-detail-section" aria-labelledby="private-comparison-summary-title">
      <header className="private-comparison-section-heading">
        <GraduationCap size={22} aria-hidden="true" />
        <div>
          <h2 id="private-comparison-summary-title">Résumé général</h2>
          <p>Les mêmes indicateurs officiels sont présentés de chaque côté, sans classement.</p>
        </div>
      </header>
      <div className="private-comparison-participants">
        <ParticipantSummary participant={current} label="Toi" />
        <ParticipantSummary participant={other} label="Autre participant" />
      </div>
    </section>
  );
}
