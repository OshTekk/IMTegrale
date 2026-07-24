import { BookOpenCheck, Check, FilePlus2, LoaderCircle, Plus, ShieldCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Modal } from "../../components/Modal";

export function GpaSimulationCreationModal({
  open,
  sourceCount,
  sourceGradedCount,
  pending,
  onClose,
  onCreate,
}: {
  open: boolean;
  sourceCount: number;
  sourceGradedCount: number;
  pending: boolean;
  onClose: () => void;
  onCreate: (name: string, importCurrent: boolean) => void;
}) {
  const [name, setName] = useState("Nouvelle projection");
  const [mode, setMode] = useState<"blank" | "academic">(sourceCount ? "academic" : "blank");
  const nameRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!open) return;
    setName("Nouvelle projection");
    setMode(sourceCount ? "academic" : "blank");
  }, [open, sourceCount]);
  return (
    <Modal
      open={open}
      title="Créer une simulation GPA"
      description="Choisis le point de départ. Toutes les hypothèses resteront privées et modifiables."
      onClose={onClose}
      size="large"
      initialFocusRef={nameRef}
      className="gpa-creation-modal"
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
            ref={nameRef}
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
            disabled={!sourceCount}
            role="radio"
            aria-checked={mode === "academic"}
          >
            <span>
              <BookOpenCheck size={21} />
            </span>
            <strong>Partir de mes UE</strong>
            <small>
              {sourceCount
                ? `${sourceCount} UE disponibles · ${sourceGradedCount} gradées`
                : "Aucune UE officielle disponible"}
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
            <small>Un scénario entièrement libre</small>
            <i>{mode === "blank" && <Check size={14} />}</i>
          </button>
        </div>
        <div className="simulation-private-note">
          <ShieldCheck size={17} />
          <span>Privé à ton compte. Les tokens de partage n’y ont jamais accès.</span>
        </div>
        <footer className="modal-actions">
          <button className="secondary-button" type="button" onClick={onClose}>
            Annuler
          </button>
          <button className="primary-button" type="submit" disabled={pending || !name.trim()}>
            {pending ? <LoaderCircle className="spin" size={17} /> : <Plus size={17} />}
            Créer
          </button>
        </footer>
      </form>
    </Modal>
  );
}
