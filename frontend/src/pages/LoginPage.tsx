import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CircleHelp, ExternalLink, Eye, EyeOff, Fingerprint, KeyRound, LockKeyhole, PlayCircle, School, ShieldCheck } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { BRAND } from "../brand";
import { BrandMark, Logo } from "../components/Logo";
import { GitHubMark } from "../components/GitHubMark";
import { SourceNotice } from "../components/SourceNotice";
import { ThemeToggle } from "../components/ThemeToggle";
import { api } from "../lib/api";
import { authenticateWithPasskey, passkeysSupported } from "../lib/passkeys";
import { replaceSessionState } from "../lib/queries";
import { broadcastSessionChange } from "../lib/sessionSync";
import type { Session } from "../types";

type LoginMode = "passkey" | "imt" | "token";

export function LoginPage() {
  const [mode, setMode] = useState<LoginMode>("imt");
  const [showPassword, setShowPassword] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");
  const queryClient = useQueryClient();
  const login = useMutation({
    mutationFn: async () => {
      if (mode === "passkey") return authenticateWithPasskey();
      if (mode === "imt") {
        return api<Session>("/api/v1/auth/login/imt", {
          method: "POST",
          body: JSON.stringify({ username, password }),
        });
      }
      return api<Session>("/api/v1/auth/login/token", {
        method: "POST",
        body: JSON.stringify({ token }),
      });
    },
    onSuccess: (session) => {
      replaceSessionState(queryClient, session);
      broadcastSessionChange();
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    login.mutate();
  };
  const selectMode = (next: LoginMode) => {
    setMode(next);
    login.reset();
  };

  return (
    <main className="login-page">
      <header className="login-topbar"><Logo /><ThemeToggle /></header>
      <div className="login-layout">
        <section className="login-context" aria-label={`Présentation d’${BRAND.name}`}>
          <div className="context-icon"><BrandMark size={52} /></div>
          <span className="login-product-label">{BRAND.descriptor}</span>
          <h1>{BRAND.tagline}</h1>
          <p>Notes PASS, moyenne pondérée par ECTS et GPA réunis dans un espace personnel sécurisé.</p>
          <div className="security-points">
            <span><ShieldCheck size={18} /> Données séparées par compte</span>
            <span><LockKeyhole size={18} /> Secrets chiffrés sur le serveur</span>
          </div>
          <SourceNotice />
          <div className="login-public-links">
            <Link to="/confiance"><CircleHelp size={17} /><span><strong>Pourquoi puis-je me connecter ?</strong><small>Comprendre les données, le chiffrement et ses limites</small></span></Link>
            <Link to="/demo"><PlayCircle size={17} /><span><strong>Voir la démo</strong><small>Suivre un parcours fictif, étape par étape</small></span></Link>
            <a className="login-source-link" href={BRAND.sourceCodeUrl} target="_blank" rel="noreferrer"><GitHubMark size={17} /><span><strong>Code source public</strong><small>Examiner l'architecture, les protections et la documentation</small></span><ExternalLink size={13} /></a>
          </div>
          <p className="login-paff-credit">Avec un clin d'œil à <a href={BRAND.paffUrl} target="_blank" rel="noreferrer">PAFF de Lucien Hervé <ExternalLink size={12} /></a>, projet étudiant antérieur.</p>
        </section>

        <section className="login-panel">
          <div className="login-heading"><span>{mode === "imt" ? "Première connexion ou retour" : "Compte déjà créé"}</span><h2>{mode === "imt" ? "Connexion avec ton compte IMT" : mode === "passkey" ? "Connexion par passkey" : "Connexion par token"}</h2></div>
          <div className="segmented login-methods" role="tablist" aria-label="Méthode de connexion">
            <button className={mode === "imt" ? "active" : ""} onClick={() => selectMode("imt")} type="button" role="tab" aria-selected={mode === "imt"}><School size={17} /> Compte IMT</button>
            {passkeysSupported() && <button className={mode === "passkey" ? "active" : ""} onClick={() => selectMode("passkey")} type="button" role="tab" aria-selected={mode === "passkey"}><Fingerprint size={17} /> Passkey</button>}
            <button className={mode === "token" ? "active" : ""} onClick={() => selectMode("token")} type="button" role="tab" aria-selected={mode === "token"}><KeyRound size={17} /> Token</button>
          </div>
          <div className={`login-method-note ${mode}`}><span>{mode === "imt" ? <School size={18} /> : mode === "passkey" ? <Fingerprint size={18} /> : <KeyRound size={18} />}</span><p>{mode === "imt" ? <><strong>Nouveau sur IMTégrale ? Commence ici.</strong> Ton espace est créé après vérification par l'IMT. Si ton compte existe déjà, aucune synchronisation de notes n'est lancée.</> : mode === "passkey" ? <><strong>Accès rapide recommandé après l'inscription.</strong> Utilise la clé enregistrée sur cet appareil, sans contacter PASS.</> : <><strong>Accès personnel ou partagé.</strong> Utilise un token généré depuis un compte IMTégrale existant.</>}</p></div>

          <form onSubmit={submit} className="login-form">
            {mode === "passkey" && <div className="passkey-login-choice"><span><Fingerprint size={28} /></span><div><strong>Connexion sans mot de passe</strong><p>Utilise Face ID, Touch ID, Windows Hello ou la sécurité de ton appareil.</p></div></div>}
            {mode === "imt" && <>
              <label>Identifiant CAS / IMT Atlantique<input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} placeholder="Votre identifiant de connexion" required autoFocus /></label>
              <label>Mot de passe IMT<span className="password-field"><input type={showPassword ? "text" : "password"} autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required /><button type="button" className="field-icon" onClick={() => setShowPassword((value) => !value)} aria-label={showPassword ? "Masquer le mot de passe" : "Afficher le mot de passe"}>{showPassword ? <EyeOff size={18} /> : <Eye size={18} />}</button></span></label>
              <p className="field-note">La première connexion importe ton espace PASS. Les suivantes vérifient uniquement ton accès IMT ; la synchronisation reste une action distincte.</p>
            </>}
            {mode === "token" && <>
              <label>Token d'accès<input autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} placeholder="bn1_…" required autoFocus /></label>
              <p className="field-note">Les droits et l'expiration dépendent du token. Il peut être révoqué à tout moment.</p>
            </>}
            {login.error && <div className="form-error" role="alert">{login.error.message}</div>}
            <button className="primary-button login-submit" disabled={login.isPending} type="submit">
              {login.isPending ? <span className="spinner" /> : mode === "passkey" ? <Fingerprint size={18} /> : <LockKeyhole size={18} />}
              {login.isPending ? "Vérification…" : mode === "passkey" ? "Utiliser ma passkey" : mode === "imt" ? "Se connecter avec l'IMT" : "Utiliser ce token"}
            </button>
          </form>
          <p className="login-footnote">HTTPS · Session HttpOnly · Protection CSRF</p>
        </section>
      </div>
    </main>
  );
}
