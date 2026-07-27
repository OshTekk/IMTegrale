import { KeyRound, ShieldAlert, Trash2 } from "lucide-react";
import type { ReactNode } from "react";
import type { SyncMode } from "../../generated/api/types.gen";
import { Modal } from "../Modal";
import { syncModeTitle } from "./syncModeCopy";

interface ConfirmationModalProps {
  open: boolean;
  pending: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  icon: ReactNode;
  children: ReactNode;
  onClose: () => void;
  onConfirm: () => void;
}

function ConfirmationModal({
  open,
  pending,
  title,
  description,
  confirmLabel,
  icon,
  children,
  onClose,
  onConfirm,
}: ConfirmationModalProps) {
  return (
    <Modal open={open} title={title} description={description} onClose={onClose} size="small">
      <div className="sync-destructive-copy">
        {icon}
        {children}
      </div>
      <footer className="modal-actions">
        <button className="secondary-button" type="button" onClick={onClose} disabled={pending}>
          Annuler
        </button>
        <button className="danger-button" type="button" onClick={onConfirm} disabled={pending}>
          {pending ? <span className="spinner" /> : <Trash2 size={17} />}
          {confirmLabel}
        </button>
      </footer>
    </Modal>
  );
}

interface LeaveAutonomousModalProps {
  open: boolean;
  targetMode: Exclude<SyncMode, "autonomous">;
  pending: boolean;
  onClose: () => void;
  onConfirm: () => void;
}

export function LeaveAutonomousModal({ open, targetMode, pending, onClose, onConfirm }: LeaveAutonomousModalProps) {
  return (
    <ConfirmationModal
      open={open}
      pending={pending}
      title="Quitter la synchronisation autonome ?"
      description={`Passage vers « ${syncModeTitle(targetMode)} »`}
      confirmLabel="Supprimer le mot de passe et changer de mode"
      icon={<ShieldAlert size={21} />}
      onClose={onClose}
      onConfirm={onConfirm}
    >
      <div>
        <strong>Suppression immédiate et irréversible</strong>
        <p>Le mot de passe IMT conservé sera supprimé et ne pourra pas être récupéré.</p>
        <p>
          {targetMode === "session_only"
            ? "La session PASS/HUB actuelle pourra rester utilisable jusqu'à son expiration."
            : "Les synchronisations planifiées seront arrêtées. La session privée pourra rester disponible pour une actualisation à la demande."}
        </p>
      </div>
    </ConfirmationModal>
  );
}

interface DeleteCredentialModalProps {
  open: boolean;
  pending: boolean;
  onClose: () => void;
  onConfirm: () => void;
}

export function DeleteCredentialModal({ open, pending, onClose, onConfirm }: DeleteCredentialModalProps) {
  return (
    <ConfirmationModal
      open={open}
      pending={pending}
      title="Supprimer le mot de passe conservé ?"
      description="Cette action ne supprime ni ton compte ni tes résultats."
      confirmLabel="Supprimer le mot de passe conservé"
      icon={<KeyRound size={21} />}
      onClose={onClose}
      onConfirm={onConfirm}
    >
      <p>La synchronisation autonome cessera après expiration de la session PASS/HUB actuelle.</p>
    </ConfirmationModal>
  );
}

interface PurgePassAccessModalProps {
  open: boolean;
  pending: boolean;
  onClose: () => void;
  onConfirm: () => void;
}

export function PurgePassAccessModal({ open, pending, onClose, onConfirm }: PurgePassAccessModalProps) {
  return (
    <ConfirmationModal
      open={open}
      pending={pending}
      title="Supprimer tout accès PASS/HUB ?"
      description="Tes résultats, UE, passkeys et ta session web restent intacts."
      confirmLabel="Supprimer les accès PASS/HUB"
      icon={<ShieldAlert size={21} />}
      onClose={onClose}
      onConfirm={onConfirm}
    >
      <div>
        <p>Cette action supprime le mot de passe autonome et révoque les sessions PASS/HUB.</p>
        <p>Les synchronisations planifiées seront arrêtées et le mode repassera à « À la demande ».</p>
      </div>
    </ConfirmationModal>
  );
}
