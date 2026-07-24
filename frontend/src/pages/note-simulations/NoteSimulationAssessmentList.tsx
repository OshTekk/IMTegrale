import { AlertTriangle, ArrowRight, BarChart3, Plus, RotateCcw } from "lucide-react";
import { EmptyState } from "../../components/EmptyState";
import { formatDate, formatNumber } from "../../lib/format";
import type { NoteSimulationAssessmentDraft, NoteSimulationUeDraft } from "../../lib/noteSimulations";
import { NoteSimulationConflictPanel, type NoteSimulationResolution } from "./NoteSimulationConflictPanel";
import { NoteSimulationNaturePill } from "./NoteSimulationNaturePill";
import { assessmentNature, domSafeKey, hasAssessmentConflict } from "./noteSimulationPresentation";

function scoreLabel(score: string): string {
  if (score === "") return "En attente";
  return `${formatNumber(Number(score))} sur 20`;
}

export function NoteSimulationAssessmentList({
  ue,
  disabled,
  onEdit,
  onAdd,
  onResolve,
}: {
  ue: NoteSimulationUeDraft;
  disabled: boolean;
  onEdit: (assessment: NoteSimulationAssessmentDraft) => void;
  onAdd: () => void;
  onResolve: (assessment: NoteSimulationAssessmentDraft, resolution: NoteSimulationResolution) => void;
}) {
  return (
    <section className="note-workbench-assessments" aria-labelledby={`assessments-${domSafeKey(ue.clientKey)}`}>
      <header>
        <div>
          <span>Évaluations</span>
          <h4 id={`assessments-${domSafeKey(ue.clientKey)}`}>
            {ue.assessments.length} évaluation
            {ue.assessments.length === 1 ? "" : "s"}
          </h4>
        </div>
        <button
          className="secondary-button"
          type="button"
          onClick={onAdd}
          disabled={disabled || ue.assessments.length >= 60}
        >
          <Plus size={16} />
          Ajouter une évaluation
        </button>
      </header>
      {ue.assessments.length ? (
        <ul className="note-assessment-cards">
          {ue.assessments.map((assessment) => {
            const nature = assessmentNature(assessment);
            const conflict = hasAssessmentConflict(assessment);
            const unavailable = assessment.server?.source?.status === "unavailable";
            const cardId = `assessment-${domSafeKey(assessment.clientKey)}`;
            return (
              <li key={assessment.clientKey}>
                <button
                  id={cardId}
                  className={`note-assessment-card nature-${nature}${conflict ? " has-conflict" : ""}`}
                  type="button"
                  onClick={() => onEdit(assessment)}
                  disabled={disabled}
                  aria-label={`Modifier ${assessment.label || "l’évaluation"}, ${scoreLabel(assessment.score)}, coefficient ${assessment.coefficient || "non renseigné"}${assessment.is_resit ? ", rattrapage" : ""}`}
                >
                  <span className="note-assessment-main">
                    <strong>{assessment.label || "Évaluation à compléter"}</strong>
                    <small>
                      {assessment.is_resit ? (
                        <>
                          <RotateCcw size={12} /> Rattrapage
                        </>
                      ) : (
                        "Évaluation classique"
                      )}
                      {assessment.server?.source?.observed_at && (
                        <> · importée le {formatDate(assessment.server.source.observed_at, false)}</>
                      )}
                    </small>
                  </span>
                  <span className={`note-assessment-score${assessment.score === "" ? " pending" : ""}`}>
                    <strong>{assessment.score === "" ? "En attente" : formatNumber(Number(assessment.score))}</strong>
                    <small>{assessment.score === "" ? "note" : "/20"}</small>
                  </span>
                  <span className="note-assessment-coefficient">
                    <small>Coefficient</small>
                    <strong>{assessment.coefficient || "—"}</strong>
                  </span>
                  <span className="note-assessment-origin">
                    <NoteSimulationNaturePill nature={nature} />
                    {(conflict || unavailable) && (
                      <small className={conflict ? "is-conflict" : "is-unavailable"}>
                        <AlertTriangle size={12} />
                        {conflict ? "Action requise" : "Source indisponible"}
                      </small>
                    )}
                  </span>
                  <span className="note-assessment-edit">
                    Modifier <ArrowRight size={15} />
                  </span>
                </button>
                {conflict && assessment.id && (
                  <NoteSimulationConflictPanel
                    scope="évaluation"
                    label="La note, son coefficient ou son statut a évolué depuis l’import."
                    disabled={disabled}
                    onResolve={(resolution) => onResolve(assessment, resolution)}
                  />
                )}
              </li>
            );
          })}
        </ul>
      ) : (
        <EmptyState
          icon={<BarChart3 size={19} />}
          title="Aucune évaluation"
          detail="Ajoute une note potentielle pour calculer la moyenne de cette UE."
        />
      )}
    </section>
  );
}
