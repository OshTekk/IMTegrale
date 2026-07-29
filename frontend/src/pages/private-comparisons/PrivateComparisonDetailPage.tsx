import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, LockKeyhole, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { privateComparisonsDeletePrivateComparison } from "../../generated/api/sdk.gen";
import { EmptyState } from "../../components/EmptyState";
import { useToast } from "../../components/Toast";
import { formatDate } from "../../lib/format";
import { apiData, throwOnApiError } from "../../lib/generatedApi";
import { queryKeys, usePrivateComparison, useSession } from "../../lib/queries";
import { PrivateComparisonCommonUes } from "./PrivateComparisonCommonUes";
import { PrivateComparisonConfirmModal } from "./PrivateComparisonConfirmModal";
import { PrivateComparisonSummary } from "./PrivateComparisonSummary";
import {
  freshnessLabel,
  privateComparisonErrorMessage,
  privateComparisonUnavailable,
  validPrivateComparisonPublicId,
} from "./privateComparisonPresentation";

export function PrivateComparisonDetailPage() {
  const { publicId } = useParams();
  const validPublicId = validPrivateComparisonPublicId(publicId) ? publicId : null;
  const session = useSession();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { showToast } = useToast();
  const accountId = session.data?.account?.id ?? "anonymous";
  const detail = usePrivateComparison(validPublicId, Boolean(validPublicId));
  const [revokeOpen, setRevokeOpen] = useState(false);
  const unavailable = !validPublicId || privateComparisonUnavailable(detail.error);

  useEffect(() => {
    document.title = detail.data
      ? `Comparaison avec ${detail.data.other.identity.official_name} · IMTégrale`
      : "Comparaison privée · IMTégrale";
    return () => {
      document.title = "Comparaisons privées · IMTégrale";
    };
  }, [detail.data]);

  useEffect(() => {
    if (!validPublicId || !privateComparisonUnavailable(detail.error)) return;
    queryClient.removeQueries({ queryKey: queryKeys.privateComparison(accountId, validPublicId), exact: true });
  }, [accountId, detail.error, queryClient, validPublicId]);

  useEffect(
    () => () => {
      if (!validPublicId) return;
      queryClient.removeQueries({ queryKey: queryKeys.privateComparison(accountId, validPublicId), exact: true });
    },
    [accountId, queryClient, validPublicId],
  );

  const revoke = useMutation({
    mutationFn: () =>
      apiData(
        privateComparisonsDeletePrivateComparison({
          path: { public_id: validPublicId! },
          throwOnError: throwOnApiError,
        }),
      ),
    onSuccess: async () => {
      if (validPublicId) {
        queryClient.removeQueries({ queryKey: queryKeys.privateComparison(accountId, validPublicId), exact: true });
      }
      await queryClient.invalidateQueries({ queryKey: queryKeys.privateComparisons(accountId) });
      showToast("Comparaison révoquée.");
      navigate("/comparisons", { replace: true });
    },
    onError: (error) => showToast(privateComparisonErrorMessage(error, "conflict"), "error"),
  });

  if (unavailable) {
    return (
      <EmptyState
        title="Cette comparaison n’est plus disponible"
        detail="Elle peut être expirée, révoquée ou ne pas appartenir à ce compte."
        action={
          <Link className="secondary-button" to="/comparisons">
            Revenir aux comparaisons
          </Link>
        }
      />
    );
  }
  if (detail.isPending) {
    return (
      <div className="private-comparison-loading" role="status">
        Chargement de la comparaison…
      </div>
    );
  }
  if (detail.error || !detail.data) {
    return (
      <EmptyState
        title="Impossible de charger la comparaison"
        detail={privateComparisonErrorMessage(detail.error)}
        action={
          <button className="secondary-button" type="button" onClick={() => void detail.refetch()}>
            Réessayer
          </button>
        }
      />
    );
  }

  const comparison = detail.data;
  return (
    <div className="private-comparison-detail-page">
      <Link className="private-comparison-back" to="/comparisons">
        <ArrowLeft size={18} aria-hidden="true" /> Revenir aux comparaisons
      </Link>
      <header className="private-comparison-detail-header">
        <div>
          <p className="private-comparisons-eyebrow">Comparaison active</p>
          <h2>Comparaison avec {comparison.other.identity.official_name}</h2>
          <p>Disponible jusqu’au {formatDate(comparison.expires_at, false)}.</p>
        </div>
        <button className="danger-button" type="button" onClick={() => setRevokeOpen(true)}>
          Mettre fin à la comparaison
        </button>
      </header>

      <section className="private-comparison-privacy-reminder" aria-label="Confidentialité">
        <LockKeyhole size={21} aria-hidden="true" />
        <div>
          <h3>Privée et bilatérale</h3>
          <p>Visible uniquement par les deux participants tant que la comparaison reste active.</p>
        </div>
        <div className="private-comparison-freshness-pair">
          <span>
            <ShieldCheck size={16} aria-hidden="true" /> Toi : {freshnessLabel(comparison.current.summary.freshness)}
          </span>
          <span>
            <ShieldCheck size={16} aria-hidden="true" /> Autre participant :{" "}
            {freshnessLabel(comparison.other.summary.freshness)}
          </span>
        </div>
      </section>

      <PrivateComparisonSummary current={comparison.current} other={comparison.other} />
      <PrivateComparisonCommonUes values={comparison.common_ues} />

      <PrivateComparisonConfirmModal
        open={revokeOpen}
        title="Mettre fin à cette comparaison ?"
        description="Tu ne pourras plus consulter les résultats partagés dans cette comparaison. Les notes et les comptes ne seront pas supprimés."
        confirmLabel="Mettre fin"
        pending={revoke.isPending}
        onCancel={() => setRevokeOpen(false)}
        onConfirm={() => revoke.mutate()}
      />
    </div>
  );
}
