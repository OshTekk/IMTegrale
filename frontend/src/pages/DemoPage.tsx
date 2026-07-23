import {
  BellRing,
  BookOpenCheck,
  CalendarDays,
  Calculator,
  ChevronLeft,
  ChevronRight,
  CircleUserRound,
  Clock3,
  Database,
  FileDown,
  Fingerprint,
  FlaskConical,
  Gauge,
  KeyRound,
  LockKeyhole,
  Pause,
  Play,
  Server,
  Share2,
  ShieldCheck,
  TableProperties,
  Trophy,
  type LucideIcon,
} from "lucide-react";
import { type CSSProperties, useEffect, useState } from "react";
import { Link } from "react-router-dom";

type DemoNode = "browser" | "edge" | "pass" | "vault" | "dashboard" | "optional";
interface DemoStep {
  title: string;
  short: string;
  detail: string;
  proof: string;
  node: DemoNode;
  icon: LucideIcon;
  optional?: boolean;
}

const steps: DemoStep[] = [
  {
    title: "Tu demandes ton inscription",
    short: "Inscription",
    detail:
      "Le navigateur envoie une fois l'identifiant CAS et le mot de passe IMT sur une connexion HTTPS valide. Les protections par compte et par connexion cliente s'appliquent avant tout appel à PASS.",
    proof: "Le mot de passe sert uniquement à cette authentification et n'est jamais écrit en base.",
    node: "browser",
    icon: CircleUserRound,
  },
  {
    title: "La frontière réseau contrôle la requête",
    short: "HTTPS",
    detail:
      "Tailscale Funnel ne publie que le listener web. Nginx limite le débit, sépare Internet de l'administration privée et rejoint le backend avec un certificat client mTLS.",
    proof: "Les routes /admin répondent 404 depuis Internet.",
    node: "edge",
    icon: ShieldCheck,
  },
  {
    title: "CAS vérifie le compte",
    short: "Vérification",
    detail:
      "Le serveur suit uniquement les origines CAS, PASS et Hub IMT autorisées, refuse les redirections externes et borne la durée comme la taille des réponses.",
    proof:
      "Un échec déclenche un délai progressif ; une panne d'un service IMT n'est pas comptée comme une erreur utilisateur.",
    node: "pass",
    icon: KeyRound,
  },
  {
    title: "La session technique est isolée",
    short: "Session privée",
    detail:
      "Après l'authentification, le mot de passe est détruit. Seuls les cookies nécessaires à PASS et au Hub sont filtrés, chiffrés en AES-256-GCM et conservés au maximum 30 jours.",
    proof:
      "La base ne contient ni mot de passe IMT ni cookie lisible ; la session peut être révoquée et ne survit pas au-delà de sa limite locale.",
    node: "vault",
    icon: LockKeyhole,
  },
  {
    title: "Les données académiques sont importées",
    short: "Import IMT",
    detail:
      "PASS fournit l'identité, le campus, le cursus, la promotion et les évaluations. COMPETENCES complète les intitulés, semestres, grades et crédits ECTS officiels de chaque UE.",
    proof: "Les données officielles sont identifiées dans l'interface et verrouillées côté API.",
    node: "vault",
    icon: Database,
  },
  {
    title: "Moyenne et GPA sont calculés",
    short: "Calculs",
    detail:
      "Les notes construisent une moyenne par UE. La moyenne générale et le GPA sont ensuite pondérés par les crédits ECTS de chaque UE ; un rattrapage validé correspond au grade E et à 2,5.",
    proof:
      "Deux résultats distincts, un même poids académique : les ECTS. Toute nouvelle donnée importée est poussée vers l'interface en direct.",
    node: "dashboard",
    icon: Calculator,
  },
  {
    title: "Chaque semestre reste lisible",
    short: "Résultats",
    detail:
      "L'espace Résultats réunit les UE, les évaluations et les nouveautés avec leurs coefficients, grades et crédits. Les filtres S5 à S10 permettent de retrouver les résultats et le GPA de chaque semestre.",
    proof: "Une donnée PASS ou COMPETENCES ne peut être ni ajoutée, ni modifiée, ni masquée par l'étudiant.",
    node: "dashboard",
    icon: TableProperties,
  },
  {
    title: "Les actualisations restent sous contrôle",
    short: "Synchronisation",
    detail:
      "La reconnexion ne synchronise rien. Le manuel est limité et l'automatique est désactivé par défaut, limité aux heures ouvrées et au minimum à deux heures d'intervalle.",
    proof:
      "La session PASS est réutilisée tant qu'elle reste valide. À son expiration, toute automatisation se met en pause jusqu'à une reconnexion volontaire.",
    node: "dashboard",
    icon: Clock3,
  },
  {
    title: "Tu choisis tes moyens d'accès",
    short: "Accès & partage",
    detail:
      "Une passkey ouvre le compte sans mot de passe ni appel à PASS. Un token personnel peut servir d'alternative, tandis qu'un token de partage reçoit uniquement le rôle et l'expiration choisis.",
    proof:
      "Le secret d'un token n'est affiché qu'une fois, n'est conservé que sous forme d'empreinte et reste révocable.",
    node: "optional",
    icon: Share2,
    optional: true,
  },
  {
    title: "Telegram est facultatif",
    short: "Notification",
    detail:
      "Après consentement, une nouvelle note peut produire une notification. Le token est chiffré, les redirections sont refusées et un bouton permet de tester la configuration.",
    proof: "Désactiver Telegram n'affecte ni les notes ni les calculs.",
    node: "optional",
    icon: BellRing,
    optional: true,
  },
  {
    title: "Les hypothèses restent séparées du réel",
    short: "Simulations",
    detail:
      "Chaque étudiant peut conserver cinq simulations GPA et cinq simulations de notes. Il peut importer son état académique, modifier la copie, ajouter de futures UE et comparer des scénarios avec autosauvegarde.",
    proof: "Aucune hypothèse n'altère une donnée officielle, une notification ou le classement.",
    node: "optional",
    icon: FlaskConical,
    optional: true,
  },
  {
    title: "Les calendriers rejoignent le même espace",
    short: "Agenda",
    detail:
      "Un lien iCalendar INPASS alimente l'agenda personnel avec une actualisation horaire. Les étudiants FIP peuvent aussi consulter les calendriers de formation 2027, 2028 et 2029.",
    proof: "Le lien INPASS est chiffré, jamais réaffiché et absent des accès partagés.",
    node: "optional",
    icon: CalendarDays,
    optional: true,
  },
  {
    title: "Un relevé personnel peut être généré",
    short: "Relevé PDF",
    detail:
      "Le relevé rassemble identité, UE, notes, grades, ECTS et calculs, avec un filtre par semestre et une annexe facultative. Il indique clairement qu'il s'agit d'un document informatif non officiel.",
    proof:
      "Le PDF est généré à la demande, n'est jamais stocké sur le serveur et renvoie vers les sources et le code du calcul.",
    node: "optional",
    icon: FileDown,
    optional: true,
  },
  {
    title: "Le classement exige un second consentement",
    short: "Leaderboard",
    detail:
      "Après activation, l'identité PASS et les deux scores deviennent immédiatement visibles aux participants actifs. Le nouvel inscrit attend 48 heures avant d'accéder lui-même aux classements GPA et moyenne.",
    proof:
      "Le retrait ou l'effacement reste immédiat. Il est possible de revenir à tout moment, avec une nouvelle attente de 48 heures avant consultation.",
    node: "optional",
    icon: Trophy,
    optional: true,
  },
];

const nodes: Array<{ id: DemoNode; label: string; icon: LucideIcon }> = [
  { id: "browser", label: "Navigateur", icon: Fingerprint },
  { id: "edge", label: "Frontière HTTPS", icon: ShieldCheck },
  { id: "pass", label: "Services IMT", icon: Server },
  { id: "vault", label: "Coffre privé", icon: Database },
  { id: "dashboard", label: "Calculs", icon: Gauge },
  { id: "optional", label: "Options", icon: BookOpenCheck },
];

export function DemoPage() {
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const active = steps[index]!;
  const activeNodeIndex = nodes.findIndex((node) => node.id === active.node);
  const nodeProgress = (activeNodeIndex / (nodes.length - 1)) * 100;
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => setIndex((value) => (value + 1) % steps.length), 3200);
    return () => window.clearInterval(timer);
  }, [playing]);
  const select = (next: number) => {
    setIndex(next);
    setPlaying(false);
  };

  return (
    <div className="demo-page">
      <section className="public-title-band demo-title-band">
        <span className="public-title-icon">
          <Play size={26} />
        </span>
        <div>
          <span className="section-kicker">Données entièrement fictives</span>
          <h1>Explorer tout le parcours IMTégrale</h1>
          <p>
            Sélectionne une étape pour voir ce qui circule, ce qui reste privé et comment chaque fonction se comporte.
          </p>
        </div>
        <span className="demo-fake-badge">
          <Database size={15} /> Profil de démonstration
        </span>
      </section>

      <section className="demo-lab">
        <div
          className="demo-map"
          style={
            {
              "--demo-progress": `${nodeProgress}%`,
              "--demo-position": `${8.333 + activeNodeIndex * 16.667}%`,
            } as CSSProperties
          }
        >
          <div className="demo-track">
            <span />
          </div>
          {nodes.map((node) => (
            <div key={node.id} className={`demo-node ${node.id === active.node ? "active" : ""}`}>
              <span>
                <node.icon size={20} />
              </span>
              <strong>{node.label}</strong>
            </div>
          ))}
          <div className={`demo-packet node-${active.node}`}>
            <active.icon size={14} />
          </div>
        </div>

        <div className="demo-workspace">
          <nav className="demo-steps" aria-label="Étapes de la démonstration">
            {steps.map((step, stepIndex) => (
              <button
                key={step.title}
                type="button"
                className={stepIndex === index ? "active" : ""}
                onClick={() => select(stepIndex)}
                aria-current={stepIndex === index ? "step" : undefined}
                aria-label={`Étape ${stepIndex + 1} : ${step.short}`}
                title={step.title}
              >
                <span>{String(stepIndex + 1).padStart(2, "0")}</span>
                <step.icon size={16} />
                <strong>{step.short}</strong>
                {step.optional && <small>Option</small>}
              </button>
            ))}
          </nav>
          <article className="demo-detail" aria-live="polite">
            <header>
              <span>
                <active.icon size={23} />
              </span>
              <div>
                <small>
                  Étape {index + 1} sur {steps.length}
                  {active.optional ? " · Facultative" : ""}
                </small>
                <h2>{active.title}</h2>
              </div>
            </header>
            <p>{active.detail}</p>
            <div className="demo-proof">
              <ShieldCheck size={18} />
              <span>
                <strong>Garantie visible</strong>
                {active.proof}
              </span>
            </div>
            <footer>
              <button
                className="icon-button"
                type="button"
                onClick={() => select(Math.max(0, index - 1))}
                disabled={index === 0}
                aria-label="Étape précédente"
              >
                <ChevronLeft size={19} />
              </button>
              <button
                className="secondary-button demo-play"
                type="button"
                onClick={() => setPlaying((value) => !value)}
              >
                {playing ? <Pause size={17} /> : <Play size={17} />}
                {playing ? "Mettre en pause" : "Lire le parcours"}
              </button>
              <button
                className="icon-button"
                type="button"
                onClick={() => select(Math.min(steps.length - 1, index + 1))}
                disabled={index === steps.length - 1}
                aria-label="Étape suivante"
              >
                <ChevronRight size={19} />
              </button>
            </footer>
          </article>
        </div>
      </section>

      <section className="demo-summary-band">
        <div>
          <LockKeyhole size={20} />
          <span>
            <strong>Privé par défaut</strong>Notes, identité, agenda, relevés et simulations restent liés au compte.
          </span>
        </div>
        <div>
          <ShieldCheck size={20} />
          <span>
            <strong>Choix séparés</strong>Automatisation, Telegram, partage et classement s'activent indépendamment.
          </span>
        </div>
        <Link className="primary-button" to="/">
          Accéder à la connexion
        </Link>
      </section>
    </div>
  );
}
