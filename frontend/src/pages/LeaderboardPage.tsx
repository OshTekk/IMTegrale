import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  BookOpenCheck,
  Check,
  CheckCircle2,
  Clock3,
  EyeOff,
  Filter,
  Gauge,
  Info,
  Scale,
  ShieldCheck,
  Trash2,
  Trophy,
  UserRoundCheck,
  Users,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { EmptyState } from "../components/EmptyState";
import { Modal } from "../components/Modal";
import { useToast } from "../components/Toast";
import {
  leaderboardActivateLeaderboard,
  leaderboardEraseLeaderboardData,
  leaderboardWithdrawFromLeaderboard,
} from "../generated/api/sdk.gen";
import { formatDate, formatNumber } from "../lib/format";
import { apiData, throwOnApiError } from "../lib/generatedApi";
import { queryKeys, useLeaderboard, useSession } from "../lib/queries";
import type { Campus, LeaderboardMetric, LeaderboardView } from "../types";

const CAMPUS_LABELS: Record<Campus, string> = {
  rennes: "Rennes",
  brest: "Brest",
  nantes: "Nantes",
  other: "Autre campus",
  unknown: "À confirmer",
};

const MISSING_LABELS: Record<LeaderboardView["eligibility"]["missing"][number], string> = {
  identity: "Prénom ou nom officiel PASS indisponible : actualiser le profil",
  campus: "Campus PASS indisponible : contacter l'administrateur",
  promotion: "Cursus ou promotion PASS indisponible : contacter l'administrateur",
  pass_notes: "Synchroniser au moins une note PASS",
  ects: "Synchroniser les ECTS officiels de toutes tes UE depuis COMPETENCES",
};

function useCountdown(target: string | null) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!target) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [target]);
  const remaining = target ? Math.max(0, new Date(target).getTime() - now) : 0;
  const seconds = Math.floor(remaining / 1000);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  return {
    done: remaining === 0,
    label: `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`,
  };
}

function scoreLabel(metric: LeaderboardMetric, value: number) {
  return metric === "gpa" ? `${formatNumber(value)} / 4` : `${formatNumber(value)} / 20`;
}

const FRESHNESS_LABELS: Record<LeaderboardView["profile"]["freshness"], string> = {
  current: "À jour",
  recommended: "Actualisation conseillée",
  stale: "À actualiser",
};

function RulesModal({ view, open, onClose }: { view: LeaderboardView; open: boolean; onClose: () => void }) {
  return (
    <Modal
      open={open}
      title="Règles du classement"
      description={`Version ${view.rules.version}, mise à jour le ${formatDate(view.rules.updated_at, false)}.`}
      onClose={onClose}
      size="large"
    >
      <div className="leaderboard-rules">
        <section>
          <span>
            <BookOpenCheck size={18} />
          </span>
          <div>
            <strong>Source contrôlée</strong>
            <p>
              {view.rules.source}. {view.rules.weighting}.
            </p>
          </div>
        </section>
        <section>
          <span>
            <ShieldCheck size={18} />
          </span>
          <div>
            <strong>Données académiques officielles</strong>
            <p>
              La dernière génération complète COMPETENCES fournit les ECTS et, lorsqu'il existe, le grade. Un grade
              absent est calculé depuis les notes brutes PASS.
            </p>
          </div>
        </section>
        <section>
          <span>
            <Clock3 size={18} />
          </span>
          <div>
            <strong>Délai anti-consultation opportuniste</strong>
            <p>
              Ton profil devient visible dès l'activation, mais tu n'accèdes au classement qu'après{" "}
              {view.rules.wait_hours} heures. Tu peux retirer ta participation à tout moment.
            </p>
          </div>
        </section>
        <section>
          <span>
            <Scale size={18} />
          </span>
          <div>
            <strong>Égalités</strong>
            <p>{view.rules.ties}. Les deux métriques produisent deux classements indépendants.</p>
          </div>
        </section>
        <section>
          <span>
            <ShieldCheck size={18} />
          </span>
          <div>
            <strong>Classement nominatif</strong>
            <p>
              Le prénom et le nom officiels PASS, le rang, le score, la date de vérification et l'état de fraîcheur
              figurent dans chaque ligne. Le cursus et la promotion définissent le segment ; le campus sert uniquement
              de filtre.
            </p>
          </div>
        </section>
        <section className="rules-exclusions">
          <span>
            <EyeOff size={18} />
          </span>
          <div>
            <strong>Jamais comptabilisé</strong>
            <ul>
              {view.rules.excluded.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        </section>
      </div>
      <footer className="modal-actions">
        <button className="primary-button" type="button" onClick={onClose}>
          Compris
        </button>
      </footer>
    </Modal>
  );
}

function OfficialProfileFields({
  officialName,
  officialIdentityAt,
  campus,
  campusSource,
  detectedCampus,
  program,
  promotionYear,
  academicSource,
}: {
  officialName: string | null;
  officialIdentityAt: string | null;
  campus: Campus;
  campusSource: string;
  detectedCampus: Campus;
  program: string;
  promotionYear: number | null;
  academicSource: string;
}) {
  return (
    <div className="form-grid leaderboard-profile-fields">
      <div className="official-campus-field official-identity-field">
        <span className="field-label">Identité publique</span>
        <div className={officialName ? "official-campus-value" : "official-campus-value is-missing"}>
          {officialName ? <UserRoundCheck size={17} /> : <AlertTriangle size={17} />}
          <div>
            <strong>{officialName ?? "Non disponible"}</strong>
            <small>
              {officialName
                ? `Valeur officielle PASS · ${formatDate(officialIdentityAt, false)}`
                : "Une relecture du profil PASS est nécessaire"}
            </small>
          </div>
        </div>
        <small className="campus-contact-note">
          <Info size={13} /> Cette identité est figée et n'est jamais modifiable dans IMTégrale.
        </small>
      </div>
      <div className="two-columns form-grid">
        <div className="official-campus-field">
          <span className="field-label">Campus courant</span>
          <div className={campus === "unknown" ? "official-campus-value is-missing" : "official-campus-value"}>
            {campus === "unknown" ? <AlertTriangle size={17} /> : <CheckCircle2 size={17} />}
            <div>
              <strong>{CAMPUS_LABELS[campus]}</strong>
              <small>{campusSource === "admin" ? "Correction administrateur" : "Valeur officielle PASS"}</small>
            </div>
          </div>
          <small className="campus-contact-note">
            <Info size={13} /> Cette donnée n'est pas modifiable ici. Pour demander une correction, contacte
            l'administrateur.
          </small>
          {campusSource === "admin" && detectedCampus !== "unknown" && campus !== detectedCampus && (
            <small className="pass-detected">
              <Info size={13} /> PASS indique actuellement {CAMPUS_LABELS[detectedCampus]}
            </small>
          )}
        </div>
        <div className="official-campus-field">
          <span className="field-label">Cursus · promotion</span>
          <div
            className={
              !promotionYear || program === "unknown" ? "official-campus-value is-missing" : "official-campus-value"
            }
          >
            {!promotionYear || program === "unknown" ? <AlertTriangle size={17} /> : <CheckCircle2 size={17} />}
            <div>
              <strong>
                {promotionYear && program !== "unknown" ? `${program} ${promotionYear}` : "Non disponible"}
              </strong>
              <small>{academicSource === "admin" ? "Correction administrateur" : "Valeur officielle PASS"}</small>
            </div>
          </div>
          <small className="campus-contact-note">
            <Info size={13} /> Cette classification détermine automatiquement ton classement et n'est pas modifiable
            ici.
          </small>
        </div>
      </div>
    </div>
  );
}

function ParticipationPanel({
  view,
  visibilityAccepted,
  setVisibilityAccepted,
  waitAccepted,
  setWaitAccepted,
  onSubmit,
  pending,
}: {
  view: LeaderboardView;
  visibilityAccepted: boolean;
  setVisibilityAccepted: (value: boolean) => void;
  waitAccepted: boolean;
  setWaitAccepted: (value: boolean) => void;
  onSubmit: () => void;
  pending: boolean;
}) {
  const dataBlockers = view.eligibility.missing;
  const formReady = Boolean(view.profile.official_name && view.profile.campus !== "unknown" && view.profile.segment);
  const canSubmit = Boolean(formReady && visibilityAccepted && waitAccepted && dataBlockers.length === 0);

  return (
    <div className="leaderboard-optin-layout">
      <section className="leaderboard-explainer">
        <span className="section-kicker">Participation volontaire</span>
        <h2>Un classement vérifiable et assumé</h2>
        <p>
          La moyenne vient des notes brutes PASS. Le GPA privilégie le grade officiel COMPETENCES, avec calcul depuis
          PASS uniquement lorsqu'il est absent. Cursus et promotion définissent automatiquement le groupe comparable.
        </p>
        <div className="privacy-flow">
          <div>
            <span>
              <UserRoundCheck size={18} />
            </span>
            <div>
              <strong>1. Ton identité PASS est utilisée</strong>
              <p>Prénom, nom, campus, cursus et promotion ne sont pas modifiables ici.</p>
            </div>
          </div>
          <div>
            <span>
              <ShieldCheck size={18} />
            </span>
            <div>
              <strong>2. Tes coefficients sont officiels</strong>
              <p>
                Les ECTS viennent de COMPETENCES et suivent automatiquement la dernière génération complète
                synchronisée.
              </p>
            </div>
          </div>
          <div>
            <span>
              <Clock3 size={18} />
            </span>
            <div>
              <strong>3. Tu observes un délai avant de consulter</strong>
              <p>
                Tu es visible immédiatement et tu accèdes au classement après 48 heures. Tu peux te retirer puis revenir
                à tout moment ; chaque activation relance cette attente.
              </p>
            </div>
          </div>
        </div>
      </section>

      <form
        className="leaderboard-join-panel"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        <header>
          <div>
            <h2>Rejoindre le classement</h2>
            <p>Ton segment officiel est appliqué côté serveur.</p>
          </div>
          <span>
            <Trophy size={20} />
          </span>
        </header>
        <OfficialProfileFields
          officialName={view.profile.official_name}
          officialIdentityAt={view.profile.official_identity_at}
          campus={view.profile.campus}
          campusSource={view.profile.campus_source}
          detectedCampus={view.profile.detected_campus}
          program={view.profile.program}
          promotionYear={view.profile.promotion_year}
          academicSource={view.profile.academic_source}
        />
        {dataBlockers.length > 0 && (
          <div className="eligibility-blockers">
            <AlertTriangle size={18} />
            <div>
              <strong>
                Encore {dataBlockers.length} étape{dataBlockers.length > 1 ? "s" : ""}
              </strong>
              {dataBlockers.map((item) => (
                <span key={item}>{MISSING_LABELS[item]}</span>
              ))}
            </div>
          </div>
        )}
        <label className={`consent-check ${visibilityAccepted ? "checked" : ""}`}>
          <input
            type="checkbox"
            checked={visibilityAccepted}
            onChange={(event) => setVisibilityAccepted(event.target.checked)}
          />
          <i>
            <Check size={14} />
          </i>
          <span>
            <strong>J'accepte la publication de mon identité</strong>
            <small>
              Dès l'activation, mon prénom, mon nom officiels PASS et mes scores seront affichés aux participants déjà
              actifs.
            </small>
          </span>
        </label>
        <label className={`consent-check ${waitAccepted ? "checked" : ""}`}>
          <input type="checkbox" checked={waitAccepted} onChange={(event) => setWaitAccepted(event.target.checked)} />
          <i>
            <Check size={14} />
          </i>
          <span>
            <strong>J'ai compris le délai de consultation</strong>
            <small>
              Pendant 48 heures, mon profil est publié mais aucun classement, rang ni nombre de participants ne m'est
              révélé. Je peux me retirer ou effacer ces données à tout moment.
            </small>
          </span>
        </label>
        <button className="primary-button leaderboard-join-button" type="submit" disabled={!canSubmit || pending}>
          {pending ? <span className="spinner" /> : <Trophy size={18} />} Activer ma participation
        </button>
      </form>
    </div>
  );
}

function PendingView({ view, onManage }: { view: LeaderboardView; onManage: () => void }) {
  const countdown = useCountdown(view.profile.ranking_visible_at);
  return (
    <>
      <section className="leaderboard-wait-banner">
        <div className="wait-icon">
          <Clock3 size={24} />
        </div>
        <div>
          <span>Accès au classement dans</span>
          <strong>{countdown.label}</strong>
          <small>Prévu le {formatDate(view.profile.ranking_visible_at)}</small>
        </div>
        <div className="wait-privacy">
          <EyeOff size={18} />
          <span>
            Aucun classement, rang ou nombre de participants ne t'est révélé avant la fin du délai. Tu peux retirer ta
            participation ou effacer ses données à tout moment.
          </span>
        </div>
      </section>
      <section className="leaderboard-pending-panel">
        <div>
          <span className="status-dot" />
          <span>Participation publiée</span>
        </div>
        <h2>Ton profil contribue déjà au classement</h2>
        <p>
          Les participants actifs voient <strong>{view.profile.official_name}</strong> et tes scores calculés depuis
          PASS. Tu découvriras le classement à la fin du délai affiché ci-dessus.
        </p>
        <dl>
          <div>
            <dt>Identité PASS</dt>
            <dd>{view.profile.official_name}</dd>
          </div>
          <div>
            <dt>Campus PASS</dt>
            <dd>{CAMPUS_LABELS[view.profile.campus]}</dd>
          </div>
          <div>
            <dt>Segment officiel</dt>
            <dd>
              {view.profile.program} {view.profile.promotion_year}
            </dd>
          </div>
        </dl>
        <footer>
          <button className="text-button" type="button" onClick={onManage}>
            Confidentialité
          </button>
        </footer>
      </section>
    </>
  );
}

function ActiveLeaderboard({
  view,
  metric,
  setMetric,
  campus,
  setCampus,
  onRules,
  onManage,
}: {
  view: LeaderboardView;
  metric: LeaderboardMetric;
  setMetric: (value: LeaderboardMetric) => void;
  campus: string;
  setCampus: (value: string) => void;
  onRules: () => void;
  onManage: () => void;
}) {
  const board = view.board;
  return (
    <>
      <section className="leaderboard-toolbar">
        <div className="leaderboard-metric-tabs" role="group" aria-label="Métrique du classement">
          <button
            className={metric === "gpa" ? "active" : ""}
            type="button"
            onClick={() => setMetric("gpa")}
            aria-pressed={metric === "gpa"}
          >
            <Gauge size={17} /> GPA
          </button>
          <button
            className={metric === "average" ? "active" : ""}
            type="button"
            onClick={() => setMetric("average")}
            aria-pressed={metric === "average"}
          >
            <Scale size={17} /> Moyenne générale
          </button>
        </div>
        <div className="leaderboard-filters">
          <Filter size={17} />
          <label>
            <span className="sr-only">Campus</span>
            <select value={campus} onChange={(event) => setCampus(event.target.value)}>
              <option value="all">Tous les campus</option>
              <option value="rennes">Rennes</option>
              <option value="brest">Brest</option>
              <option value="nantes">Nantes</option>
              <option value="other">Autres campus</option>
            </select>
          </label>
        </div>
      </section>

      <section className="leaderboard-board">
        <header>
          <div>
            <span className="section-kicker">
              {view.profile.program} {view.profile.promotion_year} · {metric === "gpa" ? "GPA" : "moyenne générale"}
            </span>
            <h2>
              {board?.participant_count ?? 0} participant{board?.participant_count === 1 ? "" : "s"}
            </h2>
            <p>Notes PASS pondérées par les ECTS officiels COMPETENCES, avec fraîcheur visible pour chaque profil.</p>
          </div>
          <div className="board-actions">
            <button className="icon-button" type="button" onClick={onRules} aria-label="Voir les règles" title="Règles">
              <Info size={18} />
            </button>
            <button
              className="icon-button"
              type="button"
              onClick={onManage}
              aria-label="Gérer ma participation"
              title="Confidentialité"
            >
              <ShieldCheck size={18} />
            </button>
          </div>
        </header>
        {board?.entries.length ? (
          <div className="leaderboard-table-wrap">
            <table className="leaderboard-table">
              <thead>
                <tr>
                  <th>Rang</th>
                  <th>Identité PASS</th>
                  <th>{metric === "gpa" ? "GPA" : "Moyenne"}</th>
                </tr>
              </thead>
              <tbody>
                {board.entries.map((entry, index) => (
                  <tr
                    key={`${entry.rank ?? "stale"}-${entry.official_name}-${index}`}
                    className={`${entry.is_self ? "is-self " : ""}${entry.freshness === "stale" ? "is-stale" : ""}`.trim()}
                  >
                    <td>
                      <span
                        className={`rank-mark rank-${entry.rank !== null && entry.rank <= 3 ? entry.rank : "other"}`}
                        aria-label={entry.rank === null ? "Rang suspendu jusqu'à actualisation" : `Rang ${entry.rank}`}
                      >
                        {entry.rank ?? "—"}
                      </span>
                    </td>
                    <td>
                      <strong>{entry.official_name}</strong>
                      {entry.is_self && <span className="self-pill">Toi</span>}
                      <small className="leaderboard-verification">
                        Vérifié le {entry.verified_at ? formatDate(entry.verified_at, false) : "—"}
                        <span className={`freshness-state freshness-${entry.freshness}`}>
                          <i />
                          {FRESHNESS_LABELS[entry.freshness]}
                        </span>
                      </small>
                    </td>
                    <td>
                      <strong>{scoreLabel(metric, entry.score)}</strong>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon={<Users size={20} />}
            title="Aucun participant pour ces filtres"
            detail="Le classement accepte aussi un seul participant. Modifie simplement les filtres pour retrouver les autres profils actifs."
          />
        )}
        <footer>
          <span>
            <ShieldCheck size={14} /> Un profil à actualiser reste visible sans rang actif
          </span>
          <span>Calculé {formatDate(board?.calculated_at)}</span>
        </footer>
      </section>
    </>
  );
}

function PrivacyModal({
  view,
  open,
  onClose,
  onWithdraw,
  onDelete,
  pending,
}: {
  view: LeaderboardView;
  open: boolean;
  onClose: () => void;
  onWithdraw: () => void;
  onDelete: () => void;
  pending: boolean;
}) {
  const [confirmation, setConfirmation] = useState("");
  useEffect(() => {
    if (!open) setConfirmation("");
  }, [open]);
  return (
    <Modal
      open={open}
      title="Confidentialité et participation"
      description="Ton profil disparaît immédiatement dès que tu retires ta participation."
      onClose={onClose}
      size="large"
    >
      <div className="privacy-management">
        {view.can_withdraw && (
          <section>
            <span>
              <EyeOff size={20} />
            </span>
            <div>
              <h3>Me retirer du classement</h3>
              <p>
                Ton profil disparaît immédiatement pour tout le monde. Tu peux revenir quand tu le souhaites, mais
                chaque nouvelle activation relance les 48 heures avant consultation.
              </p>
              <button className="danger-button armed" type="button" onClick={onWithdraw} disabled={pending}>
                {pending ? <span className="spinner" /> : <EyeOff size={16} />} Me retirer maintenant
              </button>
            </div>
          </section>
        )}
        {view.can_delete_data && (
          <section>
            <span>
              <Trash2 size={20} />
            </span>
            <div>
              <h3>Effacer mes données de classement</h3>
              <p>
                La participation et le consentement sont effacés. Ton identité officielle PASS, tes notes privées et ton
                compte IMTégrale sont conservés.
              </p>
              <label>
                Écris <strong>SUPPRIMER</strong> pour confirmer
                <input
                  value={confirmation}
                  onChange={(event) => setConfirmation(event.target.value)}
                  autoComplete="off"
                />
              </label>
              <button
                className="danger-button armed"
                type="button"
                onClick={onDelete}
                disabled={pending || confirmation !== "SUPPRIMER"}
              >
                <Trash2 size={16} /> Effacer ces données
              </button>
            </div>
          </section>
        )}
      </div>
      <footer className="modal-actions">
        <button className="secondary-button" type="button" onClick={onClose}>
          Fermer
        </button>
      </footer>
    </Modal>
  );
}

export function LeaderboardPage() {
  const [metric, setMetric] = useState<LeaderboardMetric>("gpa");
  const [campusFilter, setCampusFilter] = useState("all");
  const leaderboard = useLeaderboard(metric, campusFilter, "");
  const session = useSession();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [rulesOpen, setRulesOpen] = useState(false);
  const [privacyOpen, setPrivacyOpen] = useState(false);
  const [visibilityAccepted, setVisibilityAccepted] = useState(false);
  const [waitAccepted, setWaitAccepted] = useState(false);
  const accountId = session.data?.account?.id ?? "anonymous";
  const rootKey = queryKeys.leaderboardRoot(accountId);

  const applyMutationView = (next: LeaderboardView) => {
    queryClient.setQueriesData<LeaderboardView>({ queryKey: rootKey }, (current) =>
      current ? { ...next, board: null } : next,
    );
    queryClient.removeQueries({ queryKey: rootKey, type: "inactive" });
    queryClient.invalidateQueries({ queryKey: rootKey });
  };

  const join = useMutation({
    mutationFn: () =>
      apiData(
        leaderboardActivateLeaderboard({
          body: {
            consent_version: leaderboard.data?.consent_version ?? "",
            acknowledge_visibility: true,
            acknowledge_wait: true,
          },
          throwOnError: throwOnApiError,
        }),
      ),
    onSuccess: (next) => {
      applyMutationView(next);
      showToast("Participation activée. Ton profil est publié et le délai de 48 heures commence.");
    },
    onError: (error) => showToast(error.message, "error"),
  });
  const withdraw = useMutation({
    mutationFn: () => apiData(leaderboardWithdrawFromLeaderboard({ throwOnError: throwOnApiError })),
    onSuccess: (next) => {
      applyMutationView(next);
      setPrivacyOpen(false);
      showToast("Ton profil a été retiré immédiatement");
    },
    onError: (error) => showToast(error.message, "error"),
  });
  const erase = useMutation({
    mutationFn: () => apiData(leaderboardEraseLeaderboardData({ throwOnError: throwOnApiError })),
    onSuccess: (next) => {
      applyMutationView(next);
      setPrivacyOpen(false);
      showToast("Données de classement effacées");
    },
    onError: (error) => showToast(error.message, "error"),
  });

  const view = leaderboard.data;
  const privacyPending = withdraw.isPending || erase.isPending;
  const stateDescription = useMemo(() => {
    if (!view) return "";
    if (view.state === "active") return "Deux classements distincts, calculés uniquement depuis les données PASS.";
    if (view.state === "pending") return "Ton profil est publié ; ton accès au classement s'ouvrira après 48 heures.";
    if (view.state === "suspended")
      return "L'affichage du profil est suspendu pendant une vérification administrative.";
    return "Une participation volontaire et nominative, avec 48 heures d'attente avant toute consultation.";
  }, [view]);

  if (leaderboard.isPending)
    return (
      <div className="page-stack">
        <div className="skeleton leaderboard-hero-skeleton" />
        <div className="skeleton leaderboard-panel-skeleton" />
      </div>
    );
  if (leaderboard.isError || !view)
    return (
      <div className="error-panel">
        <AlertTriangle size={22} />
        <div>
          <h2>Impossible de charger le classement</h2>
          <p>{leaderboard.error?.message ?? "Réessaie dans quelques instants."}</p>
        </div>
        <button className="secondary-button" type="button" onClick={() => leaderboard.refetch()}>
          Réessayer
        </button>
      </div>
    );

  return (
    <div className="page-stack leaderboard-page">
      <section className="leaderboard-heading-band">
        <div>
          <span className="leaderboard-heading-icon">
            <Trophy size={23} />
          </span>
          <div>
            <span className="section-kicker">Classement IMTégrale</span>
            <h2>Comparer ce qui est réellement comparable</h2>
            <p>{stateDescription}</p>
          </div>
        </div>
        <button className="secondary-button" type="button" onClick={() => setRulesOpen(true)}>
          <Info size={16} /> Règles et calculs
        </button>
      </section>

      {view.state === "not_joined" && (
        <ParticipationPanel
          view={view}
          visibilityAccepted={visibilityAccepted}
          setVisibilityAccepted={setVisibilityAccepted}
          waitAccepted={waitAccepted}
          setWaitAccepted={setWaitAccepted}
          onSubmit={() => join.mutate()}
          pending={join.isPending}
        />
      )}
      {view.state === "pending" && <PendingView view={view} onManage={() => setPrivacyOpen(true)} />}
      {view.state === "active" && (
        <ActiveLeaderboard
          view={view}
          metric={metric}
          setMetric={setMetric}
          campus={campusFilter}
          setCampus={setCampusFilter}
          onRules={() => setRulesOpen(true)}
          onManage={() => setPrivacyOpen(true)}
        />
      )}
      {view.state === "suspended" && (
        <section className="leaderboard-suspended">
          <AlertTriangle size={22} />
          <div>
            <span className="section-kicker">Participation suspendue</span>
            <h2>Le profil n'est actuellement visible par personne</h2>
            <p>
              Une vérification administrative est en cours. Tes notes privées restent intactes et tu peux toujours
              effacer les données propres au classement.
            </p>
          </div>
          <button className="secondary-button" type="button" onClick={() => setPrivacyOpen(true)}>
            <ShieldCheck size={16} /> Confidentialité
          </button>
        </section>
      )}

      <RulesModal view={view} open={rulesOpen} onClose={() => setRulesOpen(false)} />
      <PrivacyModal
        view={view}
        open={privacyOpen}
        onClose={() => setPrivacyOpen(false)}
        onWithdraw={() => withdraw.mutate()}
        onDelete={() => erase.mutate()}
        pending={privacyPending}
      />
    </div>
  );
}
