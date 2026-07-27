import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Activity, CheckCircle2, Clock3, Info, LockKeyhole, RefreshCw, ShieldAlert, TriangleAlert } from "lucide-react";
import { useEffect, useId, useState } from "react";
import {
  settingsDeleteSyncCredential,
  settingsPurgePassAccess,
  settingsUpdateSyncMode,
} from "../../generated/api/sdk.gen";
import type { DashboardAccountResponse, SettingsResponse, SyncMode } from "../../generated/api/types.gen";
import { formatDate } from "../../lib/format";
import { apiData, throwOnApiError } from "../../lib/generatedApi";
import { queryKeys } from "../../lib/queries";
import { manualSyncMessage, useServerCountdown } from "../../lib/sync";
import { PassReconnectModal } from "../PassReconnectModal";
import { useToast } from "../Toast";
import { AutonomousSyncEnrollmentModal } from "./AutonomousSyncEnrollmentModal";
import { AutonomousSyncStatus } from "./AutonomousSyncStatus";
import { DeleteCredentialModal, LeaveAutonomousModal, PurgePassAccessModal } from "./SyncConfirmations";
import { SyncModeSelector } from "./SyncModeSelector";
import { SyncScheduleOptions, type SyncInterval } from "./SyncScheduleOptions";

interface SyncSettingsPanelProps {
  data: SettingsResponse;
  dashboardAccount: DashboardAccountResponse | undefined;
  isPrimaryOwner: boolean;
}

export function SyncSettingsPanel({ data, dashboardAccount, isPrimaryOwner }: SyncSettingsPanelProps) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const modeGroupName = useId();
  const [selectedMode, setSelectedMode] = useState<SyncMode>(data.sync.mode);
  const [interval, setInterval] = useState<SyncInterval>(data.sync.interval_hours);
  const [adaptive, setAdaptive] = useState(data.sync.adaptive);
  const [enrollmentOpen, setEnrollmentOpen] = useState(false);
  const [updatingCredential, setUpdatingCredential] = useState(false);
  const [leaveTarget, setLeaveTarget] = useState<Exclude<SyncMode, "autonomous"> | null>(null);
  const [deleteCredentialOpen, setDeleteCredentialOpen] = useState(false);
  const [purgeOpen, setPurgeOpen] = useState(false);
  const [passReconnectOpen, setPassReconnectOpen] = useState(false);
  const manualSyncRemaining = useServerCountdown(dashboardAccount?.manual_sync);

  useEffect(() => {
    setSelectedMode(data.sync.mode);
    setInterval(data.sync.interval_hours);
    setAdaptive(data.sync.adaptive);
  }, [data.sync.adaptive, data.sync.interval_hours, data.sync.mode]);

  const applySettings = (next: SettingsResponse) => {
    const accountId = queryClient.getQueryData<{ account?: { id: string } }>(queryKeys.session)?.account?.id;
    if (accountId) queryClient.setQueryData(queryKeys.settings(accountId), next);
    void queryClient.invalidateQueries({ queryKey: queryKeys.account });
  };

  const modeMutation = useMutation({
    mutationFn: (mode: SyncMode) =>
      apiData(
        settingsUpdateSyncMode({
          body: { mode, interval_hours: interval, adaptive },
          throwOnError: throwOnApiError,
        }),
      ),
    onSuccess: (next) => {
      applySettings(next);
      setLeaveTarget(null);
      showToast(
        `Mode « ${next.sync.mode === "manual" ? "À la demande" : next.sync.mode === "session_only" ? "Session privée" : "Automatique autonome"} » activé`,
      );
    },
    onError: (error) => showToast(error.message, "error"),
  });
  const deleteCredential = useMutation({
    mutationFn: () => apiData(settingsDeleteSyncCredential({ throwOnError: throwOnApiError })),
    onSuccess: (next) => {
      applySettings(next);
      setDeleteCredentialOpen(false);
      showToast("Mot de passe conservé supprimé");
    },
    onError: (error) => showToast(error.message, "error"),
  });
  const purgePassAccess = useMutation({
    mutationFn: () => apiData(settingsPurgePassAccess({ throwOnError: throwOnApiError })),
    onSuccess: (next) => {
      applySettings(next);
      setPurgeOpen(false);
      showToast("Accès PASS/HUB supprimés");
    },
    onError: (error) => showToast(error.message, "error"),
  });

  const passAccess = data.sync.pass_access;
  const serviceSession = data.sync.service_session;
  if (!passAccess?.quota || !serviceSession) {
    return (
      <section className="settings-panel sync-settings">
        <div className="error-panel">
          <TriangleAlert size={22} /> État de synchronisation indisponible.
        </div>
      </section>
    );
  }

  const saveMode = () => {
    if (selectedMode === "autonomous") {
      if (!data.sync.autonomous.configured || data.sync.autonomous.needs_reenrollment) {
        setUpdatingCredential(data.sync.autonomous.configured);
        setEnrollmentOpen(true);
        return;
      }
      modeMutation.mutate("autonomous");
      return;
    }
    if (data.sync.mode === "autonomous") {
      setLeaveTarget(selectedMode);
      return;
    }
    modeMutation.mutate(selectedMode);
  };
  const hasChanges =
    selectedMode !== data.sync.mode ||
    (selectedMode !== "manual" && (interval !== data.sync.interval_hours || adaptive !== data.sync.adaptive));
  const showAutonomous =
    isPrimaryOwner &&
    (data.sync.available_modes.includes("autonomous") ||
      data.sync.mode === "autonomous" ||
      data.sync.autonomous.configured ||
      data.sync.autonomous.needs_reenrollment);

  return (
    <section className="settings-panel sync-settings">
      <header>
        <span>
          <RefreshCw size={20} />
        </span>
        <div>
          <h2>Synchronisation IMT</h2>
          <p>Mode, fréquence et accès privés à PASS/HUB.</p>
        </div>
      </header>
      {!isPrimaryOwner && (
        <div className="privacy-note">
          <LockKeyhole size={17} />
          <span>
            <strong>Reconnexion requise.</strong> Une connexion IMT ou passkey est nécessaire pour modifier ces
            réglages.
          </span>
        </div>
      )}
      {passAccess.state === "circuit_open" && (
        <div className="pass-outage-banner">
          <Activity size={18} />
          <div>
            <strong>PASS est temporairement indisponible</strong>
            <span>Tes données déjà importées restent accessibles.</span>
          </div>
        </div>
      )}
      <div className="sync-state">
        <span className={`large-status-icon ${dashboardAccount?.last_sync_status ?? "never"}`}>
          {dashboardAccount?.last_sync_status === "error" ? <TriangleAlert size={25} /> : <CheckCircle2 size={25} />}
        </span>
        <div>
          <strong>
            {dashboardAccount?.last_sync_status === "error" ? "Synchronisation en erreur" : "Connexion opérationnelle"}
          </strong>
          <span>Dernière synchronisation : {formatDate(dashboardAccount?.last_sync_at)}</span>
        </div>
      </div>
      {dashboardAccount?.last_sync_error && <div className="inline-warning">{dashboardAccount.last_sync_error}</div>}
      <div className={`manual-sync-state ${dashboardAccount?.manual_sync?.state ?? "checking"}`} role="status">
        <Clock3 size={17} />
        <div>
          <strong>Synchronisation manuelle</strong>
          <span>{manualSyncMessage(dashboardAccount?.manual_sync, manualSyncRemaining)}</span>
        </div>
      </div>
      <div className="pass-budget">
        <div>
          <span>Dernière heure</span>
          <strong>
            {passAccess.quota.hour.remaining} / {passAccess.quota.hour.limit}
          </strong>
        </div>
        <div>
          <span>Dernières 24 h</span>
          <strong>
            {passAccess.quota.day.remaining} / {passAccess.quota.day.limit}
          </strong>
        </div>
      </div>

      {isPrimaryOwner && (
        <div className="sync-preferences">
          <SyncModeSelector
            value={selectedMode}
            availableModes={data.sync.available_modes}
            includeAutonomous={showAutonomous}
            disabled={modeMutation.isPending}
            name={modeGroupName}
            onChange={setSelectedMode}
          />
          {selectedMode !== "manual" && (
            <SyncScheduleOptions
              interval={interval}
              adaptive={adaptive}
              allowedIntervals={data.sync.allowed_intervals}
              disabled={modeMutation.isPending}
              onIntervalChange={setInterval}
              onAdaptiveChange={setAdaptive}
            />
          )}
          <div className="sync-preferences-actions">
            <button
              className="primary-button"
              type="button"
              disabled={modeMutation.isPending || !hasChanges}
              onClick={saveMode}
            >
              {modeMutation.isPending ? <span className="spinner" /> : <CheckCircle2 size={17} />}
              Enregistrer ce mode
            </button>
            <span aria-live="polite">
              {modeMutation.isPending
                ? "Enregistrement…"
                : `Mode actif : ${data.sync.mode === "manual" ? "À la demande" : data.sync.mode === "session_only" ? "Session privée" : "Automatique autonome"}`}
            </span>
          </div>
        </div>
      )}

      {data.sync.enabled && (
        <div className="adaptive-status">
          <div>
            <span>Cadence actuelle</span>
            <strong>{data.sync.current_interval_hours} h</strong>
          </div>
          <div>
            <span>Prochaine exécution</span>
            <strong>{formatDate(data.sync.next_eligible_at)}</strong>
          </div>
        </div>
      )}
      {data.sync.paused_reason && (
        <div className="inline-warning">
          <TriangleAlert size={16} />
          {data.sync.paused_reason === "reauth_required"
            ? "Reconnexion nécessaire pour reprendre la session privée."
            : data.sync.paused_reason === "credential_invalid"
              ? "Le mot de passe autonome doit être renouvelé."
              : "La synchronisation autonome est temporairement en pause."}
        </div>
      )}

      {isPrimaryOwner && (
        <AutonomousSyncStatus
          status={data.sync.autonomous}
          mode={data.sync.mode}
          disabled={modeMutation.isPending}
          canActivate={data.sync.autonomous.available}
          canEnroll={data.sync.autonomous.enrollment_available}
          onActivate={() => modeMutation.mutate("autonomous")}
          onUpdate={() => {
            setUpdatingCredential(true);
            setEnrollmentOpen(true);
          }}
          onDelete={() => setDeleteCredentialOpen(true)}
        />
      )}

      <div className={`pass-session-summary state-${serviceSession.state}`}>
        <span>
          <LockKeyhole size={18} />
        </span>
        <div>
          <strong>
            {serviceSession.state === "active"
              ? "Session PASS/HUB active"
              : serviceSession.state === "owner_managed"
                ? "Accès autonome disponible"
                : "Reconnexion IMT requise"}
          </strong>
          <small>
            {serviceSession.state === "active"
              ? `Dernière utilisation ${formatDate(serviceSession.last_used_at)} · limite locale ${formatDate(serviceSession.expires_at)}`
              : data.sync.autonomous.configured
                ? "Le mot de passe autonome est géré séparément de cette reconnexion ponctuelle."
                : "Aucun mot de passe IMT n'est conservé."}
          </small>
        </div>
        {isPrimaryOwner && (
          <button className="secondary-button" type="button" onClick={() => setPassReconnectOpen(true)}>
            <RefreshCw size={16} /> {serviceSession.reauth_required ? "Reconnecter" : "Renouveler"}
          </button>
        )}
      </div>
      {serviceSession.hub_state === "degraded" && (
        <p className="settings-hint">
          <Info size={14} /> PASS fonctionne, mais HUB COMPETENCES devra peut-être être rouvert.
        </p>
      )}
      {isPrimaryOwner && (
        <div className="pass-access-danger">
          <ShieldAlert size={18} />
          <div>
            <strong>Supprimer tout accès PASS/HUB</strong>
            <p>Révoque les sessions privées et le mot de passe autonome sans supprimer tes résultats.</p>
          </div>
          <button className="danger-button" type="button" onClick={() => setPurgeOpen(true)}>
            Supprimer les accès
          </button>
        </div>
      )}

      <AutonomousSyncEnrollmentModal
        open={enrollmentOpen}
        interval={interval}
        adaptive={adaptive}
        available={data.sync.autonomous.enrollment_available}
        updateExisting={updatingCredential}
        onClose={() => setEnrollmentOpen(false)}
        onSettings={applySettings}
        onActivated={() => showToast("Synchronisation autonome activée")}
      />
      <LeaveAutonomousModal
        open={leaveTarget !== null}
        targetMode={leaveTarget ?? "manual"}
        pending={modeMutation.isPending}
        onClose={() => setLeaveTarget(null)}
        onConfirm={() => leaveTarget && modeMutation.mutate(leaveTarget)}
      />
      <DeleteCredentialModal
        open={deleteCredentialOpen}
        pending={deleteCredential.isPending}
        onClose={() => setDeleteCredentialOpen(false)}
        onConfirm={() => deleteCredential.mutate()}
      />
      <PurgePassAccessModal
        open={purgeOpen}
        pending={purgePassAccess.isPending}
        onClose={() => setPurgeOpen(false)}
        onConfirm={() => purgePassAccess.mutate()}
      />
      <PassReconnectModal
        open={passReconnectOpen}
        identifier={data.account.imt_username}
        autonomousConfigured={data.sync.autonomous.configured}
        onClose={() => setPassReconnectOpen(false)}
        onRenewed={() => void queryClient.invalidateQueries({ queryKey: queryKeys.account })}
      />
    </section>
  );
}
