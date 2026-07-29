import { type ReactNode, useRef } from "react";
import { Modal } from "../../components/Modal";

export function PrivateComparisonConfirmModal({
  open,
  title,
  description,
  confirmLabel,
  pending = false,
  onCancel,
  onConfirm,
  children,
}: {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  pending?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  children?: ReactNode;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);

  return (
    <Modal
      open={open}
      title={title}
      description={description}
      onClose={onCancel}
      initialFocusRef={cancelRef}
      dismissible={!pending}
    >
      {children}
      <footer className="modal-actions">
        <button ref={cancelRef} className="secondary-button" type="button" onClick={onCancel} disabled={pending}>
          Annuler
        </button>
        <button className="danger-button" type="button" onClick={onConfirm} disabled={pending}>
          {pending ? "Traitement…" : confirmLabel}
        </button>
      </footer>
    </Modal>
  );
}
