import { BarChart3, Check, FilePlus2, LoaderCircle, Plus, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { Modal } from "../../components/Modal";

export function NoteSimulationCreationModal({
  open,
  sourceUeCount,
  sourceAssessmentCount,
  pending,
  onClose,
  onCreate,
}: {
  open: boolean;
  sourceUeCount: number;
  sourceAssessmentCount: number;
  pending: boolean;
  onClose: () => void;
  onCreate: (name: string, importCurrent: boolean) => void;
}) {
  const [name, setName] = useState("Projection du semestre");
  const [mode, setMode] = useState<"blank" | "academic">(sourceUeCount ? "academic" : "blank");

  useEffect(() => {
    if (!open) return;
    setName("Projection du semestre");
    setMode(sourceUeCount ? "academic" : "blank");
  }, [open, sourceUeCount]);

  return (
    <Modal
      open={open}
      title="Créer une simulation de notes"
      description="Choisis ton point de départ. Les données importées deviennent une copie librement modifiable."
      onClose={onClose}
      size="large"
      dismissible={!pending}
    >
      <form
        className="simulation-create-form"
        onSubmit={(event) => {
          event.preventDefault();
          onCreate(name, mode === "academic");
        }}
      >
        <label className="simulation-name-field">
          <span>Nom du scénario</span>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={80}
            autoComplete="off"
            required
          />
        </label>
        <div className="simulation-start-options" role="radiogroup" aria-label="Point de départ">
          <button
            type="button"
            className={mode === "academic" ? "active" : ""}
            onClick={() => setMode("academic")}
            disabled={!sourceUeCount}
            role="radio"
            aria-checked={mode === "academic"}
          >
            <span>
              <BarChart3 size={21} />
            </span>
            <strong>Importer mes notes</strong>
            <small>
              {sourceUeCount
                ? `${sourceUeCount} UE · ${sourceAssessmentCount} évaluations`
                : "Aucune donnée académique disponible"}
            </small>
            <i>{mode === "academic" && <Check size={14} />}</i>
          </button>
          <button
            type="button"
            className={mode === "blank" ? "active" : ""}
            onClick={() => setMode("blank")}
            role="radio"
            aria-checked={mode === "blank"}
          >
            <span>
              <FilePlus2 size={21} />
            </span>
            <strong>Commencer à zéro</strong>
            <small>UE et évaluations entièrement libres</small>
            <i>{mode === "blank" && <Check size={14} />}</i>
          </button>
        </div>
        <div className="simulation-private-note">
          <ShieldCheck size={17} />
          <span>Privé à ton compte. Rien n’est envoyé vers PASS ou COMPETENCES.</span>
        </div>
        <footer className="modal-actions">
          <button className="secondary-button" type="button" onClick={onClose} disabled={pending}>
            Annuler
          </button>
          <button className="primary-button" type="submit" disabled={pending || !name.trim()}>
            {pending ? <LoaderCircle className="spin" size={17} /> : <Plus size={17} />} Créer
          </button>
        </footer>
      </form>
    </Modal>
  );
}
