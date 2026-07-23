import { BadgeCheck, RotateCcw } from "lucide-react";
import { Link } from "react-router-dom";
import { formatDate, formatNumber } from "../../lib/format";
import type { NoteItem, UeItem } from "../../types";

function sourceLabel(source: NoteItem["source"]): string {
  return source === "pass" ? "PASS" : "Import historique";
}

export function ResultsEvaluationItem({
  note,
  ue,
  returnSearch,
  showSource,
  showUeLink = true,
}: {
  note: NoteItem;
  ue: UeItem | undefined;
  returnSearch: string;
  showSource: boolean;
  showUeLink?: boolean;
}) {
  const detailUrl = `/results/ue/${encodeURIComponent(note.ue_code)}${returnSearch}`;
  const heading = (
    <>
      <span className="code-label">{note.ue_code}</span>
      <strong>{note.label}</strong>
    </>
  );

  return (
    <li className={`results-evaluation-item ${showUeLink ? "" : "without-ue-link"}`.trim()}>
      <div className="results-evaluation-main">
        <div className="results-evaluation-heading">
          {showUeLink ? (
            <Link to={detailUrl} viewTransition>
              {heading}
            </Link>
          ) : (
            <span className="results-evaluation-static">{heading}</span>
          )}
          {note.is_resit && (
            <span className="results-type-label resit">
              <RotateCcw size={14} aria-hidden="true" /> Rattrapage
            </span>
          )}
        </div>
        <p>{ue?.title || "Intitulé de l'UE indisponible"}</p>
        <div className="results-evaluation-meta">
          <span>Coefficient {formatNumber(note.coefficient)}</span>
          <span>Importée le {formatDate(note.detected_at, false)}</span>
          {showSource && (
            <span>
              <BadgeCheck size={14} aria-hidden="true" /> {sourceLabel(note.source)}
            </span>
          )}
        </div>
      </div>
      <strong
        className={`results-score ${note.score >= 10 ? "success" : "danger"}`}
        aria-label={`Note ${formatNumber(note.score)} sur 20`}
      >
        {formatNumber(note.score)}
        <small>/20</small>
      </strong>
      {showUeLink && (
        <Link className="results-evaluation-link" to={detailUrl} viewTransition>
          Voir l'UE
        </Link>
      )}
    </li>
  );
}
