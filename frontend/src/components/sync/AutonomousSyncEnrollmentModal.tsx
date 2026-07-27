import { useMutation } from "@tanstack/react-query";
import { Check, Eye, EyeOff, LockKeyhole, ShieldAlert } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { settingsEnrollSyncCredential, settingsUpdateSyncMode } from "../../generated/api/sdk.gen";
import type { SettingsResponse } from "../../generated/api/types.gen";
import { ApiError } from "../../lib/api";
import { apiData, throwOnApiError } from "../../lib/generatedApi";
import { Modal } from "../Modal";
import type { SyncInterval } from "./SyncScheduleOptions";

const CONSENT_VERSION = 1;

class ActivationPendingError extends Error {
  constructor(readonly cause: unknown) {
    super("activation_pending");
  }
}

function enrollmentErrorMessage(error: unknown): string {
  if (error instanceof ActivationPendingError) {
    return "Le mot de passe est protégé, mais l'activation n'est pas terminée.";
  }
  if (!(error instanceof ApiError)) {
    return "La synchronisation autonome n'a pas pu être configurée.";
  }
  if (error.code === "SYNC_CREDENTIAL_VERIFICATION_FAILED") {
    return "Le mot de passe IMT n'a pas été accepté. Rien n'a été modifié.";
  }
  if (error.code === "SYNC_CREDENTIAL_VERIFICATION_UNAVAILABLE") {
    return "La vérification IMT est temporairement indisponible. Ton réglage actuel est conservé.";
  }
  if (error.code === "SYNC_CREDENTIAL_VERIFICATION_INCOMPLETE") {
    return "La connexion IMT n'a pas produit une session réutilisable. Rien n'a été conservé.";
  }
  if (error.code === "SYNC_CREDENTIAL_ENCRYPTION_UNAVAILABLE") {
    return "Le mot de passe ne peut pas être protégé pour le moment.";
  }
  if (error.status === 429 && error.retryAfterSeconds) {
    const minutes = Math.max(1, Math.ceil(error.retryAfterSeconds / 60));
    return `Trop de tentatives. Réessaie dans environ ${minutes} minute${minutes > 1 ? "s" : ""}.`;
  }
  return error.message;
}

interface AutonomousSyncEnrollmentModalProps {
  open: boolean;
  interval: SyncInterval;
  adaptive: boolean;
  available?: boolean;
  updateExisting?: boolean;
  onClose: () => void;
  onSettings: (settings: SettingsResponse) => void;
  onActivated: () => void;
}

export function AutonomousSyncEnrollmentModal({
  open,
  interval,
  adaptive,
  available = true,
  updateExisting = false,
  onClose,
  onSettings,
  onActivated,
}: AutonomousSyncEnrollmentModalProps) {
  const passwordRef = useRef<HTMLInputElement>(null);
  const [visible, setVisible] = useState(false);
  const [hasPassword, setHasPassword] = useState(false);
  const [acknowledgements, setAcknowledgements] = useState([false, false, false]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [activationPending, setActivationPending] = useState(false);

  useEffect(() => {
    if (!open) return;
    setVisible(false);
    setHasPassword(false);
    setAcknowledgements([false, false, false]);
    setErrorMessage(null);
    setActivationPending(false);
    if (passwordRef.current) passwordRef.current.value = "";
  }, [open]);

  const configure = useMutation({
    mutationFn: async () => {
      let password = passwordRef.current?.value ?? "";
      if (!password) throw new Error("missing_password");
      try {
        const enrolled = await apiData(
          settingsEnrollSyncCredential({
            body: {
              password,
              consent_version: CONSENT_VERSION,
              acknowledge_encrypted_storage: true,
              acknowledge_worker_risk: true,
              acknowledge_irreversible_deletion: true,
            },
            throwOnError: throwOnApiError,
          }),
        );
        password = "";
        onSettings(enrolled);
        if (!enrolled.sync.autonomous.configured) {
          throw new Error("credential_not_configured");
        }
        try {
          const activated = await apiData(
            settingsUpdateSyncMode({
              body: {
                mode: "autonomous",
                interval_hours: interval,
                adaptive,
              },
              throwOnError: throwOnApiError,
            }),
          );
          onSettings(activated);
          return activated;
        } catch (error) {
          throw new ActivationPendingError(error);
        }
      } finally {
        password = "";
        if (passwordRef.current) passwordRef.current.value = "";
        setHasPassword(false);
      }
    },
    onSuccess: () => {
      onActivated();
      onClose();
    },
    onError: (error) => {
      const pending = error instanceof ActivationPendingError;
      setActivationPending(pending);
      setErrorMessage(enrollmentErrorMessage(error));
      window.requestAnimationFrame(() => passwordRef.current?.focus());
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setErrorMessage(null);
    configure.mutate();
  };
  const allAcknowledged = acknowledgements.every(Boolean);

  return (
    <Modal
      open={open}
      title={updateExisting ? "Mettre à jour le mot de passe" : "Activer la synchronisation autonome"}
      description="Cette option améliore la continuité, avec un risque serveur supérieur."
      onClose={onClose}
      size="large"
      className="autonomous-enrollment-modal"
      initialFocusRef={passwordRef}
    >
      <form className="autonomous-enrollment-form" onSubmit={submit} autoComplete="off">
        <div className="autonomous-risk-summary">
          <LockKeyhole size={19} />
          <p>
            Ton mot de passe IMT sera conservé sous une enveloppe chiffrée afin que le worker de synchronisation puisse
            recréer une session PASS/HUB. Une compromission du worker ou du serveur root pourrait toutefois l'exposer.
          </p>
        </div>
        <label>
          Mot de passe IMT
          <span className="password-field">
            <input
              ref={passwordRef}
              type={visible ? "text" : "password"}
              name="imt-autonomous-password"
              autoComplete="current-password"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              maxLength={512}
              required
              aria-invalid={Boolean(errorMessage)}
              aria-describedby={errorMessage ? "autonomous-enrollment-error" : undefined}
              onInput={() => setHasPassword(Boolean(passwordRef.current?.value))}
            />
            <button
              className="field-icon"
              type="button"
              aria-label={visible ? "Masquer le mot de passe" : "Afficher le mot de passe"}
              aria-pressed={visible}
              onClick={() => setVisible((current) => !current)}
            >
              {visible ? <EyeOff size={18} /> : <Eye size={18} />}
            </button>
          </span>
        </label>
        <fieldset className="autonomous-consents">
          <legend>Consentement explicite</legend>
          {[
            "Je comprends que mon mot de passe IMT sera conservé sous une enveloppe chiffrée.",
            "Je comprends qu'une compromission du worker de synchronisation ou du serveur pourrait exposer ce mot de passe.",
            "Je comprends que quitter ce mode supprimera irréversiblement le mot de passe conservé.",
          ].map((label, index) => (
            <label key={label}>
              <input
                type="checkbox"
                checked={acknowledgements[index]}
                onChange={(event) =>
                  setAcknowledgements((current) =>
                    current.map((value, itemIndex) => (itemIndex === index ? event.target.checked : value)),
                  )
                }
              />
              <span>{label}</span>
            </label>
          ))}
        </fieldset>
        {errorMessage && (
          <div
            id="autonomous-enrollment-error"
            className={`autonomous-enrollment-error${activationPending ? " is-pending" : ""}`}
            role="alert"
          >
            <ShieldAlert size={18} />
            <span>
              {errorMessage}
              {activationPending &&
                " Tu peux terminer l'activation sans ressaisir le mot de passe depuis les paramètres."}
            </span>
          </div>
        )}
        <footer className="modal-actions">
          <button className="secondary-button" type="button" onClick={onClose} disabled={configure.isPending}>
            Annuler
          </button>
          <button
            className="primary-button"
            type="submit"
            disabled={!available || !hasPassword || !allAcknowledged || configure.isPending}
          >
            {configure.isPending ? <span className="spinner" /> : <Check size={17} />}
            Vérifier et activer
          </button>
        </footer>
      </form>
    </Modal>
  );
}
