import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Eye, EyeOff, KeyRound, LockKeyhole, RefreshCw, ShieldCheck } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { authReconnectPass } from "../generated/api/sdk.gen";
import { apiData, throwOnApiError } from "../lib/generatedApi";
import { queryKeys } from "../lib/queries";
import { Modal } from "./Modal";
import { useToast } from "./Toast";

interface PassReconnectModalProps {
  open: boolean;
  identifier: string | null | undefined;
  onClose: () => void;
  onRenewed?: () => void;
  purpose?: "sync" | "learning";
  autonomousConfigured?: boolean;
}

export function PassReconnectModal({
  open,
  identifier,
  onClose,
  onRenewed,
  purpose = "sync",
  autonomousConfigured = false,
}: PassReconnectModalProps) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const passwordRef = useRef<HTMLInputElement>(null);
  const [hasPassword, setHasPassword] = useState(false);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!open) {
      if (passwordRef.current) passwordRef.current.value = "";
      setHasPassword(false);
      setVisible(false);
    }
  }, [open]);
  const reconnect = useMutation({
    mutationFn: async () => {
      const password = passwordRef.current?.value ?? "";
      try {
        return await apiData(
          authReconnectPass({
            body: { password },
            throwOnError: throwOnApiError,
          }),
        );
      } finally {
        if (passwordRef.current) passwordRef.current.value = "";
        setHasPassword(false);
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.account });
      showToast(purpose === "learning" ? "Statut étudiant vérifié" : "Session PASS renouvelée");
      onClose();
      onRenewed?.();
    },
    onError: (error) => {
      showToast(error.message, "error");
      window.requestAnimationFrame(() => passwordRef.current?.focus());
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (hasPassword) reconnect.mutate();
  };

  return (
    <Modal
      open={open}
      title={purpose === "learning" ? "Vérifier mon statut étudiant" : "Renouveler la session IMT"}
      description={
        purpose === "learning"
          ? "Une authentification IMT ponctuelle confirme ton statut sans lancer de synchronisation des notes."
          : "Une authentification ponctuelle suffit pour reprendre les synchronisations."
      }
      onClose={onClose}
      size="small"
      className="pass-reconnect-modal"
      initialFocusRef={passwordRef}
    >
      <form className="pass-reconnect-form" onSubmit={submit}>
        <div className="pass-reconnect-identity">
          <span>
            <KeyRound size={18} />
          </span>
          <div>
            <small>Identifiant CAS / IMT Atlantique</small>
            <strong>{identifier ?? "Compte courant"}</strong>
          </div>
        </div>
        <label>
          Mot de passe IMT
          <div className="password-field">
            <input
              ref={passwordRef}
              type={visible ? "text" : "password"}
              onInput={() => setHasPassword(Boolean(passwordRef.current?.value))}
              autoComplete="current-password"
              name="imt-password"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              maxLength={512}
              required
            />
            <button
              className="field-icon"
              type="button"
              onClick={() => setVisible((value) => !value)}
              aria-label={visible ? "Masquer le mot de passe" : "Afficher le mot de passe"}
            >
              {visible ? <EyeOff size={17} /> : <Eye size={17} />}
            </button>
          </div>
        </label>
        <div className="pass-reconnect-assurance">
          <ShieldCheck size={17} />
          <p>
            <strong>Le mot de passe n'est pas enregistré.</strong>{" "}
            {purpose === "learning"
              ? "Cette étape actualise le profil académique et la date de vérification, sans importer de notes."
              : "Il sert uniquement à ouvrir une session technique PASS/HUB, chiffrée et conservée au maximum 30 jours."}
          </p>
        </div>
        {purpose === "sync" && autonomousConfigured && (
          <div className="pass-reconnect-distinction">
            <KeyRound size={16} />
            <p>
              Cette reconnexion renouvelle uniquement la session PASS/HUB. Elle ne remplace pas le mot de passe autonome
              conservé ; sa mise à jour reste une action séparée dans les paramètres.
            </p>
          </div>
        )}
        <div className="pass-reconnect-beta">
          <LockKeyhole size={15} />
          <span>
            Expérimentation en cours : PASS peut fermer sa session plus tôt. IMTégrale demandera alors une nouvelle
            authentification.
          </span>
        </div>
        <footer className="modal-actions">
          <button className="secondary-button" type="button" onClick={onClose}>
            Annuler
          </button>
          <button className="primary-button" type="submit" disabled={!hasPassword || reconnect.isPending}>
            {reconnect.isPending ? <span className="spinner" /> : <RefreshCw size={17} />}{" "}
            {purpose === "learning" ? "Vérifier" : "Renouveler"}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
