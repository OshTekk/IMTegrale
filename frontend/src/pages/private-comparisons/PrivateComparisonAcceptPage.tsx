import { useQueryClient } from "@tanstack/react-query";
import { Clock3, ShieldCheck, UserRoundCheck } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
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
import { queryKeys, useSession } from "../../lib/queries";
import {
  PRIVATE_COMPARISON_DOCUMENT_TITLE,
  primarySessionScope,
  useSecurityDocumentTitle,
} from "../../lib/securityScope";
import { PrivateComparisonConfirmModal } from "./PrivateComparisonConfirmModal";
import {
  emptyPrivateComparisonConsent,
  PrivateComparisonConsent,
  privateComparisonConsentComplete,
  type PrivateComparisonConsentState,
} from "./PrivateComparisonConsent";
import { PrivateComparisonScope } from "./PrivateComparisonScope";
import {
  invitationFromFragment,
  privateComparisonErrorMessage,
  usablePrivateComparisonConsentManifest,
} from "./privateComparisonPresentation";

type AcceptPageState = "checking" | "preview" | "missing" | "unavailable";

export function PrivateComparisonAcceptPage() {
  useSecurityDocumentTitle(PRIVATE_COMPARISON_DOCUMENT_TITLE);
  const session = useSession();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { showToast } = useToast();
  const accountId = session.data?.account?.id ?? "anonymous";
  const sessionScope = primarySessionScope(session.data);
  const tokenRef = useRef<string | null>(null);
  const requestRef = useRef<AbortController | null>(null);
  const processedRef = useRef(false);
  const mountedRef = useRef(false);
  const sessionScopeRef = useRef(sessionScope);
  const [state, setState] = useState<AcceptPageState>("checking");
  const [preview, setPreview] = useState<PrivateComparisonInvitationPreviewResponse | null>(null);
  const [consent, setConsent] = useState<PrivateComparisonConsentState>(emptyPrivateComparisonConsent);
  const [pendingAction, setPendingAction] = useState<"accept" | "decline" | null>(null);
  const [declineOpen, setDeclineOpen] = useState(false);

  const clearToken = () => {
    tokenRef.current = null;
  };

  useLayoutEffect(() => {
    mountedRef.current = true;
    if (!processedRef.current) {
      processedRef.current = true;
      const fragment = invitationFromFragment(window.location.hash);
      const cleanUrl = `${window.location.pathname}${window.location.search}`;
      window.history.replaceState(window.history.state, "", cleanUrl);
      if (fragment.state !== "valid") {
        setState(fragment.state === "missing" ? "missing" : "unavailable");
      } else {
        tokenRef.current = fragment.token;
        const request = new AbortController();
        requestRef.current = request;
        void apiData(
          privateComparisonsPreviewPrivateComparisonInvitation({
            body: { token: fragment.token },
            signal: request.signal,
            throwOnError: throwOnApiError,
          }),
        )
          .then((value) => {
            if (!mountedRef.current) return;
            if (
              value.consent_version !== value.consent_manifest.consent_version ||
              !usablePrivateComparisonConsentManifest(value.consent_manifest)
            ) {
              throw new Error("Private comparison consent manifest mismatch");
            }
            setPreview(value);
            setState("preview");
          })
          .catch((error: unknown) => {
            if (!mountedRef.current || request.signal.aborted) return;
            clearToken();
            setState("unavailable");
            if (import.meta.env.DEV && error instanceof Error && error.name === "AbortError") return;
          });
      }
    }

    return () => {
      mountedRef.current = false;
      queueMicrotask(() => {
        if (mountedRef.current) return;
        requestRef.current?.abort();
        requestRef.current = null;
        clearToken();
      });
    };
  }, []);

  useEffect(() => {
    if (sessionScopeRef.current === sessionScope) return;
    sessionScopeRef.current = sessionScope;
    requestRef.current?.abort();
    requestRef.current = null;
    clearToken();
    setPreview(null);
    setState("unavailable");
  }, [sessionScope]);

  const accept = async () => {
    const token = tokenRef.current;
    if (!token || !preview || !privateComparisonConsentComplete(consent) || pendingAction) return;
    setPendingAction("accept");
    try {
      const relation = await apiData(
        privateComparisonsAcceptPrivateComparisonInvitation({
          body: {
            token,
            consent_version: preview.consent_manifest.consent_version,
            acknowledge_identity_visibility: consent.identity,
            acknowledge_academic_scope: consent.academic,
            acknowledge_copy_risk: consent.copyRisk,
          },
          throwOnError: throwOnApiError,
        }),
      );
      clearToken();
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.privateComparisons(accountId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.privateComparisonInvitations(accountId) }),
      ]);
      showToast("Comparaison acceptée.");
      navigate(`/comparisons/${relation.public_id}`, { replace: true });
    } catch (error) {
      clearToken();
      setPreview(null);
      setState("unavailable");
      showToast(privateComparisonErrorMessage(error, "conflict"), "error");
    } finally {
      setPendingAction(null);
    }
  };

  const decline = async () => {
    const token = tokenRef.current;
    if (!token || pendingAction) return;
    setPendingAction("decline");
    try {
      await apiData(
        privateComparisonsDeclinePrivateComparisonInvitation({
          body: { token },
          throwOnError: throwOnApiError,
        }),
      );
      clearToken();
      setDeclineOpen(false);
      showToast("Invitation refusée.");
      navigate("/comparisons", { replace: true });
    } catch {
      clearToken();
      setPreview(null);
      setDeclineOpen(false);
      setState("unavailable");
    } finally {
      setPendingAction(null);
    }
  };

  const cancel = () => {
    clearToken();
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
          <UserRoundCheck size={26} aria-hidden="true" />
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
      <PrivateComparisonConsent value={consent} onChange={setConsent} legend="Confirme ton accord pour accepter" />
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
