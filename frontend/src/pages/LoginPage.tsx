import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Eye, EyeOff, Fingerprint, KeyRound, LockKeyhole, School, ShieldCheck } from "lucide-react";
import { type FormEvent, useState } from "react";
import { BRAND } from "../brand";
import { BrandMark, Logo } from "../components/Logo";
import { SourceNotice } from "../components/SourceNotice";
import { api } from "../lib/api";
import { authenticateWithPasskey, passkeysSupported } from "../lib/passkeys";
import { replaceSessionState } from "../lib/queries";
import { broadcastSessionChange } from "../lib/sessionSync";
import type { Session } from "../types";

type LoginMode = "passkey" | "imt" | "token";

export function LoginPage() {
  const [mode, setMode] = useState<LoginMode>(passkeysSupported() ? "passkey" : "imt");
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
      <header className="login-topbar"><Logo /></header>
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
        </section>

        <section className="login-panel">
          <div className="login-heading"><span>Accès sécurisé</span><h2>Se connecter</h2></div>
          <div className="segmented login-methods" role="tablist" aria-label="Méthode de connexion">
            {passkeysSupported() && <button className={mode === "passkey" ? "active" : ""} onClick={() => selectMode("passkey")} type="button" role="tab" aria-selected={mode === "passkey"}><Fingerprint size={17} /> Passkey</button>}
            <button className={mode === "imt" ? "active" : ""} onClick={() => selectMode("imt")} type="button" role="tab" aria-selected={mode === "imt"}><School size={17} /> IMT</button>
            <button className={mode === "token" ? "active" : ""} onClick={() => selectMode("token")} type="button" role="tab" aria-selected={mode === "token"}><KeyRound size={17} /> Token</button>
          </div>

          <form onSubmit={submit} className="login-form">
            {mode === "passkey" && <div className="passkey-login-choice"><span><Fingerprint size={28} /></span><div><strong>Connexion sans mot de passe</strong><p>Utilise Face ID, Touch ID, Windows Hello ou la sécurité de ton appareil.</p></div></div>}
            {mode === "imt" && <>
              <label>Identifiant IMT<input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} placeholder="prenom.nom" required autoFocus /></label>
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
              {login.isPending ? "Vérification…" : mode === "passkey" ? "Utiliser ma passkey" : "Continuer"}
            </button>
          </form>
          <p className="login-footnote">HTTPS · Session HttpOnly · Protection CSRF</p>
        </section>
      </div>
    </main>
  );
}
