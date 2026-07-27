import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, LockKeyhole, ShieldCheck, Trash2 } from "lucide-react";
import { useEffect, useId, useState } from "react";
import {
  settingsCompleteSyncSetup,
  settingsDeleteSyncCredential,
  settingsUpdateSyncMode,
} from "../generated/api/sdk.gen";
import type { SettingsResponse, SyncMode } from "../generated/api/types.gen";
import { apiData, throwOnApiError } from "../lib/generatedApi";
import { queryKeys, useSettings } from "../lib/queries";
import { Modal } from "./Modal";
import { AutonomousSyncEnrollmentModal } from "./sync/AutonomousSyncEnrollmentModal";
import { SyncModeSelector } from "./sync/SyncModeSelector";
import { SyncScheduleOptions, type SyncInterval } from "./sync/SyncScheduleOptions";
import { useToast } from "./Toast";

function cacheSettings(queryClient: ReturnType<typeof useQueryClient>, settings: SettingsResponse): void {
  const accountId = queryClient.getQueryData<{ account?: { id: string } }>(queryKeys.session)?.account?.id;
  if (accountId) queryClient.setQueryData(queryKeys.settings(accountId), settings);
}

export function SyncSetupModal({ open, onComplete }: { open: boolean; onComplete: () => void }) {
  const settings = useSettings();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const modeGroupName = useId();
  const [mode, setMode] = useState<SyncMode>("manual");
  const [interval, setInterval] = useState<SyncInterval>(2);
  const [adaptive, setAdaptive] = useState(true);
  const [enrollmentOpen, setEnrollmentOpen] = useState(false);

  useEffect(() => {
    if (!open || !settings.data) return;
    setMode(settings.data.sync.autonomous.activation_pending ? "autonomous" : "manual");
    setInterval(settings.data.sync.interval_hours);
    setAdaptive(settings.data.sync.adaptive);
  }, [open, settings.data]);

  const complete = useMutation({
    mutationFn: async () => {
      if (mode === "autonomous") {
        if (!settings.data?.sync.autonomous.configured) {
          throw new Error("SYNC_CREDENTIAL_REQUIRED");
        }
        return apiData(
          settingsUpdateSyncMode({
            body: { mode, interval_hours: interval, adaptive },
            throwOnError: throwOnApiError,
          }),
        );
      }
      return apiData(
        settingsCompleteSyncSetup({
          body: {
            enabled: mode === "session_only",
            interval_hours: interval,
            adaptive,
          },
          throwOnError: throwOnApiError,
        }),
      );
    },
    onSuccess: (next) => {
      cacheSettings(queryClient, next);
      showToast(
        mode === "manual"
          ? "Synchronisation à la demande choisie"
          : mode === "session_only"
            ? "Synchronisation avec session privée configurée"
            : "Synchronisation autonome activée",
      );
      onComplete();
    },
    onError: (error) => showToast(error.message, "error"),
  });

  const removePendingCredential = useMutation({
    mutationFn: () => apiData(settingsDeleteSyncCredential({ throwOnError: throwOnApiError })),
    onSuccess: (next) => {
      cacheSettings(queryClient, next);
      setMode("manual");
      showToast("Mot de passe conservé supprimé");
    },
    onError: (error) => showToast(error.message, "error"),
  });

  if (!settings.data) return null;
  const data = settings.data;
  const showAutonomous =
    data.sync.available_modes.includes("autonomous") ||
    data.sync.autonomous.configured ||
    data.sync.autonomous.needs_reenrollment;

  const save = () => {
    if (mode === "autonomous" && (!data.sync.autonomous.configured || data.sync.autonomous.needs_reenrollment)) {
      setEnrollmentOpen(true);
      return;
    }
    complete.mutate();
  };

  return (
    <>
      <Modal
        open={open && !enrollmentOpen}
        title="Choisir les synchronisations"
        description="Tu gardes le contrôle sur chaque accès planifié à PASS et HUB COMPETENCES."
        onClose={() => undefined}
        size="large"
        className="sync-setup-modal"
        dismissible={false}
      >
        <div className="sync-setup-content">
          <SyncModeSelector
            value={mode}
            availableModes={data.sync.available_modes}
            includeAutonomous={showAutonomous}
            disabled={complete.isPending}
            name={modeGroupName}
            onChange={setMode}
          />
          {mode !== "manual" && (
            <SyncScheduleOptions
              interval={interval}
              adaptive={adaptive}
              allowedIntervals={data.sync.allowed_intervals}
              disabled={complete.isPending}
              onIntervalChange={setInterval}
              onAdaptiveChange={setAdaptive}
            />
          )}
          <div className="sync-setup-privacy">
            <ShieldCheck size={19} />
            <div>
              <strong>
                {mode === "autonomous"
                  ? "Mot de passe IMT conservé avec consentement explicite"
                  : "Aucun mot de passe IMT conservé"}
              </strong>
              <p>
                {mode === "autonomous"
                  ? "Le mot de passe est protégé sous une enveloppe chiffrée, avec un risque serveur supérieur clairement assumé."
                  : "Après la connexion, le mot de passe est abandonné. Seule la session technique PASS/HUB peut être conservée chiffrée."}
              </p>
            </div>
          </div>
          {mode === "session_only" && (
            <div className="sync-setup-beta-note">
              <LockKeyhole size={16} />
              <span>
                PASS peut fermer sa session plus tôt que prévu. La synchronisation se met alors en pause et demande une
                nouvelle authentification.
              </span>
            </div>
          )}
          {data.sync.autonomous.activation_pending && (
            <div className="autonomous-enrollment-error is-pending" role="status">
              <LockKeyhole size={17} />
              <span>Le mot de passe est protégé, mais l'activation n'est pas terminée.</span>
            </div>
          )}
        </div>
        <footer className="modal-actions sync-setup-actions">
          {data.sync.autonomous.activation_pending && (
            <button
              className="text-button danger-text-button"
              type="button"
              onClick={() => removePendingCredential.mutate()}
              disabled={removePendingCredential.isPending || complete.isPending}
            >
              <Trash2 size={16} /> Abandonner et supprimer
            </button>
          )}
          <button
            className="primary-button"
            type="button"
            onClick={save}
            disabled={
              complete.isPending || removePendingCredential.isPending || !data.sync.available_modes.includes(mode)
            }
          >
            {complete.isPending ? <span className="spinner" /> : <Check size={17} />}
            {mode === "autonomous" && data.sync.autonomous.activation_pending
              ? "Terminer l'activation"
              : "Enregistrer ce choix"}
          </button>
        </footer>
      </Modal>
      <AutonomousSyncEnrollmentModal
        open={open && enrollmentOpen}
        interval={interval}
        adaptive={adaptive}
        available={data.sync.autonomous.enrollment_available}
        updateExisting={data.sync.autonomous.configured}
        onClose={() => setEnrollmentOpen(false)}
        onSettings={(next) => cacheSettings(queryClient, next)}
        onActivated={onComplete}
      />
    </>
  );
}
