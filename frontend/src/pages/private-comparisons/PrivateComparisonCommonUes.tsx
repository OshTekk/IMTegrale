import { BookOpenCheck } from "lucide-react";
import type { PrivateComparisonCommonUeResponse, PrivateComparisonUeSideResponse } from "../../generated/api/types.gen";
import { formatDate, formatNumber, yearLabel } from "../../lib/format";
import { freshnessLabel, sortCommonUes } from "./privateComparisonPresentation";

function UeSide({ label, value }: { label: string; value: PrivateComparisonUeSideResponse }) {
  return (
    <section className="private-comparison-ue-side" aria-label={label}>
      <h4>{label}</h4>
      <p className="private-comparison-ue-title">{value.title}</p>
      <dl>
        <div>
          <dt>Moyenne</dt>
          <dd>{formatNumber(value.average, " / 20")}</dd>
        </div>
        <div>
          <dt>Grade</dt>
          <dd>{value.grade ?? "Non attribué"}</dd>
        </div>
        <div>
          <dt>GPA</dt>
          <dd>{formatNumber(value.gpa, " / 4")}</dd>
        </div>
        <div>
          <dt>ECTS</dt>
          <dd>
            {formatNumber(value.earned_ects)} / {formatNumber(value.allocated_ects)}
          </dd>
        </div>
        <div>
          <dt>Validation</dt>
          <dd>{value.validated ? "Validée" : "Non validée"}</dd>
        </div>
        <div>
          <dt>Fraîcheur</dt>
          <dd>{freshnessLabel(value.freshness)}</dd>
        </div>
      </dl>
      <small>Vérifiée le {formatDate(value.verified_at, false)}</small>
    </section>
  );
}

function CommonUeCard({ value }: { value: PrivateComparisonCommonUeResponse }) {
  const semester = value.current.semester ?? value.other.semester;
  const year = value.current.year || value.other.year;
  return (
    <article className="private-comparison-ue-card">
      <header>
        <div>
          <span className="private-comparison-ue-code">{value.official_code}</span>
          <h3>{value.current.title || value.other.title}</h3>
        </div>
        <p>
          {yearLabel(year)}
          {semester ? ` · ${semester}` : ""}
        </p>
      </header>
      <div className="private-comparison-ue-sides">
        <UeSide label="Toi" value={value.current} />
        <UeSide label="Autre participant" value={value.other} />
      </div>
    </article>
  );
}

export function PrivateComparisonCommonUes({ values }: { values: readonly PrivateComparisonCommonUeResponse[] }) {
  const sorted = sortCommonUes(values);
  return (
    <section className="private-comparison-detail-section" aria-labelledby="private-comparison-ues-title">
      <header className="private-comparison-section-heading">
        <BookOpenCheck size={22} aria-hidden="true" />
        <div>
          <h2 id="private-comparison-ues-title">UE communes</h2>
          <p>Seules les UE reconnues par le même identifiant officiel sont affichées.</p>
        </div>
      </header>
      {sorted.length ? (
        <div className="private-comparison-ue-list">
          {sorted.map((value) => (
            <CommonUeCard key={value.official_code} value={value} />
          ))}
        </div>
      ) : (
        <p className="private-comparison-empty-copy">Aucune UE commune n’est disponible pour le moment.</p>
      )}
    </section>
  );
}
