import { LoaderCircle, RotateCcw, Trash2 } from "lucide-react";
import { Modal } from "../Modal";

export type SimulationConfirmation = "reset" | "delete" | null;

interface SimulationConfirmationModalProps {
  action: SimulationConfirmation;
  name: string;
  pending: boolean;
  resetDescription: string;
  deleteBody: string;
  resetBody: string;
  onClose: () => void;
  onConfirm: () => void;
}

export function SimulationConfirmationModal({
  action,
  name,
  pending,
  resetDescription,
  deleteBody,
  resetBody,
  onClose,
  onConfirm,
}: SimulationConfirmationModalProps) {
  const deleting = action === "delete";
  return (
    <Modal
      open={Boolean(action)}
      title={deleting ? "Supprimer cette simulation ?" : "Réinitialiser les hypothèses ?"}
      description={deleting ? `« ${name} » sera supprimée définitivement.` : resetDescription}
      onClose={onClose}
      size="small"
    >
      <div className={deleting ? "simulation-confirmation is-danger" : "simulation-confirmation"}>
        <span>{deleting ? <Trash2 size={21} /> : <RotateCcw size={21} />}</span>
        <p>{deleting ? deleteBody : resetBody}</p>
      </div>
      <footer className="modal-actions">
        <button className="secondary-button" type="button" onClick={onClose}>
          Annuler
        </button>
        <button
          className={deleting ? "danger-button" : "primary-button"}
          type="button"
          onClick={onConfirm}
          disabled={pending}
        >
          {pending ? (
            <LoaderCircle className="spin" size={17} />
          ) : deleting ? (
            <Trash2 size={17} />
          ) : (
            <RotateCcw size={17} />
          )}{" "}
          {deleting ? "Supprimer" : "Réinitialiser"}
        </button>
      </footer>
    </Modal>
  );
}
