import { Copy, Link2, LockKeyhole } from "lucide-react";
import { useRef, useState } from "react";
import type {
  PrivateComparisonConsentManifestResponse,
  PrivateComparisonInvitationCreatedResponse,
} from "../../generated/api/types.gen";
import { privateComparisonsCreatePrivateComparisonInvitation } from "../../generated/api/sdk.gen";
import { apiData, throwOnApiError } from "../../lib/generatedApi";
import { useSessionBoundOneShot } from "../../lib/securityScope";
import { Modal } from "../../components/Modal";
import {
  emptyPrivateComparisonConsent,
  PrivateComparisonConsent,
  privateComparisonConsentComplete,
  type PrivateComparisonConsentState,
} from "./PrivateComparisonConsent";
import {
  invitationUrl,
  PRIVATE_COMPARISON_DURATIONS,
  privateComparisonErrorMessage,
  usablePrivateComparisonConsentManifest,
  validInvitationToken,
} from "./privateComparisonPresentation";
import { PrivateComparisonScope } from "./PrivateComparisonScope";

export function PrivateComparisonInvitationModal({
  open,
  onClose,
  onCreated,
  manifest,
  manifestPending,
  sessionScope,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
  manifest: PrivateComparisonConsentManifestResponse | null;
  manifestPending: boolean;
  sessionScope: string;
}) {
  const [duration, setDuration] = useState<(typeof PRIVATE_COMPARISON_DURATIONS)[number]>(30);
  const [consent, setConsent] = useState<PrivateComparisonConsentState>(emptyPrivateComparisonConsent);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copyStatus, setCopyStatus] = useState("");
  const durationRef = useRef<HTMLSelectElement>(null);
  const oneShot = useSessionBoundOneShot<PrivateComparisonInvitationCreatedResponse>(sessionScope, open, () => {
    setCopyStatus("");
    setError(null);
    setPending(false);
    setConsent(emptyPrivateComparisonConsent);
    setDuration(30);
    onClose();
  });
  const usableManifest = manifest && usablePrivateComparisonConsentManifest(manifest) ? manifest : null;
  const scopedCreated = oneShot.value;

  const close = oneShot.purge;

  const create = async () => {
    if (!usableManifest || !privateComparisonConsentComplete(consent)) return;
    const requestManifestVersion = usableManifest.consent_version;
    const request = oneShot.begin();
    setPending(true);
    setError(null);
    try {
      const response = await apiData(
        privateComparisonsCreatePrivateComparisonInvitation({
          body: {
            consent_version: usableManifest.consent_version,
            acknowledge_identity_visibility: consent.identity,
            acknowledge_academic_scope: consent.academic,
            acknowledge_copy_risk: consent.copyRisk,
            duration_days: duration,
          },
          signal: request.controller.signal,
          throwOnError: throwOnApiError,
        }),
      );
      if (!oneShot.usable(request)) return;
      if (!validInvitationToken(response.token)) {
        throw new Error("Invalid private comparison invitation token");
      }
      if (
        response.consent_version !== requestManifestVersion ||
        !usablePrivateComparisonConsentManifest(response.consent_manifest) ||
        response.consent_manifest.consent_version !== requestManifestVersion
      ) {
        throw new Error("Private comparison consent manifest changed");
      }
      if (oneShot.set(request, response)) onCreated();
    } catch (caught) {
      if (!oneShot.usable(request)) return;
      setError(privateComparisonErrorMessage(caught, "create"));
    } finally {
      if (oneShot.finish(request)) setPending(false);
    }
  };

  const copy = async () => {
    const current = oneShot.current();
    if (!current) {
      oneShot.purge();
      return;
    }
    try {
      await navigator.clipboard.writeText(invitationUrl(current.token));
      if (oneShot.current() === current) setCopyStatus("Lien copié.");
    } catch {
      if (oneShot.current() === current) {
        setCopyStatus("Copie impossible. Sélectionne le lien puis copie-le manuellement.");
      }
    }
  };

  const link = scopedCreated ? invitationUrl(scopedCreated.token) : "";
  return (
    <Modal
      open={open}
      title={scopedCreated ? "Lien d’invitation créé" : "Créer une invitation"}
      description={
        scopedCreated
          ? "Ce lien ne s’affiche qu’une fois. Copie-le avant de fermer."
          : "Choisis la durée de la comparaison et confirme précisément son périmètre."
      }
      onClose={close}
      size="large"
      className="private-comparison-invitation-modal"
      initialFocusRef={scopedCreated ? undefined : durationRef}
    >
      {scopedCreated ? (
        <div className="private-comparison-one-shot">
          <div className="private-comparison-one-shot-notice">
            <LockKeyhole size={20} aria-hidden="true" />
            <p>
              S’il est perdu, révoque l’invitation et crée-en une nouvelle. Il est utilisable une seule fois et expire
              sous sept jours.
            </p>
          </div>
          <label htmlFor="private-comparison-created-link">Lien d’invitation</label>
          <div className="private-comparison-copy-row">
            <span className="private-comparison-link-field">
              <Link2 size={17} aria-hidden="true" />
              <input
                id="private-comparison-created-link"
                readOnly
                value={link}
                onFocus={(event) => event.currentTarget.select()}
              />
            </span>
            <button className="primary-button" type="button" onClick={copy}>
              <Copy size={18} aria-hidden="true" /> Copier le lien
            </button>
          </div>
          <p className="private-comparison-copy-status" aria-live="polite">
            {copyStatus}
          </p>
          <footer className="modal-actions">
            <button className="secondary-button" type="button" onClick={close}>
              Fermer
            </button>
          </footer>
        </div>
      ) : !usableManifest ? (
        <div className="private-comparison-manifest-unavailable">
          <p role={manifestPending ? "status" : "alert"}>
            {manifestPending
              ? "Chargement du périmètre de consentement…"
              : "Le périmètre de consentement est indisponible. Aucune invitation ne peut être créée."}
          </p>
          <footer className="modal-actions">
            <button className="secondary-button" type="button" onClick={close}>
              Fermer
            </button>
          </footer>
        </div>
      ) : (
        <form
          className="private-comparison-invitation-form"
          onSubmit={(event) => {
            event.preventDefault();
            void create();
          }}
        >
          <label className="private-comparison-duration" htmlFor="private-comparison-duration">
            <span>Durée de la comparaison après acceptation</span>
            <select
              ref={durationRef}
              id="private-comparison-duration"
              value={duration}
              onChange={(event) => setDuration(Number(event.target.value) as typeof duration)}
            >
              {PRIVATE_COMPARISON_DURATIONS.map((days) => (
                <option key={days} value={days}>
                  {days} jours{days === 30 ? " (recommandé)" : ""}
                </option>
              ))}
            </select>
          </label>
          <div className="private-comparison-invitation-facts">
            <p>Le lien expire sous sept jours et n’est utilisable qu’une fois.</p>
            <p>Le destinataire doit appartenir au même cursus et à la même promotion.</p>
            <p>La durée choisie commence uniquement après son acceptation.</p>
          </div>
          <PrivateComparisonScope manifest={usableManifest} compact />
          <PrivateComparisonConsent value={consent} onChange={setConsent} />
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <footer className="modal-actions">
            <button className="secondary-button" type="button" onClick={close} disabled={pending}>
              Annuler
            </button>
            <button
              className="primary-button"
              type="submit"
              disabled={pending || !privateComparisonConsentComplete(consent)}
            >
              {pending ? "Création…" : "Créer le lien"}
            </button>
          </footer>
        </form>
      )}
    </Modal>
  );
}
