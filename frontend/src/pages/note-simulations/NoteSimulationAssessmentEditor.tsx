import { AlertTriangle, BadgeCheck, Save, Trash2 } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { Modal } from "../../components/Modal";
import { createNoteAssessment, type NoteSimulationAssessmentDraft } from "../../lib/noteSimulations";
import { NoteSimulationNaturePill } from "./NoteSimulationNaturePill";
import { assessmentNature, natureLabel, numberOrNull, sourceLabel } from "./noteSimulationPresentation";

export function NoteSimulationAssessmentEditor({
  open,
  assessment,
  ueName,
  onClose,
  onSave,
  onDelete,
}: {
  open: boolean;
  assessment: NoteSimulationAssessmentDraft | null;
  ueName: string;
  onClose: () => void;
  onSave: (assessment: NoteSimulationAssessmentDraft) => void;
  onDelete?: (assessment: NoteSimulationAssessmentDraft) => void;
}) {
  const [form, setForm] = useState<NoteSimulationAssessmentDraft | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const labelRef = useRef<HTMLInputElement>(null);
  const labelErrorId = useId();
  const scoreErrorId = useId();
  const coefficientErrorId = useId();

  useEffect(() => {
    if (!open) return;
    setForm(assessment ? { ...assessment } : createNoteAssessment(""));
    setConfirmDelete(false);
  }, [assessment, open]);

  if (!form) return null;

  const score = numberOrNull(form.score);
  const coefficient = numberOrNull(form.coefficient);
  const invalidLabel = !form.label.trim();
  const invalidScore = score !== null && (!Number.isFinite(score) || score < 0 || score > 20);
  const invalidCoefficient =
    coefficient === null || !Number.isFinite(coefficient) || coefficient <= 0 || coefficient > 100;
  const valid = !invalidLabel && !invalidScore && !invalidCoefficient;
  const nature = assessmentNature(form);
  const sourceStatus = form.server?.source?.status;
  const displayName = form.label || "cette évaluation";

  return (
    <Modal
      open={open}
      title={assessment ? `Modifier ${displayName}` : "Ajouter une évaluation"}
      description={ueName}
      onClose={onClose}
      size="medium"
      className="note-editor-modal note-assessment-editor-modal"
      initialFocusRef={labelRef}
    >
      <form
        className="note-editor-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (valid) onSave(form);
        }}
      >
        <div className="note-editor-fields note-assessment-editor-fields">
          <label className="note-editor-wide-field">
            <span>Nom de l’évaluation</span>
            <input
              ref={labelRef}
              value={form.label}
              onChange={(event) =>
                setForm((current) => (current ? { ...current, label: event.target.value } : current))
              }
              maxLength={240}
              placeholder="Ex. Contrôle continu"
              aria-invalid={invalidLabel}
              aria-describedby={invalidLabel ? labelErrorId : undefined}
            />
            {invalidLabel && (
              <small className="field-error" id={labelErrorId}>
                Le nom de l’évaluation est requis.
              </small>
            )}
          </label>
          <label>
            <span>Note sur 20</span>
            <input
              type="number"
              value={form.score}
              onChange={(event) =>
                setForm((current) => (current ? { ...current, score: event.target.value } : current))
              }
              min="0"
              max="20"
              step="0.01"
              inputMode="decimal"
              placeholder="En attente"
              aria-invalid={invalidScore}
              aria-describedby={invalidScore ? scoreErrorId : undefined}
            />
            {invalidScore && (
              <small className="field-error" id={scoreErrorId}>
                La note doit être comprise entre 0 et 20.
              </small>
            )}
          </label>
          <label>
            <span>Coefficient</span>
            <input
              type="number"
              value={form.coefficient}
              onChange={(event) =>
                setForm((current) => (current ? { ...current, coefficient: event.target.value } : current))
              }
              min="0.01"
              max="100"
              step="0.01"
              inputMode="decimal"
              aria-invalid={invalidCoefficient}
              aria-describedby={invalidCoefficient ? coefficientErrorId : undefined}
            />
            {invalidCoefficient && (
              <small className="field-error" id={coefficientErrorId}>
                Le coefficient doit être compris entre 0,01 et 100.
              </small>
            )}
          </label>
          <label className="toggle-row note-editor-resit">
            <span>
              <strong>Rattrapage</strong>
              <small>Cette note remplace la moyenne normale selon la règle actuelle.</small>
            </span>
            <input
              type="checkbox"
              role="switch"
              checked={form.is_resit}
              onChange={(event) =>
                setForm((current) => (current ? { ...current, is_resit: event.target.checked } : current))
              }
            />
            <i aria-hidden="true" />
          </label>
        </div>

        <section className="note-editor-provenance" aria-label="Provenance">
          <div>
            <span>Origine</span>
            <NoteSimulationNaturePill nature={nature} />
          </div>
          <div>
            <span>État de la source</span>
            <strong>
              {sourceStatus === "current" ? (
                <BadgeCheck size={15} />
              ) : sourceStatus ? (
                <AlertTriangle size={15} />
              ) : null}
              {form.server ? sourceLabel(sourceStatus) : natureLabel(nature)}
            </strong>
          </div>
          <p>Cette valeur est une hypothèse locale à la simulation. Elle ne modifie aucune note officielle.</p>
        </section>

        {assessment && onDelete && (
          <section className="note-editor-danger">
            {confirmDelete ? (
              <div role="alert">
                <AlertTriangle size={18} />
                <p>
                  Supprimer <strong>{displayName}</strong> de cette simulation ?
                </p>
                <div>
                  <button className="secondary-button" type="button" onClick={() => setConfirmDelete(false)}>
                    Conserver
                  </button>
                  <button className="danger-button" type="button" onClick={() => onDelete(form)}>
                    Confirmer la suppression
                  </button>
                </div>
              </div>
            ) : (
              <button className="danger-link-button" type="button" onClick={() => setConfirmDelete(true)}>
                <Trash2 size={17} />
                Supprimer cette évaluation
              </button>
            )}
          </section>
        )}

        <footer className="modal-actions note-editor-actions">
          <button className="secondary-button" type="button" onClick={onClose}>
            Annuler
          </button>
          <button className="primary-button" type="submit" disabled={!valid}>
            <Save size={17} />
            {assessment ? "Appliquer" : "Ajouter"}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
