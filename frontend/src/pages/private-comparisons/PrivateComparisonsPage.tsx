import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Link2, Plus, ShieldCheck, UserRoundCheck } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type {
  PrivateComparisonInvitationResponse,
  PrivateComparisonRelationResponse,
} from "../../generated/api/types.gen";
import {
  privateComparisonsDeletePrivateComparison,
  privateComparisonsDeletePrivateComparisonInvitation,
} from "../../generated/api/sdk.gen";
import { EmptyState } from "../../components/EmptyState";
import { useToast } from "../../components/Toast";
import { formatDate } from "../../lib/format";
import { apiData, throwOnApiError } from "../../lib/generatedApi";
import { queryKeys, usePrivateComparisonInvitations, usePrivateComparisons, useSession } from "../../lib/queries";
import { PrivateComparisonConfirmModal } from "./PrivateComparisonConfirmModal";
import { PrivateComparisonInvitationModal } from "./PrivateComparisonInvitationModal";
import { PrivateComparisonScope } from "./PrivateComparisonScope";
import {
  comparisonStatusLabel,
  freshnessLabel,
  invitationStatusLabel,
  privateComparisonErrorMessage,
} from "./privateComparisonPresentation";

function InvitationItem({
  invitation,
  onRevoke,
}: {
  invitation: PrivateComparisonInvitationResponse;
  onRevoke: (invitation: PrivateComparisonInvitationResponse) => void;
}) {
  return (
    <article className="private-comparison-list-item">
      <div className="private-comparison-item-icon" aria-hidden="true">
        <Link2 size={19} />
      </div>
      <div className="private-comparison-item-copy">
        <div className="private-comparison-item-title">
          <h3>Invitation créée le {formatDate(invitation.created_at, false)}</h3>
          <span className={`status-pill ${invitation.status === "active" ? "success" : "neutral"}`}>
            {invitationStatusLabel(invitation.status)}
          </span>
        </div>
        <p>
          Expire le {formatDate(invitation.expires_at, false)} · comparaison prévue pour{" "}
          {invitation.relationship_duration_days} jours
        </p>
        {invitation.status === "active" && (
          <p className="private-comparison-item-note">Le lien ne peut pas être affiché à nouveau.</p>
        )}
      </div>
      {invitation.status === "active" && (
        <button className="secondary-button" type="button" onClick={() => onRevoke(invitation)}>
          Révoquer
        </button>
      )}
    </article>
  );
}

function ComparisonItem({
  comparison,
  onRevoke,
}: {
  comparison: PrivateComparisonRelationResponse;
  onRevoke: (comparison: PrivateComparisonRelationResponse) => void;
}) {
  const active = comparison.status === "active";
  return (
    <article className="private-comparison-list-item">
      <div className="private-comparison-item-icon" aria-hidden="true">
        <UserRoundCheck size={19} />
      </div>
      <div className="private-comparison-item-copy">
        <div className="private-comparison-item-title">
          <h3>{comparison.other_participant.official_name}</h3>
          <span className={`status-pill ${active ? "success" : "neutral"}`}>
            {comparisonStatusLabel(comparison.status)}
          </span>
        </div>
        <p>
          {active
            ? `Disponible jusqu’au ${formatDate(comparison.expires_at, false)}`
            : `Fin le ${formatDate(comparison.expires_at, false)}`}
          {active ? ` · ${freshnessLabel(comparison.freshness)}` : ""}
        </p>
      </div>
      {active && (
        <div className="private-comparison-item-actions">
          <Link className="primary-button" to={`/comparisons/${comparison.public_id}`}>
            Voir la comparaison
          </Link>
          <button className="secondary-button" type="button" onClick={() => onRevoke(comparison)}>
            Mettre fin
          </button>
        </div>
      )}
    </article>
  );
}

export function PrivateComparisonsPage() {
  const session = useSession();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const accountId = session.data?.account?.id ?? "anonymous";
  const invitations = usePrivateComparisonInvitations();
  const comparisons = usePrivateComparisons();
  const [creationOpen, setCreationOpen] = useState(false);
  const [invitationToRevoke, setInvitationToRevoke] = useState<PrivateComparisonInvitationResponse | null>(null);
  const [comparisonToRevoke, setComparisonToRevoke] = useState<PrivateComparisonRelationResponse | null>(null);

  const activeComparisons = useMemo(
    () => comparisons.data?.comparisons.filter((value) => value.status === "active") ?? [],
    [comparisons.data],
  );
  const comparisonHistory = useMemo(
    () => comparisons.data?.comparisons.filter((value) => value.status !== "active") ?? [],
    [comparisons.data],
  );

  const revokeInvitation = useMutation({
    mutationFn: (publicId: string) =>
      apiData(
        privateComparisonsDeletePrivateComparisonInvitation({
          path: { public_id: publicId },
          throwOnError: throwOnApiError,
        }),
      ),
    onSuccess: async () => {
      setInvitationToRevoke(null);
      await queryClient.invalidateQueries({ queryKey: queryKeys.privateComparisonInvitations(accountId) });
      showToast("Invitation révoquée.");
    },
    onError: () => showToast("Impossible de révoquer cette invitation pour le moment.", "error"),
  });

  const revokeComparison = useMutation({
    mutationFn: (publicId: string) =>
      apiData(
        privateComparisonsDeletePrivateComparison({
          path: { public_id: publicId },
          throwOnError: throwOnApiError,
        }),
      ),
    onSuccess: async (_, publicId) => {
      queryClient.removeQueries({ queryKey: queryKeys.privateComparison(accountId, publicId), exact: true });
      setComparisonToRevoke(null);
      await queryClient.invalidateQueries({ queryKey: queryKeys.privateComparisons(accountId) });
      showToast("Comparaison révoquée.");
    },
    onError: () => showToast("Impossible de mettre fin à cette comparaison pour le moment.", "error"),
  });

  const loadError = invitations.error ?? comparisons.error;
  return (
    <div className="private-comparisons-page">
      <header className="private-comparisons-intro">
        <div>
          <p className="private-comparisons-eyebrow">Partage bilatéral</p>
          <h2>Des résultats officiels comparés à deux</h2>
          <p>
            Compare tes résultats avec un étudiant de ton cursus et de ta promotion, sans classement public. Chaque
            comparaison nécessite votre accord et peut être révoquée à tout moment.
          </p>
        </div>
        <button className="primary-button" type="button" onClick={() => setCreationOpen(true)}>
          <Plus size={18} aria-hidden="true" /> Créer une invitation
        </button>
      </header>

      <PrivateComparisonScope />

      {loadError && (
        <div className="private-comparison-alert" role="alert">
          {privateComparisonErrorMessage(loadError)}
        </div>
      )}

      <section className="private-comparison-list-section" aria-labelledby="active-comparisons-title">
        <header className="private-comparison-section-heading">
          <ShieldCheck size={22} aria-hidden="true" />
          <div>
            <h2 id="active-comparisons-title">Comparaisons actives</h2>
            <p>Les résultats restent disponibles uniquement pendant la durée convenue.</p>
          </div>
        </header>
        {comparisons.isPending ? (
          <div className="private-comparison-loading" role="status">
            Chargement des comparaisons…
          </div>
        ) : activeComparisons.length ? (
          <div className="private-comparison-list">
            {activeComparisons.map((comparison) => (
              <ComparisonItem key={comparison.public_id} comparison={comparison} onRevoke={setComparisonToRevoke} />
            ))}
          </div>
        ) : (
          <EmptyState
            title="Aucune comparaison active"
            detail="Crée une invitation pour comparer tes résultats avec un étudiant de ton cursus et de ta promotion."
            action={
              <button className="secondary-button" type="button" onClick={() => setCreationOpen(true)}>
                Créer une invitation
              </button>
            }
          />
        )}
      </section>

      <section className="private-comparison-list-section" aria-labelledby="invitations-title">
        <header className="private-comparison-section-heading">
          <Link2 size={22} aria-hidden="true" />
          <div>
            <h2 id="invitations-title">Invitations créées</h2>
            <p>Une invitation active peut être révoquée, mais son lien secret ne peut jamais être réaffiché.</p>
          </div>
        </header>
        {invitations.isPending ? (
          <div className="private-comparison-loading" role="status">
            Chargement des invitations…
          </div>
        ) : invitations.data?.invitations.length ? (
          <div className="private-comparison-list">
            {invitations.data.invitations.map((invitation) => (
              <InvitationItem key={invitation.public_id} invitation={invitation} onRevoke={setInvitationToRevoke} />
            ))}
          </div>
        ) : (
          <p className="private-comparison-empty-copy">Aucune invitation créée.</p>
        )}
      </section>

      {comparisonHistory.length > 0 && (
        <section className="private-comparison-list-section" aria-labelledby="comparison-history-title">
          <header className="private-comparison-section-heading">
            <CalendarClock size={22} aria-hidden="true" />
            <div>
              <h2 id="comparison-history-title">Historique</h2>
              <p>Les relations terminées restent visibles sans leurs données académiques.</p>
            </div>
          </header>
          <div className="private-comparison-list is-history">
            {comparisonHistory.map((comparison) => (
              <ComparisonItem key={comparison.public_id} comparison={comparison} onRevoke={setComparisonToRevoke} />
            ))}
          </div>
        </section>
      )}

      <PrivateComparisonInvitationModal
        open={creationOpen}
        onClose={() => setCreationOpen(false)}
        onCreated={() => queryClient.invalidateQueries({ queryKey: queryKeys.privateComparisonInvitations(accountId) })}
      />
      <PrivateComparisonConfirmModal
        open={Boolean(invitationToRevoke)}
        title="Révoquer cette invitation ?"
        description="Le lien cessera immédiatement de fonctionner. Une comparaison déjà activée ne sera pas supprimée."
        confirmLabel="Révoquer l’invitation"
        pending={revokeInvitation.isPending}
        onCancel={() => setInvitationToRevoke(null)}
        onConfirm={() => invitationToRevoke && revokeInvitation.mutate(invitationToRevoke.public_id)}
      />
      <PrivateComparisonConfirmModal
        open={Boolean(comparisonToRevoke)}
        title="Mettre fin à cette comparaison ?"
        description="Tu ne pourras plus consulter les résultats partagés dans cette comparaison. Les notes et les comptes ne seront pas supprimés."
        confirmLabel="Mettre fin"
        pending={revokeComparison.isPending}
        onCancel={() => setComparisonToRevoke(null)}
        onConfirm={() => comparisonToRevoke && revokeComparison.mutate(comparisonToRevoke.public_id)}
      />
    </div>
  );
}
