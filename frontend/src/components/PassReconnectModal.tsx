import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Eye, EyeOff, KeyRound, LockKeyhole, RefreshCw, ShieldCheck } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { api } from "../lib/api";
import { queryKeys } from "../lib/queries";
import type { ServiceSessionStatus } from "../types";
import { Modal } from "./Modal";
import { useToast } from "./Toast";

interface PassReconnectModalProps {
  open: boolean;
  identifier: string | null | undefined;
  onClose: () => void;
  onRenewed?: () => void;
}

export function PassReconnectModal({
  open,
  identifier,
  onClose,
  onRenewed,
}: PassReconnectModalProps) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [password, setPassword] = useState("");
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!open) {
      setPassword("");
      setVisible(false);
    }
  }, [open]);
  const reconnect = useMutation({
    mutationFn: () => api<{ ok: true; service_session: ServiceSessionStatus }>(
      "/api/v1/auth/pass/reconnect",
      { method: "POST", body: JSON.stringify({ password }) },
    ),
    onSuccess: () => {
      setPassword("");
      void queryClient.invalidateQueries({ queryKey: queryKeys.account });
      showToast("Session PASS renouvelée");
      onClose();
      onRenewed?.();
    },
    onError: (error) => showToast(error.message, "error"),
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (password) reconnect.mutate();
  };

  return (
    <Modal
      open={open}
      title="Renouveler la session IMT"
      description="Une authentification ponctuelle suffit pour reprendre les synchronisations."
      onClose={onClose}
      size="small"
      className="pass-reconnect-modal"
    >
      <form className="pass-reconnect-form" onSubmit={submit}>
        <div className="pass-reconnect-identity">
          <span><KeyRound size={18} /></span>
          <div><small>Identifiant CAS / IMT Atlantique</small><strong>{identifier ?? "Compte courant"}</strong></div>
        </div>
        <label>
          Mot de passe IMT
          <div className="password-field">
            <input
              type={visible ? "text" : "password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              name="imt-password"
              maxLength={512}
              required
              autoFocus
            />
            <button className="field-icon" type="button" onClick={() => setVisible((value) => !value)} aria-label={visible ? "Masquer le mot de passe" : "Afficher le mot de passe"}>
              {visible ? <EyeOff size={17} /> : <Eye size={17} />}
            </button>
          </div>
        </label>
        <div className="pass-reconnect-assurance">
          <ShieldCheck size={17} />
          <p><strong>Le mot de passe n'est pas enregistré.</strong> Il sert uniquement à ouvrir une session technique PASS/HUB, chiffrée et conservée au maximum 30 jours.</p>
        </div>
        <div className="pass-reconnect-beta"><LockKeyhole size={15} /><span>Expérimentation en cours : PASS peut fermer sa session plus tôt. IMTégrale demandera alors une nouvelle authentification.</span></div>
        <footer className="modal-actions">
          <button className="secondary-button" type="button" onClick={onClose}>Annuler</button>
          <button className="primary-button" type="submit" disabled={!password || reconnect.isPending}>
            {reconnect.isPending ? <span className="spinner" /> : <RefreshCw size={17} />} Renouveler
          </button>
        </footer>
      </form>
    </Modal>
  );
}
