import { AlertTriangle, ChevronDown, Edit3, Info } from "lucide-react";
import { GradeBadge } from "../../components/GradeBadge";
import { formatDate, formatNumber } from "../../lib/format";
import {
  calculateNoteUeProjection,
  type NoteSimulationAssessmentDraft,
  type NoteSimulationUeDraft,
} from "../../lib/noteSimulations";
import { NoteSimulationAssessmentList } from "./NoteSimulationAssessmentList";
import { NoteSimulationConflictPanel, type NoteSimulationResolution } from "./NoteSimulationConflictPanel";
import { NoteSimulationNaturePill } from "./NoteSimulationNaturePill";
import { domSafeKey, hasUeConflict, ueNature } from "./noteSimulationPresentation";

function accessibleUeLabel(ue: NoteSimulationUeDraft, average: number | null, scored: number, total: number): string {
  const name = ue.title || ue.ue_code || "UE à compléter";
  const result = average === null ? "moyenne en attente" : `moyenne ${formatNumber(average)} sur 20`;
  return `Modifier ${name}, ${scored} évaluations renseignées sur ${total}, ${result}`;
}

export function NoteSimulationUeCard({
  ue,
  open,
  disabled,
  onToggle,
  onEditUe,
  onEditAssessment,
  onAddAssessment,
  onResolveUe,
  onResolveAssessment,
}: {
  ue: NoteSimulationUeDraft;
  open: boolean;
  disabled: boolean;
  onToggle: () => void;
  onEditUe: () => void;
  onEditAssessment: (assessment: NoteSimulationAssessmentDraft) => void;
  onAddAssessment: () => void;
  onResolveUe: (resolution: NoteSimulationResolution) => void;
  onResolveAssessment: (assessment: NoteSimulationAssessmentDraft, resolution: NoteSimulationResolution) => void;
}) {
  const projection = calculateNoteUeProjection(ue);
  const nature = ueNature(ue);
  const sourceStatus = ue.server?.source?.status;
  const conflict = hasUeConflict(ue);
  const safeKey = domSafeKey(ue.clientKey);
  const panelId = `note-ue-panel-${safeKey}`;
  const headingId = `note-ue-heading-${safeKey}`;
  const displayName = ue.title || ue.ue_code || "UE à compléter";

  return (
    <article
      className={`note-workbench-ue nature-${nature}${open ? " is-open" : ""}${conflict ? " has-conflict" : ""}`}
    >
      <button
        id={`note-ue-trigger-${safeKey}`}
        className="note-ue-trigger"
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={accessibleUeLabel(ue, projection.average, projection.scoredCount, projection.assessmentCount)}
        onClick={onToggle}
      >
        <span className="note-ue-identity">
          <span className="note-ue-semester">{ue.semester ?? "—"}</span>
          <span>
            <strong id={headingId}>{displayName}</strong>
            <small>
              {ue.ue_code || "Code libre"} ·{" "}
              {ue.credits_ects ? `${formatNumber(Number(ue.credits_ects))} ECTS` : "ECTS à renseigner"}
            </small>
          </span>
        </span>
        <span className="note-ue-result">
          <small>Moyenne</small>
          <strong>
            {formatNumber(projection.average)}
            <i>/20</i>
          </strong>
        </span>
        <span className="note-ue-grade">
          <GradeBadge grade={projection.grade} />
          <small>{projection.grade ? `${formatNumber(projection.gpaPoints)} GPA` : "en attente"}</small>
        </span>
        <span className="note-ue-completion">
          <strong>
            {projection.scoredCount}/{projection.assessmentCount}
          </strong>
          <small>notes</small>
        </span>
        <span className="note-ue-origin-summary">
          <NoteSimulationNaturePill nature={nature} />
          {sourceStatus === "conflict" && (
            <small className="is-conflict">
              <AlertTriangle size={12} /> Conflit
            </small>
          )}
          {sourceStatus === "unavailable" && (
            <small className="is-unavailable">
              <AlertTriangle size={12} /> Source indisponible
            </small>
          )}
        </span>
        <ChevronDown className="note-ue-chevron" size={19} aria-hidden="true" />
      </button>

      {open && (
        <div className="note-ue-panel" id={panelId} role="region" aria-labelledby={headingId}>
          <section className="note-ue-information" aria-labelledby={`info-${safeKey}`}>
            <header>
              <div>
                <span>Informations de l’UE</span>
                <h4 id={`info-${safeKey}`}>{displayName}</h4>
              </div>
              <button className="secondary-button" type="button" onClick={onEditUe} disabled={disabled}>
                <Edit3 size={16} />
                Modifier l’UE
              </button>
            </header>
            <dl>
              <div>
                <dt>Semestre</dt>
                <dd>{ue.semester ?? "Non défini"}</dd>
              </div>
              <div>
                <dt>Code</dt>
                <dd>{ue.ue_code || "Non renseigné"}</dd>
              </div>
              <div>
                <dt>ECTS</dt>
                <dd>{ue.credits_ects ? formatNumber(Number(ue.credits_ects)) : "—"}</dd>
              </div>
              <div>
                <dt>Origine</dt>
                <dd>{ue.server ? "Données importées" : "Ajout manuel"}</dd>
              </div>
            </dl>
            {ue.server?.source?.observed_at && (
              <p>
                <Info size={14} />
                Source observée le {formatDate(ue.server.source.observed_at, false)}
              </p>
            )}
          </section>

          {sourceStatus === "conflict" && ue.id && (
            <NoteSimulationConflictPanel
              scope="UE"
              label="Le semestre, l’intitulé ou les ECTS officiels ont évolué depuis l’import."
              disabled={disabled}
              onResolve={onResolveUe}
            />
          )}
          {sourceStatus === "unavailable" && (
            <div className="note-source-unavailable">
              <AlertTriangle size={15} />
              <span>Cette UE n’apparaît plus dans la source actuelle. Elle reste dans ta simulation.</span>
            </div>
          )}

          <NoteSimulationAssessmentList
            ue={ue}
            disabled={disabled}
            onEdit={onEditAssessment}
            onAdd={onAddAssessment}
            onResolve={onResolveAssessment}
          />

          <footer className="note-ue-actions">
            <span>
              <Info size={14} />
              Une note vide reste en attente et n’est jamais remplacée par zéro.
            </span>
            <button className="secondary-button" type="button" onClick={onEditUe} disabled={disabled}>
              <Edit3 size={16} />
              Gérer ou supprimer l’UE
            </button>
          </footer>
        </div>
      )}
    </article>
  );
}
