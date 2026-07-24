import { AlertTriangle, BadgeCheck, Save, Trash2, X } from "lucide-react";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { GradeBadge } from "../../components/GradeBadge";
import { Modal } from "../../components/Modal";
import { formatDate, formatNumber } from "../../lib/format";
import {
  createDraftEntry,
  gradePoints,
  SIMULATION_GRADES,
  SIMULATION_SEMESTERS,
  type SimulationDraftEntry,
} from "../../lib/simulations";
import type { SimulationGrade, SimulationSemester } from "../../types";
import { domSafeKey, entryNature, natureLabel, numberOrNull, sourceLabel } from "./gpaSimulationPresentation";
import { GpaSimulationConflictPanel } from "./GpaSimulationConflictPanel";
import { GpaSimulationNaturePill } from "./GpaSimulationNaturePill";

function freshEntry(semester: SimulationSemester | null): SimulationDraftEntry {
  return createDraftEntry(semester);
}

export function GpaSimulationUeEditor({
  open,
  compact,
  entry,
  defaultSemester,
  disabled,
  conflictPending,
  onClose,
  onApply,
  onDelete,
  onResolve,
}: {
  open: boolean;
  compact: boolean;
  entry: SimulationDraftEntry | null;
  defaultSemester: SimulationSemester | null;
  disabled: boolean;
  conflictPending: boolean;
  onClose: () => void;
  onApply: (entry: SimulationDraftEntry) => void;
  onDelete?: (entry: SimulationDraftEntry) => void;
  onResolve: (entry: SimulationDraftEntry, resolution: "source" | "simulation") => void;
}) {
  const [form, setForm] = useState<SimulationDraftEntry | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const titleRef = useRef<HTMLInputElement>(null);
  const deleteRef = useRef<HTMLButtonElement>(null);
  const identityErrorId = useId();
  const creditsErrorId = useId();

  useEffect(() => {
    if (!open) return;
    setForm(entry ? { ...entry } : freshEntry(defaultSemester));
    setConfirmDelete(false);
  }, [defaultSemester, entry, open]);

  useEffect(() => {
    if (!open || compact) return;
    window.requestAnimationFrame(() => titleRef.current?.focus({ preventScroll: true }));
  }, [compact, entry?.clientKey, open]);

  if (!open || !form) return null;

  const identityMissing = !form.ue_code.trim() && !form.title.trim();
  const credits = numberOrNull(form.credits_ects);
  const invalidCredits = credits !== null && (!Number.isFinite(credits) || credits <= 0 || credits > 60);
  const valid = !identityMissing && !invalidCredits;
  const displayName = form.title || form.ue_code || "cette UE";
  const editorName = entry ? entry.title || entry.ue_code || "cette UE" : "Nouvelle UE";
  const sourceStatus = form.server?.source?.status;
  const provenance = sourceLabel(form);

  const content: ReactNode = (
    <form
      className="gpa-editor-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (valid && !disabled) onApply(form);
      }}
    >
      <div className="gpa-editor-scroll">
        <div className="gpa-editor-fields">
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
              disabled={disabled}
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
              placeholder="Ex. FIC210"
              aria-invalid={identityMissing}
              aria-describedby={identityMissing ? identityErrorId : undefined}
              disabled={disabled}
            />
          </label>
          <label>
            <span>Semestre</span>
            <select
              value={form.semester ?? ""}
              onChange={(event) =>
                setForm((current) =>
                  current
                    ? { ...current, semester: (event.target.value || null) as SimulationSemester | null }
                    : current,
                )
              }
              disabled={disabled}
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
              disabled={disabled}
            />
          </label>
          <label>
            <span>Grade potentiel</span>
            <select
              value={form.grade ?? ""}
              onChange={(event) =>
                setForm((current) =>
                  current ? { ...current, grade: (event.target.value || null) as SimulationGrade | null } : current,
                )
              }
              disabled={disabled}
            >
              <option value="">En attente</option>
              {SIMULATION_GRADES.map(({ grade, points }) => (
                <option key={grade} value={grade}>
                  {grade} · {formatNumber(points)} points
                </option>
              ))}
            </select>
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

        <section className="gpa-editor-result" aria-label="Résultat de l’hypothèse">
          <div>
            <span>Grade</span>
            <GradeBadge grade={form.grade} />
          </div>
          <div>
            <span>Points GPA</span>
            <strong>{formatNumber(gradePoints(form.grade))}</strong>
          </div>
          <div>
            <span>Nature</span>
            <GpaSimulationNaturePill nature={entryNature(form)} />
          </div>
        </section>

        <section className="gpa-editor-provenance" aria-label="Provenance">
          <div>
            <span>État de la source</span>
            <strong>
              {sourceStatus === "current" ? (
                <BadgeCheck size={15} />
              ) : sourceStatus ? (
                <AlertTriangle size={15} />
              ) : null}
              {form.server
                ? sourceStatus === "unavailable"
                  ? "Source devenue indisponible"
                  : sourceStatus === "conflict"
                    ? "Évolution à vérifier"
                    : "Source disponible"
                : natureLabel(entryNature(form))}
            </strong>
          </div>
          {provenance && <p>{provenance}</p>}
          {form.server?.source?.observed_at && (
            <small>Source observée le {formatDate(form.server.source.observed_at, false)}</small>
          )}
          <p>Cette hypothèse reste privée et n’est jamais envoyée vers PASS ou COMPETENCES.</p>
        </section>

        <GpaSimulationConflictPanel
          entry={form}
          disabled={disabled}
          pending={conflictPending}
          onResolve={(resolution) => onResolve(form, resolution)}
        />

        {entry && onDelete && (
          <section className="gpa-editor-danger">
            {confirmDelete ? (
              <div role="alert">
                <AlertTriangle size={18} />
                <p>
                  Supprimer <strong>{displayName}</strong> de cette simulation ? Les données officielles restent
                  inchangées.
                </p>
                <div>
                  <button
                    className="secondary-button"
                    type="button"
                    onClick={() => {
                      setConfirmDelete(false);
                      window.requestAnimationFrame(() => deleteRef.current?.focus());
                    }}
                  >
                    Conserver l’UE
                  </button>
                  <button className="danger-button" type="button" onClick={() => onDelete(form)} disabled={disabled}>
                    Confirmer la suppression
                  </button>
                </div>
              </div>
            ) : (
              <button
                ref={deleteRef}
                className="danger-link-button"
                type="button"
                onClick={() => setConfirmDelete(true)}
                disabled={disabled}
              >
                <Trash2 size={17} />
                Supprimer cette UE
              </button>
            )}
          </section>
        )}
      </div>

      <footer className="gpa-editor-actions">
        <button className="secondary-button" type="button" onClick={onClose}>
          <X size={17} />
          Annuler
        </button>
        <button className="primary-button" type="submit" disabled={!valid || disabled}>
          <Save size={17} />
          {entry ? "Appliquer" : "Ajouter l’UE"}
        </button>
      </footer>
    </form>
  );

  if (compact) {
    return (
      <Modal
        open
        title={entry ? `Modifier ${editorName}` : "Ajouter une UE"}
        description="Les changements seront appliqués au scénario avant l’autosauvegarde."
        onClose={onClose}
        size="large"
        className="gpa-editor-modal"
        initialFocusRef={titleRef}
      >
        {content}
      </Modal>
    );
  }

  return (
    <aside
      id={`gpa-ue-editor-${domSafeKey(form.clientKey)}`}
      className="gpa-inline-editor"
      aria-label={entry ? `Édition de ${editorName}` : "Ajout d’une UE"}
    >
      <header>
        <div>
          <span>{entry ? "UE sélectionnée" : "Nouvelle UE"}</span>
          <h3>{entry ? editorName : "Ajouter une hypothèse"}</h3>
        </div>
        <button className="icon-button" type="button" onClick={onClose} aria-label="Fermer l’éditeur">
          <X size={18} />
        </button>
      </header>
      {content}
    </aside>
  );
}
