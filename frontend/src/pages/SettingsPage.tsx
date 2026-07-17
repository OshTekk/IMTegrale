import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Bell, Bot, CalendarClock, CheckCircle2, Clock3, ExternalLink, Fingerprint, Info, KeyRound, LockKeyhole, MessageCircle, RefreshCw, Send, ShieldCheck, Smartphone, Trash2, TriangleAlert, UserRound, Zap } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { Modal } from "../components/Modal";
import { useToast } from "../components/Toast";
import { api } from "../lib/api";
import { formatDate } from "../lib/format";
import { registerPasskey } from "../lib/passkeys";
import { queryKeys, useDashboard, useSettings } from "../lib/queries";
import { manualSyncMessage, useServerCountdown } from "../lib/sync";
import type { PasskeyItem, Role, SettingsView } from "../types";

type Interval = 2 | 4 | 6 | 8 | 12 | 24;

export function SettingsPage({ role }: { role: Role }) {
  const settings = useSettings();
  const dashboard = useDashboard();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [displayName, setDisplayName] = useState("");
  const [timezone, setTimezone] = useState("Europe/Paris");
  const [botToken, setBotToken] = useState("");
  const [chatId, setChatId] = useState("");
  const [autoSyncInterval, setAutoSyncInterval] = useState<Interval>(2);
  const [adaptive, setAdaptive] = useState(true);
  const [autoSyncConsentOpen, setAutoSyncConsentOpen] = useState(false);
  const [telegramGuideOpen, setTelegramGuideOpen] = useState(false);
  const [passkeyName, setPasskeyName] = useState("Appareil principal");
  const manualSync = dashboard.data?.account.manual_sync;
  const manualSyncRemaining = useServerCountdown(manualSync);
  const passkeys = useQuery({
    queryKey: ["account", "passkeys"],
    queryFn: () => api<PasskeyItem[]>("/api/v1/auth/passkeys"),
    enabled: role === "owner",
  });

  useEffect(() => {
    if (!settings.data) return;
    setDisplayName(settings.data.account.display_name);
    setTimezone(settings.data.account.timezone);
    setAutoSyncInterval(settings.data.sync.interval_hours);
    setAdaptive(settings.data.sync.adaptive);
  }, [settings.data]);

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.account });
    void queryClient.invalidateQueries({ queryKey: queryKeys.session });
  };
  const accountMutation = useMutation({
    mutationFn: () => api<SettingsView>("/api/v1/settings/account", { method: "PATCH", body: JSON.stringify({ display_name: displayName, timezone }) }),
    onSuccess: () => { refresh(); showToast("Profil enregistré"); },
    onError: (error) => showToast(error.message, "error"),
  });
  const telegramMutation = useMutation({
    mutationFn: () => api<SettingsView>("/api/v1/settings/telegram", { method: "PUT", body: JSON.stringify({ bot_token: botToken, chat_id: chatId, enabled: true }) }),
    onSuccess: () => { setBotToken(""); setChatId(""); refresh(); showToast("Telegram configuré"); },
    onError: (error) => showToast(error.message, "error"),
  });
  const toggleTelegram = useMutation({
    mutationFn: (enabled: boolean) => api<SettingsView>("/api/v1/settings/telegram", { method: "PATCH", body: JSON.stringify({ enabled }) }),
    onSuccess: () => { refresh(); showToast("Préférence Telegram mise à jour"); },
    onError: (error) => showToast(error.message, "error"),
  });
  const testTelegram = useMutation({
    mutationFn: () => api("/api/v1/settings/telegram/test", { method: "POST", body: "{}" }),
    onSuccess: () => { refresh(); showToast("Message de test reçu par Telegram"); },
    onError: (error) => { refresh(); showToast(error.message, "error"); },
  });
  const autoSyncMutation = useMutation({
    mutationFn: ({ enabled, interval, useAdaptive }: { enabled: boolean; interval: Interval; useAdaptive: boolean }) => api<SettingsView>("/api/v1/settings/auto-sync", { method: "PATCH", body: JSON.stringify({ enabled, interval_hours: interval, adaptive: useAdaptive }) }),
    onSuccess: (_next, variables) => { setAutoSyncConsentOpen(false); refresh(); showToast(variables.enabled ? "Actualisation automatique autorisée" : "Actualisation automatique désactivée"); },
    onError: (error) => showToast(error.message, "error"),
  });
  const addPasskey = useMutation({
    mutationFn: () => registerPasskey(passkeyName),
    onSuccess: () => { void passkeys.refetch(); refresh(); showToast("Passkey ajoutée"); },
    onError: (error) => showToast(error.message, "error"),
  });
  const removePasskey = useMutation({
    mutationFn: (id: string) => api(`/api/v1/auth/passkeys/${id}`, { method: "DELETE", body: "{}" }),
    onSuccess: () => { void passkeys.refetch(); refresh(); showToast("Passkey supprimée"); },
    onError: (error) => showToast(error.message, "error"),
  });

  if (settings.isPending) return <div className="settings-skeleton skeleton" />;
  if (settings.isError || !settings.data) return <div className="error-panel"><TriangleAlert size={22} />{settings.error?.message}</div>;
  const data = settings.data;
  if (role !== "owner") return <div className="settings-grid"><section className="settings-panel access-only"><span className="large-status-icon"><ShieldCheck size={26} /></span><h2>Accès {role === "viewer" ? "en lecture seule" : "avec édition"}</h2><p>Seul le propriétaire peut modifier le compte, Telegram et les accès.</p><div className="info-line"><KeyRound size={17} /><span>Méthode</span><strong>Token d'accès</strong></div></section></div>;

  const saveProfile = (event: FormEvent) => { event.preventDefault(); accountMutation.mutate(); };
  const saveTelegram = (event: FormEvent) => { event.preventDefault(); telegramMutation.mutate(); };
  const updateAuto = (enabled: boolean, interval = autoSyncInterval, useAdaptive = adaptive) => autoSyncMutation.mutate({ enabled, interval, useAdaptive });
  const segment = data.account.promotion_year ? `${data.account.program} ${data.account.promotion_year}` : "Non disponible";
  return (
    <div className="settings-grid">
      {data.sync.pass_access.state === "circuit_open" && <div className="pass-outage-banner"><Activity size={18} /><div><strong>PASS est temporairement indisponible</strong><span>Tes données déjà importées restent accessibles. Prochaine vérification : {formatDate(data.sync.pass_access.circuit.next_probe_at)}.</span></div></div>}

      <section className="settings-panel profile-settings">
        <header><span><UserRound size={20} /></span><div><h2>Compte</h2><p>Identité IMTégrale et profil officiel PASS.</p></div></header>
        <form onSubmit={saveProfile} className="settings-form"><label>Nom d'usage IMTégrale <small>Privé</small><input value={displayName} onChange={(event) => setDisplayName(event.target.value)} required /></label><label>Identifiant IMT<input value={data.account.imt_username ?? ""} disabled /></label><label>Fuseau horaire<select value={timezone} onChange={(event) => setTimezone(event.target.value)}><option value="Europe/Paris">Europe/Paris</option><option value="Europe/Zurich">Europe/Zurich</option></select></label><button className="primary-button" type="submit" disabled={accountMutation.isPending}>Enregistrer</button></form>
        <div className="official-profile-grid"><div><span>Identité officielle</span><strong>{data.account.official_name ?? "Non disponible"}</strong></div><div><span>Campus</span><strong>{data.account.campus === "unknown" ? "Non disponible" : data.account.campus[0]?.toUpperCase() + data.account.campus.slice(1)}</strong></div><div><span>Cursus · promotion</span><strong>{segment}</strong></div></div>
        <p className="settings-hint"><Info size={14} /> {data.account.official_name ? <>Identité figée et profil vérifié sur PASS le {formatDate(data.account.official_identity_at ?? data.account.profile_refreshed_at, false)}. Contacte l'administrateur pour demander une relecture.</> : <>L'identité officielle sera récupérée lors de la prochaine connexion autorisée à PASS. Contacte l'administrateur si elle reste indisponible.</>}</p>
      </section>

      <section className="settings-panel sync-settings">
        <header><span><RefreshCw size={20} /></span><div><h2>Synchronisation IMT</h2><p>Fraîcheur, budget et consentement.</p></div></header>
        <div className="sync-state"><span className={`large-status-icon ${dashboard.data?.account.last_sync_status ?? "never"}`}>{dashboard.data?.account.last_sync_status === "error" ? <TriangleAlert size={25} /> : <CheckCircle2 size={25} />}</span><div><strong>{dashboard.data?.account.last_sync_status === "error" ? "Synchronisation en erreur" : "Connexion opérationnelle"}</strong><span>Dernière synchronisation : {formatDate(dashboard.data?.account.last_sync_at)}</span></div></div>
        {dashboard.data?.account.last_sync_error && <div className="inline-warning">{dashboard.data.account.last_sync_error}</div>}
        <div className={`manual-sync-state ${manualSync?.state ?? "checking"}`} role="status" aria-live="polite"><Clock3 size={17} /><div><strong>Synchronisation manuelle</strong><span>{manualSyncMessage(manualSync, manualSyncRemaining)}</span></div></div>
        <div className="pass-budget"><div><span>Dernière heure</span><strong>{data.sync.pass_access.quota.hour.remaining} / {data.sync.pass_access.quota.hour.limit}</strong></div><div><span>Dernières 24 h</span><strong>{data.sync.pass_access.quota.day.remaining} / {data.sync.pass_access.quota.day.limit}</strong></div></div>
        <div className={`auto-sync-box ${data.sync.enabled ? "is-enabled" : ""}`}>
          <div className="auto-sync-heading"><span><CalendarClock size={18} /></span><div><strong>Actualisation automatique</strong><small>{data.sync.enabled ? `Base choisie : ${data.sync.interval_hours} h` : "Désactivée par défaut"}</small></div><label className="switch"><input type="checkbox" aria-label="Actualisation automatique" checked={data.sync.enabled} disabled={autoSyncMutation.isPending} onChange={(event) => event.target.checked ? setAutoSyncConsentOpen(true) : updateAuto(false)} /><i /></label></div>
          <label className="auto-sync-frequency">Fréquence de base<select value={autoSyncInterval} onChange={(event) => { const interval = Number(event.target.value) as Interval; setAutoSyncInterval(interval); if (data.sync.enabled) updateAuto(true, interval); }} disabled={autoSyncMutation.isPending}>{data.sync.allowed_intervals.map((hours) => <option key={hours} value={hours}>{hours === 24 ? "Une fois par jour" : `Toutes les ${hours} heures`}</option>)}</select></label>
          <label className="adaptive-control"><span><Zap size={16} /><span><strong>Cadence adaptative</strong><small>Ralentit après trois passages sans nouvelle note.</small></span></span><input type="checkbox" checked={adaptive} onChange={(event) => { setAdaptive(event.target.checked); if (data.sync.enabled) updateAuto(true, autoSyncInterval, event.target.checked); }} /></label>
          {data.sync.enabled && <div className="adaptive-status"><div><span>Cadence actuelle</span><strong>{data.sync.current_interval_hours} h</strong></div><div><span>Prochaine exécution</span><strong>{formatDate(data.sync.next_eligible_at)}</strong></div></div>}
          <div className="auto-sync-window"><Clock3 size={15} /><span>Du lundi au vendredi, de {data.sync.business_hours.start.replace(":", " h ")} à {data.sync.business_hours.end.replace(":", " h ")}.</span></div>
        </div>
        <div className="info-line"><LockKeyhole size={17} /><span>Identifiants chiffrés</span><strong>Mis à jour le {formatDate(data.account.credentials_updated_at, false)}</strong></div>
      </section>

      <section className="settings-panel passkey-settings">
        <header><span><Fingerprint size={20} /></span><div><h2>Passkeys</h2><p>Connexions sans accès à PASS.</p></div></header>
        <div className="passkey-list">{passkeys.data?.map((item) => <div className="passkey-row" key={item.id}><span><Fingerprint size={18} /></span><div><strong>{item.name}</strong><small>{item.backed_up ? "Synchronisée" : "Cet appareil"} · ajoutée le {formatDate(item.created_at, false)}</small></div><button className="icon-button danger-icon" type="button" onClick={() => removePasskey.mutate(item.id)} aria-label={`Supprimer ${item.name}`} title="Supprimer"><Trash2 size={16} /></button></div>)}{passkeys.data?.length === 0 && <p className="settings-hint">Aucune passkey enregistrée.</p>}</div>
        <div className="passkey-add"><input value={passkeyName} onChange={(event) => setPasskeyName(event.target.value)} maxLength={80} aria-label="Nom de la passkey" /><button className="primary-button" type="button" disabled={addPasskey.isPending || passkeyName.trim().length < 2} onClick={() => addPasskey.mutate()}><Fingerprint size={17} /> Ajouter</button></div>
      </section>

      <section className="settings-panel telegram-settings">
        <header><span><Send size={20} /></span><div><h2>Notifications Telegram</h2><p>Alertes du bot Python conservées.</p></div><button className="icon-button telegram-help" type="button" onClick={() => setTelegramGuideOpen(true)} aria-label="Guide de configuration Telegram" title="Guide Telegram"><Info size={17} /></button></header>
        {data.telegram.configured && <div className="configured-row"><div><span className={`large-status-icon ${data.telegram.last_test_status === "failed" ? "error" : "success"}`}><Smartphone size={22} /></span><span><strong>Telegram configuré</strong><small>{data.telegram.last_test_at ? `${data.telegram.last_test_status === "success" ? "Test réussi" : data.telegram.last_test_status === "failed" ? "Dernier test échoué" : "Test en cours"} · ${formatDate(data.telegram.last_test_at)}` : data.telegram.enabled ? "Notifications actives · test recommandé" : "Notifications suspendues"}</small></span></div><div className="telegram-config-actions"><button className="secondary-button" type="button" onClick={() => testTelegram.mutate()} disabled={testTelegram.isPending}>{testTelegram.isPending ? <span className="spinner" /> : <Send size={16} />} {testTelegram.isPending ? "Envoi" : "Tester"}</button><label className="switch"><input type="checkbox" aria-label="Notifications Telegram" checked={data.telegram.enabled} onChange={(event) => toggleTelegram.mutate(event.target.checked)} /><i /></label></div></div>}
        <form className="settings-form" onSubmit={saveTelegram} autoComplete="off"><label>Token du bot<input type="password" name="botnote-telegram-token" value={botToken} onChange={(event) => setBotToken(event.target.value)} placeholder={data.telegram.configured ? "Remplacer le token actuel" : "123456:ABC…"} autoComplete="new-password" data-1p-ignore="true" data-lpignore="true" spellCheck={false} required /></label><label>Chat ID<input name="botnote-telegram-chat-id" value={chatId} onChange={(event) => setChatId(event.target.value)} placeholder={data.telegram.configured ? "Remplacer le Chat ID actuel" : "123456789"} inputMode="numeric" autoComplete="off" data-1p-ignore="true" data-lpignore="true" required /></label><div className="form-actions-row"><button className="primary-button" type="submit" disabled={telegramMutation.isPending}><Bell size={17} /> {data.telegram.configured ? "Mettre à jour" : "Activer"}</button></div></form>
      </section>

      <Modal open={autoSyncConsentOpen} title="Autoriser l'actualisation automatique" description="Cette autorisation est facultative et révocable à tout moment." onClose={() => setAutoSyncConsentOpen(false)} size="small"><div className="auto-sync-consent"><span><CalendarClock size={21} /></span><div><strong>Accès planifié à PASS</strong><p>Fréquence de base : {autoSyncInterval} heures, du lundi au vendredi entre 8 h et 20 h. La cadence adaptative est {adaptive ? "activée" : "désactivée"}.</p></div></div><div className="privacy-note"><ShieldCheck size={16} /><span>Aucun accès automatique n'a lieu sans ce consentement.</span></div><footer className="modal-actions"><button className="secondary-button" type="button" onClick={() => setAutoSyncConsentOpen(false)}>Annuler</button><button className="primary-button" type="button" onClick={() => updateAuto(true)} disabled={autoSyncMutation.isPending}>{autoSyncMutation.isPending ? <span className="spinner" /> : <CheckCircle2 size={17} />} Autoriser</button></footer></Modal>
      <Modal open={telegramGuideOpen} title="Configurer les notifications Telegram" description="Quatre étapes pour relier un bot privé à IMTégrale." onClose={() => setTelegramGuideOpen(false)} size="large"><div className="telegram-guide"><section><span>1</span><div><strong>Créer un bot</strong><p>Ouvre le compte vérifié <b>@BotFather</b>, envoie <code>/newbot</code>, puis choisis son nom et son identifiant.</p><a href="https://t.me/BotFather" target="_blank" rel="noreferrer">Ouvrir BotFather <ExternalLink size={14} /></a></div><Bot size={19} /></section><section><span>2</span><div><strong>Conserver le token</strong><p>BotFather fournit un token après la création. Colle-le dans IMTégrale sans l'envoyer à quelqu'un d'autre.</p></div><KeyRound size={19} /></section><section><span>3</span><div><strong>Démarrer la conversation</strong><p>Ouvre ton nouveau bot, appuie sur <b>Démarrer</b> et envoie <code>/start</code>. Sans ce message, le bot ne peut pas t'écrire.</p></div><MessageCircle size={19} /></section><section><span>4</span><div><strong>Récupérer le Chat ID</strong><p>Appelle la méthode officielle <code>getUpdates</code> après ton message, puis relève le nombre dans <code>message.chat.id</code>.</p><a href="https://core.telegram.org/bots/api#getupdates" target="_blank" rel="noreferrer">Documentation officielle <ExternalLink size={14} /></a></div><Smartphone size={19} /></section></div><div className="telegram-security-note"><ShieldCheck size={17} /><span>Le token est chiffré sur le serveur et n'est jamais réaffiché. Une fois les deux valeurs enregistrées, utilise <strong>Tester</strong> et vérifie que le message arrive dans Telegram.</span></div><footer className="modal-actions"><button className="primary-button" type="button" onClick={() => setTelegramGuideOpen(false)}>Compris</button></footer></Modal>
    </div>
  );
}
