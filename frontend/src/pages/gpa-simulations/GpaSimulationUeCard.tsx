import { AlertTriangle, ChevronRight } from "lucide-react";
import { GradeBadge } from "../../components/GradeBadge";
import { formatNumber } from "../../lib/format";
import { gradePoints, type SimulationDraftEntry } from "../../lib/simulations";
import {
  accessibleEntryLabel,
  domSafeKey,
  entryHasConflict,
  entryIsIncomplete,
  entryNature,
  sourceLabel,
} from "./gpaSimulationPresentation";
import { GpaSimulationNaturePill } from "./GpaSimulationNaturePill";

export function GpaSimulationUeCard({
  entry,
  selected,
  compact,
  disabled,
  onOpen,
}: {
  entry: SimulationDraftEntry;
  selected: boolean;
  compact: boolean;
  disabled: boolean;
  onOpen: () => void;
}) {
  const safeKey = domSafeKey(entry.clientKey);
  const conflict = entryHasConflict(entry);
  const incomplete = entryIsIncomplete(entry);
  const sourceStatus = entry.server?.source?.status;
  const provenance = sourceLabel(entry);
  return (
    <article
      className={`gpa-ue-card nature-${entryNature(entry)}${selected ? " is-selected" : ""}${conflict ? " has-conflict" : ""}${incomplete ? " is-incomplete" : ""}`}
    >
      <button
        id={`gpa-ue-trigger-${safeKey}`}
        type="button"
        className="gpa-ue-trigger"
        aria-label={accessibleEntryLabel(entry)}
        aria-expanded={selected}
        aria-controls={!compact && selected ? `gpa-ue-editor-${safeKey}` : undefined}
        aria-haspopup={compact ? "dialog" : undefined}
        onClick={onOpen}
        disabled={disabled}
      >
        <span className="gpa-ue-identity">
          <span className="gpa-ue-semester">{entry.semester ?? "—"}</span>
          <span>
            <strong>{entry.title || entry.ue_code || "UE à compléter"}</strong>
            <small>{entry.ue_code || "Code libre"}</small>
          </span>
        </span>
        <span className="gpa-ue-grade">
          <small>Grade</small>
          <GradeBadge grade={entry.grade} />
        </span>
        <span className="gpa-ue-points">
          <small>Points GPA</small>
          <strong>{formatNumber(gradePoints(entry.grade))}</strong>
        </span>
        <span className="gpa-ue-credits">
          <small>ECTS</small>
          <strong>{entry.credits_ects ? formatNumber(Number(entry.credits_ects)) : "—"}</strong>
        </span>
        <span className="gpa-ue-status">
          <GpaSimulationNaturePill nature={entryNature(entry)} />
          {conflict && (
            <small className="is-conflict">
              <AlertTriangle size={13} /> Conflit à résoudre
            </small>
          )}
          {sourceStatus === "unavailable" && (
            <small className="is-unavailable">
              <AlertTriangle size={13} /> Source indisponible
            </small>
          )}
          {incomplete && !conflict && <small className="is-incomplete">Informations à compléter</small>}
          {provenance && (
            <small className="gpa-source-label" title={provenance}>
              {provenance}
            </small>
          )}
        </span>
        <ChevronRight className="gpa-ue-chevron" size={20} aria-hidden="true" />
      </button>
    </article>
  );
}
