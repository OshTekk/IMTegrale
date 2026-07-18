import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  Ban,
  BarChart3,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleUserRound,
  Clock3,
  Eye,
  EyeOff,
  FileClock,
  Gauge,
  Info,
  KeyRound,
  LockKeyhole,
  LogOut,
  Pencil,
  RefreshCw,
  Search,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Signal,
  Trash2,
  UserRoundCheck,
  Users,
  XCircle,
} from "lucide-react";
import { type FormEvent, useDeferredValue, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { Logo } from "../components/Logo";
import { Modal } from "../components/Modal";
import { useToast } from "../components/Toast";
import { adminApi, ApiError } from "../lib/api";
import { formatDate, formatNumber } from "../lib/format";
import type {
  AdminAccount,
  AdminAccountsView,
  AdminPassMetrics,
  AdminSession,
  AdminToken,
  Campus,
  PassAccessStatus,
} from "../types";

const ADMIN_SESSION_KEY = ["admin", "session"] as const;

type AdminAction =
  | "disable"
  | "enable"
  | "revoke_access"
  | "leaderboard_suspend"
  | "leaderboard_restore"
  | "leaderboard_withdraw"
  | "leaderboard_release_wait"
  | "leaderboard_clear_cooldown"
  | "leaderboard_delete_data"
  | "leaderboard_refresh_score_basis"
  | "auth_clear_cooldown"
  | "profile_refresh";

interface AuditItem {
  id: number;
  action: string;
  target_account_id: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

interface ActionDefinition {
  title: string;
  description: string;
  confirmation: string;
  reasonRequired?: boolean;
  dangerous?: boolean;
}

const ACTIONS: Record<AdminAction, ActionDefinition> = {
  disable: { title: "Désactiver le compte", description: "Toutes les sessions seront fermées et tous les tokens actifs révoqués.", confirmation: "Désactiver", reasonRequired: true, dangerous: true },
  enable: { title: "Réactiver le compte", description: "L'utilisateur pourra de nouveau se connecter avec PASS.", confirmation: "Réactiver" },
  revoke_access: { title: "Révoquer tous les accès", description: "Ferme les sessions web et révoque les tokens sans désactiver le compte.", confirmation: "Révoquer", dangerous: true },
  leaderboard_suspend: { title: "Suspendre la publication", description: "Le profil disparaît du classement jusqu'à une restauration manuelle.", confirmation: "Suspendre", reasonRequired: true, dangerous: true },
  leaderboard_restore: { title: "Restaurer la publication", description: "Le profil redevient visible s'il participe encore au classement.", confirmation: "Restaurer" },
  leaderboard_withdraw: { title: "Retirer du classement", description: "Le retrait est immédiat. Le délai normal de 48 heures avant réactivation est conservé.", confirmation: "Retirer", dangerous: true },
  leaderboard_release_wait: { title: "Ouvrir le classement maintenant", description: "Met fin au délai initial de 48 heures. L'utilisateur accède immédiatement aux classements.", confirmation: "Donner accès" },
  leaderboard_clear_cooldown: { title: "Lever le délai de réactivation", description: "Autorise exceptionnellement l'utilisateur à rejoindre de nouveau le classement sans attendre.", confirmation: "Lever le délai" },
  leaderboard_delete_data: { title: "Effacer les données leaderboard", description: "Efface la participation et le consentement, sans toucher au profil PASS, aux notes ni au compte.", confirmation: "Effacer", dangerous: true },
  leaderboard_refresh_score_basis: { title: "Actualiser les coefficients du classement", description: "Recopie la dernière génération complète d'ECTS officiels COMPETENCES. Les valeurs manuelles restent exclues.", confirmation: "Actualiser", reasonRequired: true },
  auth_clear_cooldown: { title: "Lever le cooldown PASS", description: "Réinitialise exceptionnellement le délai de sécurité lié aux échecs de connexion de ce compte. La protection par adresse cliente reste active.", confirmation: "Lever le cooldown", reasonRequired: true },
  profile_refresh: { title: "Actualiser le profil officiel", description: "Le prénom, le nom, le campus, le cursus et la promotion seront relus sur PASS lors de la prochaine synchronisation autorisée.", confirmation: "Programmer l'actualisation" },
};

const STATE_LABELS: Record<AdminAccount["leaderboard"]["state"], string> = {
  not_joined: "Non inscrit",
  cooldown: "Délai 48 h",
  pending: "En attente",
  active: "Actif",
  suspended: "Suspendu",
};

function PasswordInput({ value, onChange, autoComplete }: { value: string; onChange: (value: string) => void; autoComplete: string }) {
  const [visible, setVisible] = useState(false);
  return <div className="password-field"><input type={visible ? "text" : "password"} value={value} onChange={(event) => onChange(event.target.value)} autoComplete={autoComplete} required /><button className="field-icon" type="button" onClick={() => setVisible((current) => !current)} aria-label={visible ? "Masquer le mot de passe" : "Afficher le mot de passe"}>{visible ? <EyeOff size={17} /> : <Eye size={17} />}</button></div>;
}

function AdminUnavailable({ detail }: { detail?: string }) {
  return (
    <main className="admin-gate-page">
      <header><Logo /></header>
      <section className="admin-gate-panel admin-unavailable">
        <span><LockKeyhole size={25} /></span>
        <h1>Portail indisponible</h1>
        <p>{detail ?? "Cette interface n'est accessible que depuis l'identité Tailscale administrateur autorisée."}</p>
        <Link className="secondary-button" to="/">Retour à IMTégrale</Link>
      </section>
    </main>
  );
}

function AdminLogin({ onAuthenticated }: { onAuthenticated: (session: AdminSession) => void }) {
  const { showToast } = useToast();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const login = useMutation({
    mutationFn: () => adminApi<AdminSession>("/api/v1/admin/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
    onSuccess: (session) => { setPassword(""); onAuthenticated(session); },
    onError: (error) => showToast(error.message, "error"),
  });
  return (
    <main className="admin-gate-page">
      <header><Logo /><span><ShieldCheck size={16} /> Administration privée</span></header>
      <section className="admin-gate-panel">
        <div className="admin-gate-heading"><span><LockKeyhole size={21} /></span><div><small>Accès séparé</small><h1>Portail administrateur</h1><p>Authentification réservée à l'identité réseau autorisée.</p></div></div>
        <form className="login-form" onSubmit={(event) => { event.preventDefault(); login.mutate(); }}>
          <label>Identifiant administrateur<input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" required /></label>
          <label>Mot de passe<PasswordInput value={password} onChange={setPassword} autoComplete="current-password" /></label>
          <button className="primary-button login-submit" type="submit" disabled={login.isPending}>{login.isPending ? <span className="spinner" /> : <Shield size={18} />} Se connecter</button>
        </form>
        <footer><LockKeyhole size={14} /> Session isolée de la connexion étudiante</footer>
      </section>
    </main>
  );
}

function ForcedPasswordChange({ session, onChanged }: { session: AdminSession; onChanged: (session: AdminSession) => void }) {
  const { showToast } = useToast();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const change = useMutation({
    mutationFn: () => adminApi<AdminSession>("/api/v1/admin/auth/password", { method: "POST", body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }) }),
    onSuccess: (next) => { onChanged(next); showToast("Mot de passe administrateur remplacé"); },
    onError: (error) => showToast(error.message, "error"),
  });
  const valid = newPassword.length >= 16 && newPassword === confirmation && newPassword !== currentPassword;
  return (
    <main className="admin-gate-page">
      <header><Logo /><span><ShieldAlert size={16} /> Initialisation requise</span></header>
      <section className="admin-gate-panel admin-password-panel">
        <div className="admin-gate-heading"><span><KeyRound size={21} /></span><div><small>Compte {session.username}</small><h1>Remplacer le mot de passe initial</h1><p>Cette étape est obligatoire avant tout accès aux comptes.</p></div></div>
        <form className="login-form" onSubmit={(event) => { event.preventDefault(); if (valid) change.mutate(); }}>
          <label>Mot de passe actuel<PasswordInput value={currentPassword} onChange={setCurrentPassword} autoComplete="current-password" /></label>
          <label>Nouveau mot de passe<PasswordInput value={newPassword} onChange={setNewPassword} autoComplete="new-password" /><small className="field-note">16 caractères minimum avec une combinaison robuste.</small></label>
          <label>Confirmer le nouveau mot de passe<PasswordInput value={confirmation} onChange={setConfirmation} autoComplete="new-password" /></label>
          {confirmation && newPassword !== confirmation && <div className="form-error">Les deux mots de passe ne correspondent pas.</div>}
          <button className="primary-button login-submit" type="submit" disabled={!valid || change.isPending}>{change.isPending ? <span className="spinner" /> : <Check size={18} />} Enregistrer et continuer</button>
        </form>
      </section>
    </main>
  );
}

function AdminStats({ data }: { data: AdminAccountsView | undefined }) {
  const stats = data?.stats;
  return (
    <section className="admin-stats" aria-label="Indicateurs administrateur">
      <div><span><Users size={19} /></span><strong>{stats?.accounts ?? 0}</strong><small>comptes</small></div>
      <div><span><CheckCircle2 size={19} /></span><strong>{stats?.participants ?? 0}</strong><small>participants</small></div>
      <div><span><ShieldAlert size={19} /></span><strong>{stats?.reviews ?? 0}</strong><small>à vérifier</small></div>
      <div><span><Ban size={19} /></span><strong>{stats?.disabled ?? 0}</strong><small>désactivés</small></div>
    </section>
  );
}

function LeaderboardState({ account }: { account: AdminAccount }) {
  const state = account.leaderboard.state;
  return <span className={`admin-state admin-state-${state}`}><i />{STATE_LABELS[state]}</span>;
}

function AccountsTable({ accounts, onSelect }: { accounts: AdminAccount[]; onSelect: (account: AdminAccount) => void }) {
  if (!accounts.length) return <EmptyState icon={<Search size={20} />} title="Aucun compte trouvé" detail="Aucun nom ou identifiant IMT ne correspond à cette recherche." />;
  return (
    <div className="admin-table-wrap">
      <table className="admin-accounts-table">
        <thead><tr><th>Compte</th><th>Accès</th><th>Synchronisation</th><th>Leaderboard</th><th><span className="sr-only">Ouvrir</span></th></tr></thead>
        <tbody>{accounts.map((account) => (
          <tr key={account.id} className={account.is_disabled ? "is-disabled" : ""}>
            <td data-label="Compte"><span className="account-avatar">{account.display_name.slice(0, 1).toUpperCase()}</span><div><strong>{account.display_name}</strong><small>{account.imt_username}</small></div></td>
            <td data-label="Accès"><span className={`access-status ${account.is_disabled ? "disabled" : "enabled"}`}>{account.is_disabled ? <XCircle size={14} /> : <CheckCircle2 size={14} />}{account.is_disabled ? "Désactivé" : "Actif"}</span><small>{account.session_count} session{account.session_count === 1 ? "" : "s"} · {account.active_token_count} token{account.active_token_count === 1 ? "" : "s"}</small></td>
            <td data-label="Synchronisation"><strong>{account.last_sync_status === "success" ? "À jour" : account.last_sync_status === "error" ? "Erreur" : account.last_sync_status === "running" ? "En cours" : "Jamais"}</strong><small>{formatDate(account.last_sync_at)}</small></td>
            <td data-label="Leaderboard"><LeaderboardState account={account} />{account.leaderboard.official_first_name && account.leaderboard.official_last_name && <small>{account.leaderboard.official_first_name} {account.leaderboard.official_last_name}</small>}</td>
            <td><button className="icon-button" type="button" onClick={() => onSelect(account)} aria-label={`Gérer ${account.display_name}`}><ChevronRight size={18} /></button></td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function AccountDetail({
  account,
  open,
  onClose,
  onAction,
  onSync,
  onProfile,
  onDeleteAccount,
  onDeleteToken,
  pending,
}: {
  account: AdminAccount | null;
  open: boolean;
  onClose: () => void;
  onAction: (action: AdminAction) => void;
  onSync: () => void;
  onProfile: (value: { campus: Exclude<Campus, "unknown">; program: string; promotion_year: number; reason: string }) => void;
  onDeleteAccount: () => void;
  onDeleteToken: (token: AdminToken) => void;
  pending: boolean;
}) {
  const [campus, setCampus] = useState<Exclude<Campus, "unknown"> | "">("");
  const [program, setProgram] = useState("");
  const [promotionYear, setPromotionYear] = useState("");
  const [profileReason, setProfileReason] = useState("");
  useEffect(() => {
    if (!account) return;
    setCampus(account.leaderboard.campus === "unknown" ? (account.leaderboard.detected_campus === "unknown" ? "" : account.leaderboard.detected_campus) : account.leaderboard.campus);
    setProgram(account.leaderboard.program === "unknown" ? "" : account.leaderboard.program);
    setPromotionYear(account.leaderboard.promotion_year?.toString() ?? "");
    setProfileReason("");
  }, [account]);
  if (!account) return null;
  const promotionNumber = Number(promotionYear);
  const profileValid = Boolean(
    campus
    && program.trim().length >= 2
    && promotionNumber >= 2000
    && promotionNumber <= 2100
    && profileReason.trim().length >= 3
  );
  const leaderboardState = account.leaderboard.state;
  const ectsBasis = account.leaderboard.score_ects_basis ?? {};
  const ectsCount = Object.keys(ectsBasis).length;
  const ectsTotal = Object.values(ectsBasis).reduce((total, value) => total + value, 0);
  const syncMode = !account.auto_sync_enabled
    ? "Désactivée"
    : account.auto_sync_adaptive
      ? `Adaptative · ${account.auto_sync_current_interval_hours} h actuellement`
      : `Fixe · toutes les ${account.auto_sync_interval_hours} h`;
  return (
    <Modal open={open} title={account.display_name} description={account.imt_username} onClose={onClose} size="large">
      <div className="admin-account-detail">
        {account.is_disabled && <div className="admin-disabled-banner"><Ban size={18} /><div><strong>Compte désactivé</strong><span>{account.disabled_reason || "Aucun motif renseigné"} · {formatDate(account.disabled_at)}</span></div></div>}
        <section className="admin-detail-summary">
          <div><span>Dernière connexion</span><strong>{formatDate(account.last_login_at)}</strong></div>
          <div><span>Accès actifs</span><strong>{account.session_count} session{account.session_count === 1 ? "" : "s"} · {account.active_token_count} token{account.active_token_count === 1 ? "" : "s"} · {account.passkey_count} passkey{account.passkey_count === 1 ? "" : "s"}</strong></div>
          <div><span>Actualisation auto</span><strong>{syncMode}</strong></div>
          <div><span>Création</span><strong>{formatDate(account.created_at)}</strong></div>
        </section>
        <section className="admin-detail-section">
          <header><div><h3>Compte et accès</h3><p>Les actions de sécurité ferment les sessions immédiatement.</p></div></header>
          <div className="admin-button-row">
            <button className="secondary-button" type="button" onClick={onSync} disabled={pending || account.is_disabled} title="Contourne le cooldown utilisateur et conserve le verrou de concurrence"><RefreshCw size={16} /> Forcer la synchronisation</button>
            <button className="secondary-button" type="button" onClick={() => onAction("profile_refresh")} disabled={pending || account.is_disabled}><Signal size={16} /> Relire le profil PASS</button>
            <button className="secondary-button" type="button" onClick={() => onAction("auth_clear_cooldown")} disabled={pending || account.is_disabled}><Clock3 size={16} /> Lever le cooldown PASS</button>
            <button className="secondary-button" type="button" onClick={() => onAction("revoke_access")} disabled={pending}><KeyRound size={16} /> Révoquer les accès</button>
            <button className={account.is_disabled ? "secondary-button" : "danger-button armed"} type="button" onClick={() => onAction(account.is_disabled ? "enable" : "disable")} disabled={pending}>{account.is_disabled ? <UserRoundCheck size={16} /> : <Ban size={16} />}{account.is_disabled ? "Réactiver" : "Désactiver"}</button>
          </div>
        </section>
        <section className="admin-detail-section">
          <header><div><h3>Tokens d'accès</h3><p>Suppression ciblée et fermeture immédiate des sessions associées.</p></div><span className="admin-section-count">{account.tokens.length}</span></header>
          {account.tokens.length ? <div className="admin-token-list">{account.tokens.map((token) => {
            const active = !token.revoked_at && (!token.expires_at || new Date(token.expires_at) > new Date());
            const role = token.role === "owner" ? "Personnel" : token.role === "editor" ? "Lecture (ancien)" : "Lecture";
            return <div key={token.id} className={active ? "" : "is-inactive"}><span className="admin-token-icon"><KeyRound size={17} /></span><div><strong>{token.name}</strong><small>{role} · {token.prefix} · {active ? `Actif${token.last_used_at ? `, utilisé ${formatDate(token.last_used_at)}` : ""}` : "Révoqué ou expiré"}</small></div><button className="icon-button danger-icon" type="button" onClick={() => onDeleteToken(token)} aria-label={`Supprimer le token ${token.name}`} title="Supprimer définitivement"><Trash2 size={16} /></button></div>;
          })}</div> : <div className="admin-empty-inline"><KeyRound size={17} /> Aucun token partagé</div>}
        </section>
        <section className="admin-detail-section">
          <header><div><h3>Profil académique et leaderboard</h3><p><LeaderboardState account={account} /> · source {account.leaderboard.academic_source === "pass" ? "PASS" : account.leaderboard.academic_source === "admin" ? "correction admin" : "non confirmée"} · vérifié {formatDate(account.leaderboard.academic_verified_at)}</p></div>{account.leaderboard.classification_review_required && <span className="review-flag"><AlertTriangle size={14} /> Divergence à vérifier</span>}</header>
          <div className="admin-official-profile"><Info size={16} /><span>Le prénom et le nom sont figés depuis PASS. Le campus, le cursus et la promotion segmentent le classement ; toute correction administrative est historisée.</span></div>
          {account.leaderboard.has_leaderboard_data && <div className="admin-score-basis">
            <span><ShieldCheck size={18} /></span>
            <div>
              <strong>Coefficients officiels du classement</strong>
              <small>{ectsCount ? `${ectsCount} UE · ${formatNumber(ectsTotal)} ECTS` : "Aucune base de calcul disponible"}{account.leaderboard.score_basis_updated_at ? ` · ${formatDate(account.leaderboard.score_basis_updated_at)}` : ""}</small>
            </div>
            <button className="secondary-button" type="button" onClick={() => onAction("leaderboard_refresh_score_basis")} disabled={pending || leaderboardState === "not_joined" || leaderboardState === "cooldown"}><RefreshCw size={16} /> Actualiser</button>
          </div>}
          <form className="admin-profile-form" onSubmit={(event) => { event.preventDefault(); if (profileValid && campus) onProfile({ campus, program: program.trim().toUpperCase(), promotion_year: promotionNumber, reason: profileReason.trim() }); }}>
            <label>Identité PASS<input value={account.leaderboard.official_first_name && account.leaderboard.official_last_name ? `${account.leaderboard.official_first_name} ${account.leaderboard.official_last_name}` : "Non disponible"} disabled /></label>
            <label>Campus<select value={campus} onChange={(event) => setCampus(event.target.value as Exclude<Campus, "unknown">)} required><option value="" disabled>Sélectionner</option><option value="rennes">Rennes</option><option value="brest">Brest</option><option value="nantes">Nantes</option><option value="other">Autre</option></select></label>
            <label>Cursus<input value={program} onChange={(event) => setProgram(event.target.value)} minLength={2} maxLength={32} placeholder="FIP, FIT, FIL, FISE…" required /></label>
            <label>Promotion<input type="number" value={promotionYear} onChange={(event) => setPromotionYear(event.target.value)} min={2000} max={2100} placeholder="2028" required /></label>
            <label className="admin-profile-reason">Motif de la correction<input value={profileReason} onChange={(event) => setProfileReason(event.target.value)} minLength={3} maxLength={240} placeholder="Correction demandée par l'étudiant…" required /></label>
            <button className="secondary-button" type="submit" disabled={!profileValid || pending}><Pencil size={16} /> Corriger</button>
          </form>
          <div className="admin-button-row moderation-actions">
            {(leaderboardState === "active" || leaderboardState === "pending") && <button className="secondary-button" type="button" onClick={() => onAction("leaderboard_suspend")}><ShieldAlert size={16} /> Suspendre</button>}
            {leaderboardState === "suspended" && <button className="secondary-button" type="button" onClick={() => onAction("leaderboard_restore")}><ShieldCheck size={16} /> Restaurer</button>}
            {leaderboardState === "pending" && <button className="secondary-button" type="button" onClick={() => onAction("leaderboard_release_wait")}><Clock3 size={16} /> Donner accès maintenant</button>}
            {(leaderboardState === "active" || leaderboardState === "pending" || leaderboardState === "suspended") && <button className="secondary-button" type="button" onClick={() => onAction("leaderboard_withdraw")}><EyeOff size={16} /> Retirer</button>}
            {leaderboardState === "cooldown" && <button className="secondary-button" type="button" onClick={() => onAction("leaderboard_clear_cooldown")}><Clock3 size={16} /> Lever le délai</button>}
            {account.leaderboard.has_leaderboard_data && <button className="danger-button" type="button" onClick={() => onAction("leaderboard_delete_data")}><Trash2 size={16} /> Effacer les données</button>}
          </div>
        </section>
        <section className="admin-detail-section admin-danger-zone">
          <header><div><h3>Suppression définitive</h3><p>Supprime le compte, ses notes, paramètres, sessions, tokens et données de classement.</p></div></header>
          <button className="danger-button armed" type="button" onClick={onDeleteAccount} disabled={pending}><Trash2 size={16} /> Supprimer ce compte</button>
        </section>
      </div>
      <footer className="modal-actions"><button className="secondary-button" type="button" onClick={onClose}>Fermer</button></footer>
    </Modal>
  );
}

function ActionModal({ account, action, open, onClose, onConfirm, pending }: { account: AdminAccount | null; action: AdminAction | null; open: boolean; onClose: () => void; onConfirm: (reason: string) => void; pending: boolean }) {
  const [reason, setReason] = useState("");
  useEffect(() => { if (!open) setReason(""); }, [open]);
  if (!account || !action) return null;
  const definition = ACTIONS[action];
  return (
    <Modal open={open} title={definition.title} description={`Compte concerné : ${account.display_name}`} onClose={onClose} size="small">
      <form className="modal-form" onSubmit={(event) => { event.preventDefault(); if (!definition.reasonRequired || reason.trim()) onConfirm(reason.trim()); }}>
        <div className={`admin-action-warning ${definition.dangerous ? "danger" : ""}`}><AlertTriangle size={19} /><p>{definition.description}</p></div>
        {(definition.reasonRequired || action === "revoke_access") && <label>Motif {definition.reasonRequired ? "obligatoire" : "facultatif"}<input value={reason} onChange={(event) => setReason(event.target.value)} maxLength={240} placeholder="Incident, demande de l'utilisateur…" required={definition.reasonRequired} /></label>}
        <footer className="modal-actions"><button className="secondary-button" type="button" onClick={onClose}>Annuler</button><button className={definition.dangerous ? "danger-button armed" : "primary-button"} type="submit" disabled={pending || Boolean(definition.reasonRequired && !reason.trim())}>{pending ? <span className="spinner" /> : definition.dangerous ? <AlertTriangle size={16} /> : <Check size={16} />}{definition.confirmation}</button></footer>
      </form>
    </Modal>
  );
}

function ForcedSyncModal({ account, open, onClose, onConfirm, pending }: { account: AdminAccount | null; open: boolean; onClose: () => void; onConfirm: (reason: string) => void; pending: boolean }) {
  const [reason, setReason] = useState("");
  useEffect(() => { if (!open) setReason(""); }, [open]);
  if (!account) return null;
  return (
    <Modal open={open} title="Forcer la synchronisation PASS" description={`Compte concerné : ${account.display_name}`} onClose={onClose} size="small">
      <form className="modal-form" onSubmit={(event) => { event.preventDefault(); if (reason.trim().length >= 3) onConfirm(reason.trim()); }}>
        <div className="admin-action-warning"><RefreshCw size={19} /><p>Cette opération contourne exceptionnellement le quota du compte. Le verrou global, le repos inter-requêtes et le circuit de protection restent appliqués.</p></div>
        <label>Motif obligatoire<input value={reason} onChange={(event) => setReason(event.target.value)} minLength={3} maxLength={240} placeholder="Demande urgente vérifiée…" required /></label>
        <footer className="modal-actions"><button className="secondary-button" type="button" onClick={onClose}>Annuler</button><button className="primary-button" type="submit" disabled={pending || reason.trim().length < 3}>{pending ? <span className="spinner" /> : <RefreshCw size={16} />} Lancer</button></footer>
      </form>
    </Modal>
  );
}

type DeleteTarget = { kind: "account" } | { kind: "token"; token: AdminToken };

function DeleteResourceModal({ account, target, open, onClose, onConfirm, pending }: { account: AdminAccount | null; target: DeleteTarget | null; open: boolean; onClose: () => void; onConfirm: (reason: string) => void; pending: boolean }) {
  const [reason, setReason] = useState("");
  const [confirmation, setConfirmation] = useState("");
  useEffect(() => { if (!open) { setReason(""); setConfirmation(""); } }, [open]);
  if (!account || !target) return null;
  const deletingAccount = target.kind === "account";
  const subject = deletingAccount ? account.display_name : target.token.name;
  return (
    <Modal open={open} title={deletingAccount ? "Supprimer définitivement le compte" : "Supprimer définitivement le token"} description={`Élément concerné : ${subject}`} onClose={onClose} size="small">
      <form className="modal-form" onSubmit={(event) => { event.preventDefault(); if (reason.trim() && confirmation === "SUPPRIMER") onConfirm(reason.trim()); }}>
        <div className="admin-action-warning danger"><AlertTriangle size={19} /><p>{deletingAccount ? "Toutes les données de ce compte seront supprimées sans possibilité de restauration depuis l'interface." : "Le token et toutes ses sessions seront supprimés immédiatement. Le lien partagé cessera de fonctionner."}</p></div>
        <label>Motif obligatoire<input value={reason} onChange={(event) => setReason(event.target.value)} maxLength={240} placeholder="Demande de l'utilisateur, sécurité…" required /></label>
        <label>Écris <strong>SUPPRIMER</strong> pour confirmer<input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} autoComplete="off" required /></label>
        <footer className="modal-actions"><button className="secondary-button" type="button" onClick={onClose}>Annuler</button><button className="danger-button armed" type="submit" disabled={pending || !reason.trim() || confirmation !== "SUPPRIMER"}>{pending ? <span className="spinner" /> : <Trash2 size={16} />} Supprimer définitivement</button></footer>
      </form>
    </Modal>
  );
}

function AuditLog({ items, pending }: { items: AuditItem[] | undefined; pending: boolean }) {
  if (pending) return <div className="skeleton admin-audit-skeleton" />;
  if (!items?.length) return <EmptyState icon={<FileClock size={20} />} title="Journal vide" detail="Les connexions et actions administratives apparaîtront ici." />;
  return <div className="admin-audit-list">{items.map((item) => <article key={item.id}><span><FileClock size={17} /></span><div><strong>{item.action.replaceAll(".", " · ")}</strong><small>{item.target_account_id ? `Compte ${item.target_account_id.slice(0, 8)} · ` : ""}{formatDate(item.created_at)}</small></div></article>)}</div>;
}

type MetricsWindow = "24h" | "7d" | "30d";
type AdminPassStatus = Omit<PassAccessStatus, "quota" | "profile">;

const PASS_STATE_LABELS: Record<AdminPassStatus["state"], string> = {
  available: "Disponible",
  busy: "Opération en cours",
  resting: "Repos inter-requêtes",
  circuit_open: "Protection active",
};

const PASS_REASON_LABELS: Record<string, string> = {
  PASS_ACCOUNT_QUOTA: "Quota du compte",
  PASS_BUSY: "Une autre opération était active",
  PASS_QUIET_PERIOD: "Repos global",
  PASS_CIRCUIT_OPEN: "Circuit PASS ouvert",
  PASS_PROBE_RUNNING: "Sonde déjà en cours",
  PASS_AUTOMATIC_PRIORITY: "Priorité à une actualisation en retard",
};

function metricEntries(values: Record<string, number>): Array<[string, number]> {
  return Object.entries(values).sort((left, right) => right[1] - left[1]);
}

function formatMilliseconds(value: number | null): string {
  if (value === null) return "—";
  if (value >= 1_000) return `${(value / 1_000).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} s`;
  return `${Math.round(value)} ms`;
}

function PassConsole({
  status,
  metrics,
  accounts,
  window,
  onWindow,
  onRefresh,
  onProbe,
  pending,
}: {
  status: AdminPassStatus | undefined;
  metrics: AdminPassMetrics | undefined;
  accounts: AdminAccount[];
  window: MetricsWindow;
  onWindow: (window: MetricsWindow) => void;
  onRefresh: () => void;
  onProbe: (accountId: string, reason: string) => void;
  pending: boolean;
}) {
  const [accountId, setAccountId] = useState("");
  const [reason, setReason] = useState("");
  const circuit = status?.circuit;
  const errors = metricEntries(metrics?.errors ?? {});
  const denials = metricEntries(metrics?.denials ?? {});
  const operationKinds = metricEntries(metrics?.by_kind ?? {});
  return (
    <section className="admin-content-panel admin-pass-console">
      <header>
        <div><h2>Accès PASS</h2><p>Supervision anonymisée et protection globale</p></div>
        <div className="admin-pass-header-actions">
          {status && <span className={`pass-state pass-state-${status.state}`}><i />{PASS_STATE_LABELS[status.state]}</span>}
          <button className="icon-button" type="button" onClick={onRefresh} aria-label="Actualiser la supervision PASS" title="Actualiser"><RefreshCw size={17} /></button>
        </div>
      </header>
      {circuit && circuit.state !== "closed" && <div className="admin-pass-alert"><ShieldAlert size={19} /><div><strong>Protection PASS {circuit.state === "open" ? "active" : "en observation"}</strong><span>{circuit.reason ?? "Réouverture contrôlée en attente"}{circuit.next_probe_at ? ` · prochain essai ${formatDate(circuit.next_probe_at)}` : ""}</span></div></div>}
      <div className="admin-metrics-toolbar">
        <div className="segmented-control" aria-label="Fenêtre de métriques">
          {(["24h", "7d", "30d"] as MetricsWindow[]).map((value) => <button key={value} type="button" className={window === value ? "active" : ""} onClick={() => onWindow(value)}>{value}</button>)}
        </div>
        <span><LockKeyhole size={14} /> Conservation 30 jours · aucune identité stockée</span>
      </div>
      <div className="admin-pass-metrics" aria-label="Métriques PASS">
        <div><span><Activity size={18} /></span><strong>{metrics?.operations ?? 0}</strong><small>opérations</small></div>
        <div><span><Signal size={18} /></span><strong>{metrics?.real_requests ?? 0}</strong><small>requêtes réseau</small></div>
        <div><span><Gauge size={18} /></span><strong>{Math.round((metrics?.session_reuse.hit_rate ?? 0) * 100)} %</strong><small>sessions réutilisées</small></div>
        <div><span><BarChart3 size={18} /></span><strong>{formatMilliseconds(metrics?.duration_ms.p95 ?? null)}</strong><small>durée p95</small></div>
      </div>
      <div className="admin-pass-detail-grid">
        <section>
          <h3>Performance</h3>
          <dl><div><dt>Moyenne</dt><dd>{formatMilliseconds(metrics?.duration_ms.mean ?? null)}</dd></div><div><dt>Pire durée</dt><dd>{formatMilliseconds(metrics?.duration_ms.worst ?? null)}</dd></div><div><dt>SSO complets</dt><dd>{metrics?.session_reuse.full_sso_performed ?? 0}</dd></div><div><dt>SSO évités</dt><dd>{metrics?.session_reuse.full_sso_avoided ?? 0}</dd></div><div><dt>Profils lus</dt><dd>{metrics?.profiles.fetched ?? 0}</dd></div><div><dt>Profils évités</dt><dd>{metrics?.profiles.skipped ?? 0}</dd></div></dl>
        </section>
        <section>
          <h3>Opérations</h3>
          {operationKinds.length ? <ul>{operationKinds.map(([key, value]) => <li key={key}><span>{key === "login" ? "Connexions" : key === "sync" ? "Synchronisations" : key}</span><strong>{value}</strong></li>)}</ul> : <p className="admin-pass-empty">Aucune opération sur cette période</p>}
        </section>
        <section>
          <h3>Refus avant réseau</h3>
          {denials.length ? <ul>{denials.map(([key, value]) => <li key={key}><span>{PASS_REASON_LABELS[key] ?? key}</span><strong>{value}</strong></li>)}</ul> : <p className="admin-pass-empty">Aucun refus sur cette période</p>}
        </section>
        <section>
          <h3>Erreurs classifiées</h3>
          {errors.length ? <ul>{errors.map(([key, value]) => <li key={key}><span>{key}</span><strong>{value}</strong></li>)}</ul> : <p className="admin-pass-empty">Aucune erreur sur cette période</p>}
        </section>
      </div>
      <form className="admin-probe-form" onSubmit={(event) => { event.preventDefault(); if (accountId && reason.trim().length >= 3) onProbe(accountId, reason.trim()); }}>
        <div><ShieldCheck size={18} /><div><h3>Sonde contrôlée</h3><p>Une opération unique, prioritaire et auditée.</p></div></div>
        <label>Compte<select value={accountId} onChange={(event) => setAccountId(event.target.value)} required><option value="" disabled>Sélectionner un compte</option>{accounts.filter((account) => !account.is_disabled).map((account) => <option key={account.id} value={account.id}>{account.display_name} · {account.imt_username}</option>)}</select></label>
        <label>Motif<input value={reason} onChange={(event) => setReason(event.target.value)} minLength={3} maxLength={240} placeholder="Validation après incident PASS…" required /></label>
        <button className="secondary-button" type="submit" disabled={pending || !accountId || reason.trim().length < 3}>{pending ? <span className="spinner" /> : <Activity size={16} />} Lancer la sonde</button>
      </form>
    </section>
  );
}

function AdminDashboard({ session, onLogout }: { session: AdminSession; onLogout: () => void }) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [tab, setTab] = useState<"accounts" | "pass" | "audit">("accounts");
  const [metricsWindow, setMetricsWindow] = useState<MetricsWindow>("24h");
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search.trim());
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<AdminAction | null>(null);
  const [syncOpen, setSyncOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null);
  const accountsKey = ["admin", "accounts", deferredSearch] as const;
  const accounts = useQuery({
    queryKey: accountsKey,
    queryFn: () => adminApi<AdminAccountsView>(`/api/v1/admin/accounts?search=${encodeURIComponent(deferredSearch)}`),
    staleTime: 5_000,
  });
  const audit = useQuery({ queryKey: ["admin", "audit"], queryFn: () => adminApi<AuditItem[]>("/api/v1/admin/audit"), enabled: tab === "audit", staleTime: 5_000 });
  const passStatus = useQuery({ queryKey: ["admin", "pass", "status"], queryFn: () => adminApi<AdminPassStatus>("/api/v1/admin/pass/status"), enabled: tab === "pass", staleTime: 15_000, refetchInterval: tab === "pass" ? 30_000 : false });
  const passMetrics = useQuery({ queryKey: ["admin", "pass", "metrics", metricsWindow], queryFn: () => adminApi<AdminPassMetrics>(`/api/v1/admin/pass/metrics?window=${metricsWindow}`), enabled: tab === "pass", staleTime: 10_000 });
  const selected = useMemo(() => accounts.data?.accounts.find((account) => account.id === selectedId) ?? null, [accounts.data, selectedId]);
  const refreshAccounts = (updated?: AdminAccount) => {
    if (updated) queryClient.setQueryData<AdminAccountsView>(accountsKey, (current) => current ? { ...current, accounts: current.accounts.map((account) => account.id === updated.id ? updated : account) } : current);
    queryClient.invalidateQueries({ queryKey: ["admin", "accounts"] });
    queryClient.invalidateQueries({ queryKey: ["admin", "audit"] });
    queryClient.invalidateQueries({ queryKey: ["admin", "pass"] });
  };
  const action = useMutation({
    mutationFn: ({ accountId, actionName, reason }: { accountId: string; actionName: AdminAction; reason: string }) => adminApi<AdminAccount>(`/api/v1/admin/accounts/${accountId}/actions`, { method: "POST", body: JSON.stringify({ action: actionName, reason: reason || null }) }),
    onSuccess: (updated) => { refreshAccounts(updated); setPendingAction(null); showToast("Action appliquée et journalisée"); },
    onError: (error) => showToast(error.message, "error"),
  });
  const sync = useMutation({
    mutationFn: ({ accountId, reason }: { accountId: string; reason: string }) => adminApi(`/api/v1/admin/accounts/${accountId}/sync`, { method: "POST", body: JSON.stringify({ reason }), headers: { "Idempotency-Key": crypto.randomUUID() } }),
    onSuccess: () => { setSyncOpen(false); window.setTimeout(() => refreshAccounts(), 1500); showToast("Synchronisation PASS lancée et journalisée"); },
    onError: (error) => showToast(error.message, "error"),
  });
  const profile = useMutation({
    mutationFn: ({ accountId, value }: { accountId: string; value: { campus: Exclude<Campus, "unknown">; program: string; promotion_year: number; reason: string } }) => adminApi<AdminAccount>(`/api/v1/admin/accounts/${accountId}/leaderboard`, { method: "PATCH", body: JSON.stringify(value) }),
    onSuccess: (updated) => { refreshAccounts(updated); showToast("Profil académique corrigé et journalisé"); },
    onError: (error) => showToast(error.message, "error"),
  });
  const deleteToken = useMutation({
    mutationFn: ({ accountId, tokenId, reason }: { accountId: string; tokenId: string; reason: string }) => adminApi<AdminAccount>(`/api/v1/admin/accounts/${accountId}/tokens/${tokenId}`, { method: "DELETE", body: JSON.stringify({ confirmation: "SUPPRIMER", reason }) }),
    onSuccess: (updated) => { refreshAccounts(updated); setDeleteTarget(null); showToast("Token supprimé et sessions associées fermées"); },
    onError: (error) => showToast(error.message, "error"),
  });
  const deleteAccount = useMutation({
    mutationFn: ({ accountId, reason }: { accountId: string; reason: string }) => adminApi<{ deleted: boolean; id: string }>(`/api/v1/admin/accounts/${accountId}`, { method: "DELETE", body: JSON.stringify({ confirmation: "SUPPRIMER", reason }) }),
    onSuccess: () => { setDeleteTarget(null); setSelectedId(null); refreshAccounts(); showToast("Compte supprimé définitivement"); },
    onError: (error) => showToast(error.message, "error"),
  });
  const probe = useMutation({
    mutationFn: ({ accountId, reason }: { accountId: string; reason: string }) => adminApi("/api/v1/admin/pass/probe", { method: "POST", body: JSON.stringify({ account_id: accountId, reason }), headers: { "Idempotency-Key": crypto.randomUUID() } }),
    onSuccess: () => { window.setTimeout(() => queryClient.invalidateQueries({ queryKey: ["admin", "pass"] }), 1500); showToast("Sonde PASS lancée et journalisée"); },
    onError: (error) => showToast(error.message, "error"),
  });
  return (
    <div className="admin-portal">
      <header className="admin-topbar"><Logo /><div><span className="admin-private-pill"><ShieldCheck size={14} /> Portail privé</span><span>{session.username}</span><button className="icon-button" type="button" onClick={onLogout} aria-label="Se déconnecter" title="Se déconnecter"><LogOut size={17} /></button></div></header>
      <main className="admin-main">
        <section className="admin-page-heading"><div><span className="section-kicker">Administration IMTégrale</span><h1>Comptes et publication</h1><p>Contrôles immédiats, corrections explicites et traçabilité complète.</p></div><span><Shield size={25} /></span></section>
        <AdminStats data={accounts.data} />
        <nav className="admin-tabs" aria-label="Sections du portail"><button className={tab === "accounts" ? "active" : ""} type="button" onClick={() => setTab("accounts")}><CircleUserRound size={17} /> Comptes</button><button className={tab === "pass" ? "active" : ""} type="button" onClick={() => setTab("pass")}><Activity size={17} /> PASS</button><button className={tab === "audit" ? "active" : ""} type="button" onClick={() => setTab("audit")}><FileClock size={17} /> Journal d'audit</button></nav>
        {tab === "accounts" ? <section className="admin-content-panel"><header><div><h2>Comptes étudiants</h2><p>{accounts.data?.accounts.length ?? 0} résultat{accounts.data?.accounts.length === 1 ? "" : "s"}</p></div><label className="admin-search"><Search size={17} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Nom ou identifiant IMT" aria-label="Rechercher un compte" /></label></header>{accounts.isPending ? <div className="skeleton admin-table-skeleton" /> : accounts.isError ? <div className="error-panel"><AlertTriangle size={20} /><div><h2>Chargement impossible</h2><p>{accounts.error.message}</p></div></div> : <AccountsTable accounts={accounts.data?.accounts ?? []} onSelect={(account) => setSelectedId(account.id)} />}</section> : tab === "pass" ? <PassConsole status={passStatus.data} metrics={passMetrics.data} accounts={accounts.data?.accounts ?? []} window={metricsWindow} onWindow={setMetricsWindow} onRefresh={() => { passStatus.refetch(); passMetrics.refetch(); }} onProbe={(accountId, reason) => probe.mutate({ accountId, reason })} pending={probe.isPending} /> : <section className="admin-content-panel"><header><div><h2>Journal d'audit</h2><p>100 dernières actions de la console.</p></div><button className="icon-button" type="button" onClick={() => audit.refetch()} aria-label="Actualiser"><RefreshCw size={17} /></button></header><AuditLog items={audit.data} pending={audit.isPending} /></section>}
      </main>
      <AccountDetail account={selected} open={Boolean(selected)} onClose={() => setSelectedId(null)} onAction={setPendingAction} onSync={() => setSyncOpen(true)} onProfile={(value) => selected && profile.mutate({ accountId: selected.id, value })} onDeleteAccount={() => setDeleteTarget({ kind: "account" })} onDeleteToken={(token) => setDeleteTarget({ kind: "token", token })} pending={action.isPending || sync.isPending || profile.isPending || deleteToken.isPending || deleteAccount.isPending} />
      <ActionModal account={selected} action={pendingAction} open={Boolean(selected && pendingAction)} onClose={() => setPendingAction(null)} onConfirm={(reason) => selected && pendingAction && action.mutate({ accountId: selected.id, actionName: pendingAction, reason })} pending={action.isPending} />
      <ForcedSyncModal account={selected} open={Boolean(selected && syncOpen)} onClose={() => setSyncOpen(false)} onConfirm={(reason) => selected && sync.mutate({ accountId: selected.id, reason })} pending={sync.isPending} />
      <DeleteResourceModal account={selected} target={deleteTarget} open={Boolean(selected && deleteTarget)} onClose={() => setDeleteTarget(null)} onConfirm={(reason) => { if (!selected || !deleteTarget) return; if (deleteTarget.kind === "account") deleteAccount.mutate({ accountId: selected.id, reason }); else deleteToken.mutate({ accountId: selected.id, tokenId: deleteTarget.token.id, reason }); }} pending={deleteToken.isPending || deleteAccount.isPending} />
    </div>
  );
}

export function AdminPortal() {
  const queryClient = useQueryClient();
  const session = useQuery({ queryKey: ADMIN_SESSION_KEY, queryFn: () => adminApi<AdminSession>("/api/v1/admin/auth/session"), retry: false, staleTime: 15_000 });
  const logout = useMutation({
    mutationFn: () => adminApi("/api/v1/admin/auth/logout", { method: "POST", body: "{}" }),
    onSuccess: () => { queryClient.removeQueries({ queryKey: ["admin"] }); queryClient.setQueryData(ADMIN_SESSION_KEY, { authenticated: false }); },
  });
  useEffect(() => {
    const unauthorized = () => {
      queryClient.removeQueries({ queryKey: ["admin"] });
      queryClient.setQueryData(ADMIN_SESSION_KEY, { authenticated: false });
    };
    window.addEventListener("botnote:admin-unauthorized", unauthorized);
    return () => window.removeEventListener("botnote:admin-unauthorized", unauthorized);
  }, [queryClient]);
  if (session.isPending) return <div className="app-loading"><Logo /><span className="loading-line" /></div>;
  if (session.isError) {
    const error = session.error;
    return error instanceof ApiError && error.status === 404 ? <AdminUnavailable /> : <AdminUnavailable detail={error.message} />;
  }
  if (!session.data?.authenticated) return <AdminLogin onAuthenticated={(next) => queryClient.setQueryData(ADMIN_SESSION_KEY, next)} />;
  if (session.data.must_change_password) return <ForcedPasswordChange session={session.data} onChanged={(next) => queryClient.setQueryData(ADMIN_SESSION_KEY, next)} />;
  return <AdminDashboard session={session.data} onLogout={() => logout.mutate()} />;
}
