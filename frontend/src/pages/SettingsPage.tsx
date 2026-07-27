import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bell,
  Bot,
  ExternalLink,
  Fingerprint,
  Info,
  KeyRound,
  LockKeyhole,
  MessageCircle,
  Send,
  ShieldCheck,
  Smartphone,
  Trash2,
  TriangleAlert,
  UserRound,
} from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { Modal } from "../components/Modal";
import { SyncSettingsPanel } from "../components/sync/SyncSettingsPanel";
import { useToast } from "../components/Toast";
import {
  authDeletePasskey,
  authListPasskeys,
  settingsConfigureTelegram,
  settingsTestTelegram,
  settingsToggleTelegram,
  settingsUpdateAccount,
} from "../generated/api/sdk.gen";
import { formatDate } from "../lib/format";
import { apiData, throwOnApiError } from "../lib/generatedApi";
import { registerPasskey } from "../lib/passkeys";
import { queryKeys, useDashboard, useSettings } from "../lib/queries";
import type { Role } from "../types";

export function SettingsPage({ role, isPrimaryOwner }: { role: Role; isPrimaryOwner: boolean }) {
  const settings = useSettings();
  const dashboard = useDashboard();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [displayName, setDisplayName] = useState("");
  const [timezone, setTimezone] = useState("Europe/Paris");
  const [botToken, setBotToken] = useState("");
  const [chatId, setChatId] = useState("");
  const [telegramGuideOpen, setTelegramGuideOpen] = useState(false);
  const [passkeyName, setPasskeyName] = useState("Appareil principal");
  const passkeys = useQuery({
    queryKey: ["account", "passkeys"],
    queryFn: () => apiData(authListPasskeys({ throwOnError: throwOnApiError })),
    enabled: isPrimaryOwner,
  });

  useEffect(() => {
    if (!settings.data) return;
    setDisplayName(settings.data.account.display_name);
    setTimezone(settings.data.account.timezone);
  }, [settings.data]);

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.account });
    void queryClient.invalidateQueries({ queryKey: queryKeys.session });
  };
  const accountMutation = useMutation({
    mutationFn: () =>
      apiData(settingsUpdateAccount({ body: { display_name: displayName, timezone }, throwOnError: throwOnApiError })),
    onSuccess: () => {
      refresh();
      showToast("Profil enregistré");
    },
    onError: (error) => showToast(error.message, "error"),
  });
  const telegramMutation = useMutation({
    mutationFn: () =>
      apiData(
        settingsConfigureTelegram({
          body: { bot_token: botToken, chat_id: chatId, enabled: true },
          throwOnError: throwOnApiError,
        }),
      ),
    onSuccess: () => {
      setBotToken("");
      setChatId("");
      refresh();
      showToast("Telegram configuré");
    },
    onError: (error) => showToast(error.message, "error"),
  });
  const toggleTelegram = useMutation({
    mutationFn: (enabled: boolean) =>
      apiData(settingsToggleTelegram({ body: { enabled }, throwOnError: throwOnApiError })),
    onSuccess: () => {
      refresh();
      showToast("Préférence Telegram mise à jour");
    },
    onError: (error) => showToast(error.message, "error"),
  });
  const testTelegram = useMutation({
    mutationFn: () => apiData(settingsTestTelegram({ throwOnError: throwOnApiError })),
    onSuccess: () => {
      refresh();
      showToast("Message de test reçu par Telegram");
    },
    onError: (error) => {
      refresh();
      showToast(error.message, "error");
    },
  });
  const addPasskey = useMutation({
    mutationFn: () => registerPasskey(passkeyName),
    onSuccess: () => {
      void passkeys.refetch();
      refresh();
      showToast("Passkey ajoutée");
    },
    onError: (error) => showToast(error.message, "error"),
  });
  const removePasskey = useMutation({
    mutationFn: (id: string) => apiData(authDeletePasskey({ path: { passkey_id: id }, throwOnError: throwOnApiError })),
    onSuccess: () => {
      void passkeys.refetch();
      refresh();
      showToast("Passkey supprimée");
    },
    onError: (error) => showToast(error.message, "error"),
  });

  if (settings.isPending) return <div className="settings-skeleton skeleton" />;
  if (settings.isError || !settings.data)
    return (
      <div className="error-panel">
        <TriangleAlert size={22} />
        {settings.error?.message}
      </div>
    );
  const data = settings.data;
  if (role !== "owner")
    return (
      <div className="settings-grid">
        <section className="settings-panel access-only">
          <span className="large-status-icon">
            <ShieldCheck size={26} />
          </span>
          <h2>Accès en lecture seule</h2>
          <p>Seul le propriétaire peut modifier le compte, Telegram et les accès.</p>
          <div className="info-line">
            <KeyRound size={17} />
            <span>Méthode</span>
            <strong>Token d'accès</strong>
          </div>
        </section>
      </div>
    );
  const saveProfile = (event: FormEvent) => {
    event.preventDefault();
    accountMutation.mutate();
  };
  const saveTelegram = (event: FormEvent) => {
    event.preventDefault();
    telegramMutation.mutate();
  };
  const segment = data.account.promotion_year
    ? `${data.account.program} ${data.account.promotion_year}`
    : "Non disponible";
  return (
    <div className="settings-grid">
      <section className="settings-panel profile-settings">
        <header>
          <span>
            <UserRound size={20} />
          </span>
          <div>
            <h2>Compte</h2>
            <p>Identité IMTégrale et profil officiel PASS.</p>
          </div>
        </header>
        <form onSubmit={saveProfile} className="settings-form">
          <label>
            Nom d'usage IMTégrale <small>Privé</small>
            <input value={displayName} onChange={(event) => setDisplayName(event.target.value)} required />
          </label>
          <label>
            Identifiant CAS / IMT Atlantique
            <input value={data.account.imt_username ?? ""} disabled />
          </label>
          <label>
            Fuseau horaire
            <select value={timezone} onChange={(event) => setTimezone(event.target.value)}>
              <option value="Europe/Paris">Europe/Paris</option>
              <option value="Europe/Zurich">Europe/Zurich</option>
            </select>
          </label>
          <button className="primary-button" type="submit" disabled={accountMutation.isPending}>
            Enregistrer
          </button>
        </form>
        <div className="official-profile-grid">
          <div>
            <span>Identité officielle</span>
            <strong>{data.account.official_name ?? "Non disponible"}</strong>
          </div>
          <div>
            <span>Campus</span>
            <strong>
              {data.account.campus === "unknown"
                ? "Non disponible"
                : data.account.campus[0]?.toUpperCase() + data.account.campus.slice(1)}
            </strong>
          </div>
          <div>
            <span>Cursus · promotion</span>
            <strong>{segment}</strong>
          </div>
        </div>
        <p className="settings-hint">
          <Info size={14} />{" "}
          {data.account.official_name ? (
            <>
              Identité figée et profil vérifié sur PASS le{" "}
              {formatDate(data.account.official_identity_at ?? data.account.profile_refreshed_at, false)}. Contacte
              l'administrateur pour demander une relecture.
            </>
          ) : (
            <>
              L'identité officielle sera récupérée lors de la prochaine connexion autorisée à PASS. Contacte
              l'administrateur si elle reste indisponible.
            </>
          )}
        </p>
      </section>

      <SyncSettingsPanel data={data} dashboardAccount={dashboard.data?.account} isPrimaryOwner={isPrimaryOwner} />

      <section className="settings-panel passkey-settings">
        <header>
          <span>
            <Fingerprint size={20} />
          </span>
          <div>
            <h2>Passkeys</h2>
            <p>Connexions sans accès à PASS.</p>
          </div>
        </header>
        {isPrimaryOwner ? (
          <>
            <div className="passkey-list">
              {passkeys.data?.map((item) => (
                <div className="passkey-row" key={item.id}>
                  <span>
                    <Fingerprint size={18} />
                  </span>
                  <div>
                    <strong>{item.name}</strong>
                    <small>
                      {item.backed_up ? "Synchronisée" : "Cet appareil"} · ajoutée le{" "}
                      {formatDate(item.created_at, false)}
                    </small>
                  </div>
                  <button
                    className="icon-button danger-icon"
                    type="button"
                    onClick={() => removePasskey.mutate(item.id)}
                    aria-label={`Supprimer ${item.name}`}
                    title="Supprimer"
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              ))}
              {passkeys.data?.length === 0 && <p className="settings-hint">Aucune passkey enregistrée.</p>}
            </div>
            <div className="passkey-add">
              <input
                value={passkeyName}
                onChange={(event) => setPasskeyName(event.target.value)}
                maxLength={80}
                aria-label="Nom de la passkey"
              />
              <button
                className="primary-button"
                type="button"
                disabled={addPasskey.isPending || passkeyName.trim().length < 2}
                onClick={() => addPasskey.mutate()}
              >
                <Fingerprint size={17} /> Ajouter
              </button>
            </div>
          </>
        ) : (
          <div className="privacy-note">
            <LockKeyhole size={17} />
            <span>
              <strong>Reconnexion requise.</strong> Déconnecte-toi puis reconnecte-toi avec ton compte IMT ou une
              passkey déjà enregistrée pour gérer les passkeys.
            </span>
          </div>
        )}
      </section>

      <section className="settings-panel telegram-settings">
        <header>
          <span>
            <Send size={20} />
          </span>
          <div>
            <h2>Notifications Telegram</h2>
            <p>Alertes du bot Python conservées.</p>
          </div>
          <button
            className="icon-button telegram-help"
            type="button"
            onClick={() => setTelegramGuideOpen(true)}
            aria-label="Guide de configuration Telegram"
            title="Guide Telegram"
          >
            <Info size={17} />
          </button>
        </header>
        {data.telegram.configured && (
          <div className="configured-row">
            <div>
              <span
                className={`large-status-icon ${data.telegram.last_test_status === "failed" ? "error" : "success"}`}
              >
                <Smartphone size={22} />
              </span>
              <span>
                <strong>Telegram configuré</strong>
                <small>
                  {data.telegram.last_test_at
                    ? `${data.telegram.last_test_status === "success" ? "Test réussi" : data.telegram.last_test_status === "failed" ? "Dernier test échoué" : "Test en cours"} · ${formatDate(data.telegram.last_test_at)}`
                    : data.telegram.enabled
                      ? "Notifications actives · test recommandé"
                      : "Notifications suspendues"}
                </small>
              </span>
            </div>
            <div className="telegram-config-actions">
              <button
                className="secondary-button"
                type="button"
                onClick={() => testTelegram.mutate()}
                disabled={testTelegram.isPending}
              >
                {testTelegram.isPending ? <span className="spinner" /> : <Send size={16} />}{" "}
                {testTelegram.isPending ? "Envoi" : "Tester"}
              </button>
              <label className="switch">
                <input
                  type="checkbox"
                  aria-label="Notifications Telegram"
                  checked={data.telegram.enabled}
                  onChange={(event) => toggleTelegram.mutate(event.target.checked)}
                />
                <i />
              </label>
            </div>
          </div>
        )}
        {isPrimaryOwner ? (
          <form className="settings-form" onSubmit={saveTelegram} autoComplete="off">
            <label>
              Token du bot
              <input
                type="password"
                name="botnote-telegram-token"
                value={botToken}
                onChange={(event) => setBotToken(event.target.value)}
                placeholder={data.telegram.configured ? "Remplacer le token actuel" : "123456:ABC…"}
                autoComplete="new-password"
                data-1p-ignore="true"
                data-lpignore="true"
                spellCheck={false}
                required
              />
            </label>
            <label>
              Chat ID
              <input
                name="botnote-telegram-chat-id"
                value={chatId}
                onChange={(event) => setChatId(event.target.value)}
                placeholder={data.telegram.configured ? "Remplacer le Chat ID actuel" : "123456789"}
                inputMode="numeric"
                autoComplete="off"
                data-1p-ignore="true"
                data-lpignore="true"
                required
              />
            </label>
            <div className="form-actions-row">
              <button className="primary-button" type="submit" disabled={telegramMutation.isPending}>
                <Bell size={17} /> {data.telegram.configured ? "Mettre à jour" : "Activer"}
              </button>
            </div>
          </form>
        ) : (
          <div className="privacy-note">
            <LockKeyhole size={17} />
            <span>
              <strong>Reconnexion requise.</strong> Le remplacement du token Telegram ou du Chat ID exige une connexion
              IMT ou passkey.
            </span>
          </div>
        )}
      </section>

      <Modal
        open={telegramGuideOpen}
        title="Configurer les notifications Telegram"
        description="Quatre étapes pour relier un bot privé à IMTégrale."
        onClose={() => setTelegramGuideOpen(false)}
        size="large"
      >
        <div className="telegram-guide">
          <section>
            <span>1</span>
            <div>
              <strong>Créer un bot</strong>
              <p>
                Ouvre le compte vérifié <b>@BotFather</b>, envoie <code>/newbot</code>, puis choisis son nom et son
                identifiant.
              </p>
              <a href="https://t.me/BotFather" target="_blank" rel="noreferrer">
                Ouvrir BotFather <ExternalLink size={14} />
              </a>
            </div>
            <Bot size={19} />
          </section>
          <section>
            <span>2</span>
            <div>
              <strong>Conserver le token</strong>
              <p>
                BotFather fournit un token après la création. Colle-le dans IMTégrale sans l'envoyer à quelqu'un
                d'autre.
              </p>
            </div>
            <KeyRound size={19} />
          </section>
          <section>
            <span>3</span>
            <div>
              <strong>Démarrer la conversation</strong>
              <p>
                Ouvre ton nouveau bot, appuie sur <b>Démarrer</b> et envoie <code>/start</code>. Sans ce message, le bot
                ne peut pas t'écrire.
              </p>
            </div>
            <MessageCircle size={19} />
          </section>
          <section>
            <span>4</span>
            <div>
              <strong>Récupérer le Chat ID</strong>
              <p>
                Appelle la méthode officielle <code>getUpdates</code> après ton message, puis relève le nombre dans{" "}
                <code>message.chat.id</code>.
              </p>
              <a href="https://core.telegram.org/bots/api#getupdates" target="_blank" rel="noreferrer">
                Documentation officielle <ExternalLink size={14} />
              </a>
            </div>
            <Smartphone size={19} />
          </section>
        </div>
        <div className="telegram-security-note">
          <ShieldCheck size={17} />
          <span>
            Le token est chiffré sur le serveur et n'est jamais réaffiché. Une fois les deux valeurs enregistrées,
            utilise <strong>Tester</strong> et vérifie que le message arrive dans Telegram.
          </span>
        </div>
        <footer className="modal-actions">
          <button className="primary-button" type="button" onClick={() => setTelegramGuideOpen(false)}>
            Compris
          </button>
        </footer>
      </Modal>
    </div>
  );
}
