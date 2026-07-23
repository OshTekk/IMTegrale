import { BadgeCheck, CheckCircle2, ChevronDown, CircleDashed, ExternalLink, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { GradeBadge } from "../../components/GradeBadge";
import { formatDate, formatNumber, yearLabel } from "../../lib/format";
import type { NoteItem, UeItem } from "../../types";
import { ResultsEvaluationItem } from "./ResultsEvaluationItem";

export function ResultsUeStatus({ ue }: { ue: UeItem }) {
  if (ue.grade === null && ue.average === null) {
    return (
      <span className="status-pill neutral">
        <CircleDashed size={14} aria-hidden="true" /> En attente
      </span>
    );
  }
  if (ue.grade === "E") {
    return (
      <span className="status-pill success">
        <CheckCircle2 size={14} aria-hidden="true" /> Validée après rattrapage
      </span>
    );
  }
  if (ue.grade === "FX" || ue.grade === "F") {
    return (
      <span className="status-pill warning">
        <TriangleAlert size={14} aria-hidden="true" /> Rattrapage requis
      </span>
    );
  }
  if (ue.validated) {
    return (
      <span className="status-pill success">
        <CheckCircle2 size={14} aria-hidden="true" /> Validée
      </span>
    );
  }
  return <span className="status-pill danger">Non validée</span>;
}

export function gradeSourceLabel(source: UeItem["grade_source"]): string {
  if (source === "competences") return "Grade COMPETENCES";
  if (source === "pass_calculated") return "Grade calculé depuis PASS";
  return "Grade calculé";
}

export function ResultsUeCard({
  ue,
  notes,
  returnSearch,
  showEvaluationSource,
}: {
  ue: UeItem;
  notes: readonly NoteItem[];
  returnSearch: string;
  showEvaluationSource: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const safeCode = ue.code.replace(/[^A-Za-z0-9_-]/g, "-");
  const detailsId = `results-ue-evaluations-${safeCode}`;
  const detailUrl = `/results/ue/${encodeURIComponent(ue.code)}${returnSearch}`;
  const credits =
    ue.credits_ects === null
      ? "Indisponibles"
      : ue.earned_credits_ects === null
        ? `${formatNumber(ue.credits_ects)} alloués`
        : `${formatNumber(ue.earned_credits_ects)} / ${formatNumber(ue.credits_ects)}`;

  return (
    <article className={`results-ue-card ${ue.credits_ects === null ? "needs-data" : ""}`}>
      <header>
        <div className="results-ue-identity">
          <div>
            <span className="code-label">{ue.code}</span>
            <span>{ue.semester ?? yearLabel(ue.year)}</span>
          </div>
          <h5>{ue.title || ue.code}</h5>
          {ue.official_code && <small>{ue.official_code}</small>}
        </div>
        <ResultsUeStatus ue={ue} />
      </header>

      <dl className="results-ue-metrics">
        <div>
          <dt>Moyenne PASS</dt>
          <dd>{formatNumber(ue.average, " /20")}</dd>
        </div>
        <div>
          <dt>Grade</dt>
          <dd>
            <GradeBadge grade={ue.grade} description={ue.grade_description} />
            <span className="sr-only">{gradeSourceLabel(ue.grade_source)}</span>
          </dd>
        </div>
        <div>
          <dt>GPA</dt>
          <dd>{formatNumber(ue.gpa, " /4")}</dd>
        </div>
        <div>
          <dt>ECTS obtenus / alloués</dt>
          <dd className={ue.credits_ects === null ? "missing-value" : undefined}>
            {ue.credits_ects === null && <TriangleAlert size={15} aria-hidden="true" />}
            {credits}
          </dd>
        </div>
      </dl>

      <div className="results-ue-provenance">
        <span>
          <BadgeCheck size={15} aria-hidden="true" /> {gradeSourceLabel(ue.grade_source)}
        </span>
        {ue.metadata_refreshed_at && <span>Actualisée le {formatDate(ue.metadata_refreshed_at, false)}</span>}
        <span>
          {notes.length} évaluation{notes.length > 1 ? "s" : ""}
        </span>
      </div>

      <footer>
        <Link className="secondary-button" to={detailUrl} viewTransition>
          <ExternalLink size={16} aria-hidden="true" /> Voir l'UE
        </Link>
        <button
          className="results-expand-button"
          type="button"
          onClick={() => setExpanded((current) => !current)}
          aria-expanded={expanded}
          aria-controls={detailsId}
        >
          <span>{expanded ? "Masquer les évaluations" : "Voir les évaluations"}</span>
          <ChevronDown className={expanded ? "is-open" : undefined} size={18} aria-hidden="true" />
        </button>
      </footer>

      <div className="results-ue-details" id={detailsId} hidden={!expanded}>
        {notes.length ? (
          <ul className="results-evaluation-list">
            {notes.map((note) => (
              <ResultsEvaluationItem
                key={note.id}
                note={note}
                ue={ue}
                returnSearch={returnSearch}
                showSource={showEvaluationSource}
              />
            ))}
          </ul>
        ) : (
          <p>Aucune évaluation détaillée n'est disponible pour cette UE.</p>
        )}
      </div>
    </article>
  );
}
