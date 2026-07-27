import { CheckCircle2, Clock3, KeyRound, TriangleAlert } from "lucide-react";
import type { AutonomousSyncSettingsResponse, SyncMode } from "../../generated/api/types.gen";
import { formatDate } from "../../lib/format";

interface AutonomousSyncStatusProps {
  status: AutonomousSyncSettingsResponse;
  mode: SyncMode;
  disabled: boolean;
  canActivate: boolean;
  canEnroll: boolean;
  onActivate: () => void;
  onUpdate: () => void;
  onDelete: () => void;
}

export function AutonomousSyncStatus({
  status,
  mode,
  disabled,
  canActivate,
  canEnroll,
  onActivate,
  onUpdate,
  onDelete,
}: AutonomousSyncStatusProps) {
  if (!status.configured && !status.needs_reenrollment) return null;
  const pending = status.activation_pending && status.configured;
  const invalid = status.needs_reenrollment;
  return (
    <section className={`autonomous-status${invalid ? " is-warning" : ""}`} aria-live="polite">
      <span aria-hidden="true">
        {invalid ? <TriangleAlert size={20} /> : pending ? <Clock3 size={20} /> : <CheckCircle2 size={20} />}
      </span>
      <div>
        <h3>{invalid ? "Mot de passe à renouveler" : pending ? "Activation à terminer" : "Mot de passe protégé"}</h3>
        <dl>
          <div>
            <dt>Vérifié le</dt>
            <dd>{formatDate(status.verified_at)}</dd>
          </div>
          <div>
            <dt>Dernière utilisation</dt>
            <dd>{status.last_used_at ? formatDate(status.last_used_at) : "Pas encore utilisé"}</dd>
          </div>
          <div>
            <dt>Dernier succès</dt>
            <dd>
              {status.last_success_at ? formatDate(status.last_success_at) : "Aucun succès autonome pour le moment"}
            </dd>
          </div>
          {status.last_failure_at && (
            <div>
              <dt>Dernier échec</dt>
              <dd>{formatDate(status.last_failure_at)}</dd>
            </div>
          )}
        </dl>
        <div className="autonomous-status-actions">
          {pending && mode !== "autonomous" && (
            <button className="primary-button" type="button" onClick={onActivate} disabled={disabled || !canActivate}>
              <CheckCircle2 size={17} /> Terminer l'activation
            </button>
          )}
          <button className="secondary-button" type="button" onClick={onUpdate} disabled={disabled || !canEnroll}>
            <KeyRound size={17} /> {invalid ? "Renouveler le mot de passe" : "Mettre à jour le mot de passe"}
          </button>
          <button className="text-button danger-text-button" type="button" onClick={onDelete} disabled={disabled}>
            Supprimer le mot de passe conservé
          </button>
        </div>
      </div>
    </section>
  );
}
