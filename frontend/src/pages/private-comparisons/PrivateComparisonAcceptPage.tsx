import { useQueryClient } from "@tanstack/react-query";
import { Clock3, ShieldCheck } from "lucide-react";
import { useLayoutEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { PrivateComparisonInvitationPreviewResponse } from "../../generated/api/types.gen";
import {
  privateComparisonsAcceptPrivateComparisonInvitation,
  privateComparisonsDeclinePrivateComparisonInvitation,
  privateComparisonsPreviewPrivateComparisonInvitation,
} from "../../generated/api/sdk.gen";
import { EmptyState } from "../../components/EmptyState";
import { useToast } from "../../components/Toast";
import { formatDate } from "../../lib/format";
import { apiData, throwOnApiError } from "../../lib/generatedApi";
import { armPrivateDeadline } from "../../lib/privateComparisonLease";
import { consumeInvitationFragment, initializeInvitationFragmentOwner } from "../../lib/invitationFragmentOwner";
import { queryKeys, useSession } from "../../lib/queries";
import {
  PRIVATE_COMPARISON_DOCUMENT_TITLE,
  primarySessionScope,
  useSessionBoundOneShot,
  useSecurityDocumentTitle,
} from "../../lib/securityScope";
import { useSessionSecurity, useVerifiedSessionRequest } from "../../lib/sessionSecurity";
import { PrivateComparisonConfirmModal } from "./PrivateComparisonConfirmModal";
import {
  emptyPrivateComparisonConsent,
  PrivateComparisonConsent,
  privateComparisonConsentComplete,
  type PrivateComparisonConsentState,
} from "./PrivateComparisonConsent";
import { PrivateComparisonScope } from "./PrivateComparisonScope";
import { privateComparisonErrorMessage, usablePrivateComparisonConsentManifest } from "./privateComparisonPresentation";

type AcceptPageState = "checking" | "preview" | "missing" | "unavailable";

export function PrivateComparisonAcceptPage() {
  useSecurityDocumentTitle(PRIVATE_COMPARISON_DOCUMENT_TITLE);
  const session = useSession();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { showToast } = useToast();
  const accountId = session.data?.account?.id ?? "anonymous";
  const sessionScope = primarySessionScope(session.data);
  const runVerifiedRequest = useVerifiedSessionRequest();
  const sessionSecurity = useSessionSecurity();
  const processedRef = useRef(false);
  const [state, setState] = useState<AcceptPageState>("checking");
  const [preview, setPreview] = useState<PrivateComparisonInvitationPreviewResponse | null>(null);
  const [consent, setConsent] = useState<PrivateComparisonConsentState>(emptyPrivateComparisonConsent);
  const [pendingAction, setPendingAction] = useState<"accept" | "decline" | null>(null);
  const [declineOpen, setDeclineOpen] = useState(false);
  const bearer = useSessionBoundOneShot<string>(sessionScope, true, () => {
    setPreview(null);
    setConsent(emptyPrivateComparisonConsent);
    setPendingAction(null);
    setDeclineOpen(false);
    setState("unavailable");
  });
  const bearerRef = useRef(bearer);
  const runVerifiedRequestRef = useRef(runVerifiedRequest);
  bearerRef.current = bearer;
  runVerifiedRequestRef.current = runVerifiedRequest;

  useLayoutEffect(() => {
    if (!preview) return;
    return armPrivateDeadline(preview.expires_at, () => bearerRef.current.purge());
  }, [preview]);

  useLayoutEffect(() => {
    if (processedRef.current) return;
    processedRef.current = true;
    const owner = initializeInvitationFragmentOwner();
    owner.observe({
      securityState: sessionSecurity.status,
      authEpoch: sessionSecurity.authEpoch,
      session: session.data,
      sessionScope: sessionSecurity.scope,
      sessionExpiresAt: session.data?.session_expires_at ?? null,
      monotonicDeadline: null,
      wallDeadline: null,
      currentRequestSequence: 0,
      latestCommittedSequence: 0,
      transitionReason: null,
    });
    const token = consumeInvitationFragment(sessionSecurity.authEpoch, sessionScope);
    if (!token) {
      setState(owner.consumeRejectedFragment() ? "unavailable" : "missing");
      return;
    }
    const currentBearer = bearerRef.current;
    const request = currentBearer.begin();
    if (!currentBearer.set(request, token)) {
      currentBearer.purge();
      return;
    }
    void runVerifiedRequestRef
      .current(request.scope, (signal) =>
        apiData(
          privateComparisonsPreviewPrivateComparisonInvitation({
            headers: { "X-IMTEGRALE-SESSION-BINDING": request.scope },
            body: { token },
            signal,
            throwOnError: throwOnApiError,
          }),
        ),
      )
      .then((value) => {
        if (!currentBearer.finish(request)) return;
        if (
          value.consent_version !== value.consent_manifest.consent_version ||
          !usablePrivateComparisonConsentManifest(value.consent_manifest, "acceptor")
        ) {
          throw new Error("Private comparison consent manifest mismatch");
        }
        setPreview(value);
        setState("preview");
      })
      .catch(() => {
        if (currentBearer.usable(request)) currentBearer.purge();
      });
  }, [session.data, sessionScope, sessionSecurity.authEpoch, sessionSecurity.scope, sessionSecurity.status]);

  const accept = async () => {
    const token = bearer.current();
    const requestScope = sessionScope;
    if (!token || !preview || !privateComparisonConsentComplete(consent) || pendingAction) return;
    setPendingAction("accept");
    try {
      const relation = await runVerifiedRequest(requestScope, (signal) =>
        apiData(
          privateComparisonsAcceptPrivateComparisonInvitation({
            headers: { "X-IMTEGRALE-SESSION-BINDING": requestScope },
            body: {
              token,
              consent_version: preview.consent_manifest.consent_version,
              actor_role: "acceptor",
              manifest_digest: preview.consent_manifest.manifest_digest,
              acknowledge_identity_visibility: consent.identity,
              acknowledge_academic_scope: consent.academic,
              acknowledge_copy_risk: consent.copyRisk,
            },
            signal,
            throwOnError: throwOnApiError,
          }),
        ),
      );
      if (bearer.current() !== token) return;
      bearer.clear();
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.privateComparisons(accountId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.privateComparisonInvitations(accountId) }),
      ]);
      showToast("Comparaison acceptée.");
      navigate(`/comparisons/${relation.public_id}`, { replace: true });
    } catch (error) {
      if (bearer.current() !== token) return;
      bearer.purge();
      showToast(privateComparisonErrorMessage(error, "conflict"), "error");
    }
  };

  const decline = async () => {
    const token = bearer.current();
    const requestScope = sessionScope;
    if (!token || pendingAction) return;
    setPendingAction("decline");
    try {
      await runVerifiedRequest(requestScope, (signal) =>
        apiData(
          privateComparisonsDeclinePrivateComparisonInvitation({
            headers: { "X-IMTEGRALE-SESSION-BINDING": requestScope },
            body: { token },
            signal,
            throwOnError: throwOnApiError,
          }),
        ),
      );
      if (bearer.current() !== token) return;
      bearer.clear();
      setDeclineOpen(false);
      showToast("Invitation refusée.");
      navigate("/comparisons", { replace: true });
    } catch {
      if (bearer.current() === token) bearer.purge();
    }
  };

  const cancel = () => {
    bearer.purge();
    navigate("/comparisons", { replace: true });
  };

  if (state === "checking") {
    return (
      <div className="private-comparison-loading" role="status">
        Vérification de l’invitation…
      </div>
    );
  }
  if (state === "missing") {
    return (
      <EmptyState
        title="Invitation à rouvrir"
        detail="Rouvre le lien d’invitation original pour continuer. Aucun secret n’est conservé après un rechargement."
        action={
          <Link className="secondary-button" to="/comparisons">
            Revenir aux comparaisons
          </Link>
        }
      />
    );
  }
  if (state === "unavailable" || !preview) {
    return (
      <EmptyState
        title="Cette invitation n’est plus disponible"
        detail="Elle peut avoir expiré, déjà été utilisée ou ne pas convenir à ce compte."
        action={
          <Link className="secondary-button" to="/comparisons">
            Revenir aux comparaisons
          </Link>
        }
      />
    );
  }

  return (
    <div className="private-comparison-accept-page">
      <header className="private-comparison-accept-header">
        <span className="private-comparison-accept-icon">
          <ShieldCheck size={26} aria-hidden="true" />
        </span>
        <div>
          <p className="private-comparisons-eyebrow">Invitation privée</p>
          <h2>{preview.creator.official_name} te propose une comparaison</h2>
          <p>Consulte précisément le périmètre avant de donner ton accord.</p>
        </div>
      </header>

      <div className="private-comparison-invitation-meta">
        <p>
          <Clock3 size={18} aria-hidden="true" /> Le lien expire le {formatDate(preview.expires_at, false)}.
        </p>
        <p>
          <ShieldCheck size={18} aria-hidden="true" /> La comparaison durera {preview.relationship_duration_days} jours
          après acceptation.
        </p>
      </div>

      <PrivateComparisonScope manifest={preview.consent_manifest} compact />
      <PrivateComparisonConsent
        manifest={preview.consent_manifest}
        value={consent}
        onChange={setConsent}
        legend="Confirme ton accord pour accepter"
      />
      <div className="private-comparison-accept-actions">
        <button
          className="secondary-button"
          type="button"
          onClick={() => setDeclineOpen(true)}
          disabled={Boolean(pendingAction)}
        >
          Refuser
        </button>
        <button className="secondary-button" type="button" onClick={cancel} disabled={Boolean(pendingAction)}>
          Annuler
        </button>
        <button
          className="primary-button"
          type="button"
          onClick={() => void accept()}
          disabled={Boolean(pendingAction) || !privateComparisonConsentComplete(consent)}
        >
          {pendingAction === "accept" ? "Acceptation…" : "Accepter la comparaison"}
        </button>
      </div>

      <PrivateComparisonConfirmModal
        open={declineOpen}
        title="Refuser cette invitation ?"
        description="Le lien cessera de fonctionner. Le créateur ne verra pas ton identité."
        confirmLabel="Refuser l’invitation"
        pending={pendingAction === "decline"}
        onCancel={() => setDeclineOpen(false)}
        onConfirm={() => void decline()}
      />
    </div>
  );
}
