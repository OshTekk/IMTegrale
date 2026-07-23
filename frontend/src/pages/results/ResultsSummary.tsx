import { BookOpenCheck, ClipboardList, FileDown, Gauge, GraduationCap } from "lucide-react";
import { Link } from "react-router-dom";
import { formatNumber } from "../../lib/format";
import type { Dashboard } from "../../types";

export function ResultsSummary({
  summary,
  canDownloadReport,
}: {
  summary: Dashboard["summary"];
  canDownloadReport: boolean;
}) {
  return (
    <section className="results-summary" aria-label="Synthèse des résultats">
      <div className="results-summary-heading">
        <div>
          <span className="section-kicker">Situation académique</span>
          <h2>Résultats officiels</h2>
        </div>
        {canDownloadReport && (
          <Link className="secondary-button" to="/ues/releve" viewTransition>
            <FileDown size={17} /> Relevé académique
          </Link>
        )}
      </div>
      <dl>
        <div>
          <dt>
            <Gauge size={17} /> Moyenne générale
          </dt>
          <dd>{formatNumber(summary.average, " /20")}</dd>
        </div>
        <div>
          <dt>
            <GraduationCap size={18} /> GPA général
          </dt>
          <dd>{formatNumber(summary.gpa, " /4")}</dd>
        </div>
        <div>
          <dt>
            <BookOpenCheck size={17} /> ECTS validés
          </dt>
          <dd>{formatNumber(summary.validated_credits)}</dd>
        </div>
        <div>
          <dt>Unités d'enseignement</dt>
          <dd>{summary.ue_count}</dd>
        </div>
        <div>
          <dt>
            <ClipboardList size={17} /> Évaluations
          </dt>
          <dd>{summary.note_count}</dd>
        </div>
      </dl>
    </section>
  );
}
