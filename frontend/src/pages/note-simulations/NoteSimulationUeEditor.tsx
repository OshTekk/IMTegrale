import { AlertTriangle, BadgeCheck, Save, Trash2 } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { Modal } from "../../components/Modal";
import { createNoteUe, type NoteSimulationUeDraft } from "../../lib/noteSimulations";
import { SIMULATION_SEMESTERS } from "../../lib/simulations";
import type { SimulationSemester } from "../../types";
import { NoteSimulationNaturePill } from "./NoteSimulationNaturePill";
import { natureLabel, numberOrNull, sourceLabel, ueNature } from "./noteSimulationPresentation";

function freshUe(semester: SimulationSemester | null): NoteSimulationUeDraft {
  return { ...createNoteUe(semester), assessments: [] };
}

export function NoteSimulationUeEditor({
  open,
  ue,
  defaultSemester,
  onClose,
  onSave,
  onDelete,
}: {
  open: boolean;
  ue: NoteSimulationUeDraft | null;
  defaultSemester: SimulationSemester | null;
  onClose: () => void;
  onSave: (ue: NoteSimulationUeDraft) => void;
  onDelete?: (ue: NoteSimulationUeDraft) => void;
}) {
  const [form, setForm] = useState<NoteSimulationUeDraft | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const titleRef = useRef<HTMLInputElement>(null);
  const identityErrorId = useId();
  const creditsErrorId = useId();

  useEffect(() => {
    if (!open) return;
    setForm(
      ue
        ? {
            ...ue,
            assessments: [...ue.assessments],
          }
        : freshUe(defaultSemester),
    );
    setConfirmDelete(false);
  }, [defaultSemester, open, ue]);

  if (!form) return null;

  const identityMissing = !form.ue_code.trim() && !form.title.trim();
  const credits = numberOrNull(form.credits_ects);
  const invalidCredits = credits !== null && (!Number.isFinite(credits) || credits <= 0 || credits > 60);
  const valid = !identityMissing && !invalidCredits;
  const nature = ueNature(form);
  const sourceStatus = form.server?.source?.status;
  const displayName = form.title || form.ue_code || "cette UE";

  return (
    <Modal
      open={open}
      title={ue ? `Modifier ${displayName}` : "Ajouter une UE"}
      description={
        ue ? "Les évaluations restent inchangées." : "L’UE sera ajoutée au scénario uniquement après validation."
      }
      onClose={onClose}
      size="large"
      className="note-editor-modal"
      initialFocusRef={titleRef}
    >
      <form
        className="note-editor-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (valid) onSave(form);
        }}
      >
        <div className="note-editor-fields note-ue-editor-fields">
          <label>
            <span>Intitulé de l’UE</span>
            <input
              ref={titleRef}
              value={form.title}
              onChange={(event) =>
                setForm((current) => (current ? { ...current, title: event.target.value } : current))
              }
              maxLength={200}
              placeholder="Intitulé de l’UE"
              aria-invalid={identityMissing}
              aria-describedby={identityMissing ? identityErrorId : undefined}
            />
          </label>
          <label>
            <span>Code UE</span>
            <input
              value={form.ue_code}
              onChange={(event) =>
                setForm((current) => (current ? { ...current, ue_code: event.target.value.toUpperCase() } : current))
              }
              maxLength={32}
              placeholder="Ex. INF210"
              aria-invalid={identityMissing}
              aria-describedby={identityMissing ? identityErrorId : undefined}
            />
          </label>
          <label>
            <span>Semestre</span>
            <select
              value={form.semester ?? ""}
              onChange={(event) =>
                setForm((current) =>
                  current
                    ? {
                        ...current,
                        semester: (event.target.value || null) as SimulationSemester | null,
                      }
                    : current,
                )
              }
            >
              <option value="">Non défini</option>
              {SIMULATION_SEMESTERS.map((semester) => (
                <option key={semester}>{semester}</option>
              ))}
            </select>
          </label>
          <label>
            <span>Crédits ECTS</span>
            <input
              type="number"
              value={form.credits_ects}
              onChange={(event) =>
                setForm((current) => (current ? { ...current, credits_ects: event.target.value } : current))
              }
              min="0.01"
              max="60"
              step="0.01"
              inputMode="decimal"
              placeholder="Facultatif"
              aria-invalid={invalidCredits}
              aria-describedby={invalidCredits ? creditsErrorId : undefined}
            />
          </label>
        </div>
        {identityMissing && (
          <p className="field-error" id={identityErrorId}>
            Renseigne au moins un intitulé ou un code UE.
          </p>
        )}
        {invalidCredits && (
          <p className="field-error" id={creditsErrorId}>
            Les crédits doivent être compris entre 0,01 et 60.
          </p>
        )}
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
          <p>Cette modification reste une hypothèse privée et n’est jamais envoyée vers PASS ou COMPETENCES.</p>
        </section>

        {ue && onDelete && (
          <section className="note-editor-danger">
            {confirmDelete ? (
              <div role="alert">
                <AlertTriangle size={18} />
                <p>
                  Supprimer <strong>{displayName}</strong> et ses {form.assessments.length} évaluation
                  {form.assessments.length === 1 ? "" : "s"} du scénario ?
                </p>
                <div>
                  <button className="secondary-button" type="button" onClick={() => setConfirmDelete(false)}>
                    Conserver l’UE
                  </button>
                  <button className="danger-button" type="button" onClick={() => onDelete(form)}>
                    Confirmer la suppression
                  </button>
                </div>
              </div>
            ) : (
              <button className="danger-link-button" type="button" onClick={() => setConfirmDelete(true)}>
                <Trash2 size={17} />
                Supprimer cette UE
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
            {ue ? "Appliquer" : "Ajouter l’UE"}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
