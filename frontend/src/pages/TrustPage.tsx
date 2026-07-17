import {
  ArrowRight,
  ExternalLink,
  Database,
  Fingerprint,
  KeyRound,
  LockKeyhole,
  RefreshCw,
  Server,
  ShieldAlert,
  ShieldCheck,
  UserRoundCheck,
} from "lucide-react";
import { Link } from "react-router-dom";
import { BRAND } from "../brand";
import { GitHubMark } from "../components/GitHubMark";

const guarantees = [
  { icon: ShieldCheck, title: "Transport HTTPS", text: "Le navigateur vérifie un certificat public valide. Les cookies de session sont Secure, HttpOnly et SameSite=Strict." },
  { icon: LockKeyhole, title: "Secrets chiffrés", text: "Le mot de passe IMT et le token Telegram sont chiffrés en AES-256-GCM avec une clé conservée hors de PostgreSQL." },
  { icon: Database, title: "Données cloisonnées", text: "Chaque note, UE, session, token et événement est rattaché à un compte et filtré côté serveur." },
  { icon: RefreshCw, title: "PASS protégé", text: "Les synchronisations sont consenties, sérialisées, limitées par heure et par jour, puis suspendues automatiquement si PASS devient instable." },
];

export function TrustPage() {
  return (
    <div className="trust-page">
      <section className="public-title-band">
        <span className="public-title-icon"><ShieldCheck size={26} /></span>
        <div><span className="section-kicker">Confiance et transparence</span><h1>Pourquoi puis-je me connecter ?</h1><p>Voici ce que reçoit IMTégrale, pourquoi le service en a besoin et où s'arrête sa promesse de sécurité.</p></div>
      </section>

      <section className="trust-answer">
        <div><span className="section-kicker">Réponse courte</span><h2>PASS ne propose pas de délégation OAuth utilisable ici.</h2><p>Pour vérifier ton accès et importer tes notes, IMTégrale doit donc transmettre tes identifiants à la page d'authentification IMT depuis son serveur. Il ne s'agit ni d'une connexion officielle « avec IMT », ni d'un partenariat avec l'école.</p></div>
        <span className="trust-answer-mark"><UserRoundCheck size={29} /></span>
      </section>

      <section className="trust-flow" aria-label="Chemin d'une première connexion">
        <header><span className="section-kicker">Première connexion</span><h2>Un trajet court, puis des choix explicites</h2></header>
        <div className="trust-flow-line">
          <article><span><Fingerprint size={20} /></span><strong>Ton navigateur</strong><small>Envoie l'identifiant en HTTPS</small></article>
          <i><ArrowRight size={18} /></i>
          <article><span><Server size={20} /></span><strong>IMTégrale</strong><small>Applique quotas et protection anti-abus</small></article>
          <i><ArrowRight size={18} /></i>
          <article><span><KeyRound size={20} /></span><strong>Services IMT</strong><small>CAS vérifie, PASS et COMPETENCES répondent</small></article>
          <i><ArrowRight size={18} /></i>
          <article><span><Database size={20} /></span><strong>Espace privé</strong><small>Importe profil, notes, UE et ECTS</small></article>
        </div>
      </section>

      <section className="trust-guarantees">
        {guarantees.map((item) => <article key={item.title}><span><item.icon size={20} /></span><div><h2>{item.title}</h2><p>{item.text}</p></div></article>)}
      </section>

      <section className="trust-split">
        <article>
          <span className="section-kicker">Après l'inscription</span>
          <h2>Se reconnecter ne déclenche pas de scraping</h2>
          <p>Une connexion IMT ultérieure vérifie uniquement le compte. Une synchronisation manuelle reste une action séparée ; l'automatisation est désactivée tant que tu ne l'as pas autorisée.</p>
          <div className="trust-methods"><span><Fingerprint size={17} /> Passkey recommandée</span><span><KeyRound size={17} /> Token personnel révocable</span></div>
        </article>
        <article className="trust-limit">
          <span><ShieldAlert size={21} /></span>
          <div><span className="section-kicker">Limite importante</span><h2>Le chiffrement n'annule pas la confiance serveur</h2><p>Le worker doit pouvoir relire le mot de passe pour synchroniser PASS. Une compromission complète du serveur et de sa clé maître permettrait donc de le déchiffrer. Aucune interface sérieuse ne doit présenter cela comme l'équivalent d'OAuth.</p></div>
        </article>
      </section>

      <section className="trust-source-band">
        <span><GitHubMark size={25} /></span>
        <div><span className="section-kicker">Code source public</span><h2>Les protections peuvent être examinées, discutées et améliorées.</h2><p>Le dépôt expose l'architecture, les migrations et les contrôles de sécurité. Cela rend le projet auditable, sans prétendre qu'un dépôt public prouve à lui seul la version réellement exécutée sur le serveur.</p></div>
        <a className="secondary-button" href={BRAND.sourceCodeUrl} target="_blank" rel="noreferrer">Ouvrir GitHub <ExternalLink size={16} /></a>
      </section>

      <section className="public-cta-band">
        <div><h2>Voir le fonctionnement avant de décider</h2><p>La démo utilise uniquement un profil fictif et montre aussi les fonctions facultatives.</p></div>
        <div><Link className="secondary-button" to="/demo">Explorer la démo <ArrowRight size={16} /></Link><Link className="primary-button" to="/">Revenir à la connexion</Link></div>
      </section>
    </div>
  );
}
