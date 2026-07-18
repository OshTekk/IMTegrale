import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Check, Clock3, FlaskConical, Hand, LockKeyhole, ShieldCheck, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { queryKeys, useSettings } from "../lib/queries";
import type { SettingsView } from "../types";
import { Modal } from "./Modal";
import { useToast } from "./Toast";

type Interval = 2 | 4 | 6 | 8 | 12 | 24;

export function SyncSetupModal({ open, onComplete }: { open: boolean; onComplete: () => void }) {
  const settings = useSettings();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [mode, setMode] = useState<"manual" | "automatic">("manual");
  const [interval, setInterval] = useState<Interval>(2);
  const [adaptive, setAdaptive] = useState(true);
  useEffect(() => {
    if (!open || !settings.data) return;
    setMode(settings.data.sync.enabled ? "automatic" : "manual");
    setInterval(settings.data.sync.interval_hours);
    setAdaptive(settings.data.sync.adaptive);
  }, [open, settings.data]);
  const save = useMutation({
    mutationFn: () => api<SettingsView>("/api/v1/settings/sync-setup", {
      method: "PUT",
      body: JSON.stringify({
        enabled: mode === "automatic",
        interval_hours: interval,
        adaptive,
      }),
    }),
    onSuccess: (next) => {
      const accountId = queryClient.getQueryData<{ account?: { id: string } }>(queryKeys.session)?.account?.id;
      if (accountId) queryClient.setQueryData(queryKeys.settings(accountId), next);
      showToast(mode === "automatic" ? "Synchronisation automatique configurée" : "Synchronisation manuelle choisie");
      onComplete();
    },
    onError: (error) => showToast(error.message, "error"),
  });

  return (
    <Modal
      open={open}
      title="Choisir les synchronisations"
      description="Tu gardes le contrôle sur chaque accès planifié à PASS et HUB COMPETENCES."
      onClose={() => undefined}
      size="large"
      className="sync-setup-modal"
      dismissible={false}
    >
      <div className="sync-setup-content">
        <div className="sync-setup-choice" role="radiogroup" aria-label="Mode de synchronisation">
          <button type="button" role="radio" aria-checked={mode === "manual"} className={mode === "manual" ? "selected" : ""} onClick={() => setMode("manual")}>
            <span><Hand size={21} /></span><div><strong>À la demande</strong><small>Choix par défaut</small><p>Aucun appel planifié. Tu lances toi-même les actualisations.</p></div>{mode === "manual" && <Check size={17} />}
          </button>
          <button type="button" role="radio" aria-checked={mode === "automatic"} className={mode === "automatic" ? "selected" : ""} onClick={() => setMode("automatic")}>
            <span><CalendarClock size={21} /></span><div><strong>Automatique</strong><small><FlaskConical size={13} /> Bêta</small><p>IMTégrale vérifie les nouvelles notes pendant les heures ouvrées.</p></div>{mode === "automatic" && <Check size={17} />}
          </button>
        </div>
        {mode === "automatic" && (
          <section className="sync-setup-options">
            <label>Fréquence de base<select value={interval} onChange={(event) => setInterval(Number(event.target.value) as Interval)}>{([2, 4, 6, 8, 12, 24] as Interval[]).map((hours) => <option key={hours} value={hours}>{hours === 24 ? "Une fois par jour" : `Toutes les ${hours} heures`}</option>)}</select></label>
            <label className="adaptive-control"><span><Zap size={17} /><span><strong>Cadence adaptative</strong><small>Ralentit automatiquement après plusieurs passages sans changement.</small></span></span><input type="checkbox" checked={adaptive} onChange={(event) => setAdaptive(event.target.checked)} /></label>
            <div className="sync-setup-window"><Clock3 size={16} /><span>Du lundi au vendredi, entre 8 h et 20 h. Deux heures est la fréquence maximale.</span></div>
          </section>
        )}
        <div className="sync-setup-privacy">
          <ShieldCheck size={19} />
          <div><strong>Aucun mot de passe IMT conservé</strong><p>Après la connexion, le mot de passe est détruit. Seule la session technique PASS/HUB est chiffrée, révocable et limitée à 30 jours.</p></div>
        </div>
        <div className="sync-setup-beta-note"><LockKeyhole size={16} /><span>Les accès planifiés peuvent aider la session distante à rester ouverte, sans garantie de la part de PASS. Si elle expire, l'automatisation se met en pause et te demande de te reconnecter.</span></div>
      </div>
      <footer className="modal-actions"><button className="primary-button" type="button" onClick={() => save.mutate()} disabled={save.isPending}>{save.isPending ? <span className="spinner" /> : <Check size={17} />} Enregistrer ce choix</button></footer>
    </Modal>
  );
}
