import { useMutation } from "@tanstack/react-query";
import { CheckCircle2, Copy, Fingerprint, KeyRound, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { passkeysSupported, registerPasskey } from "../lib/passkeys";
import type { ShareToken } from "../types";
import { useToast } from "./Toast";
import { Modal } from "./Modal";

export function SecuritySetupModal({ open, onComplete }: { open: boolean; onComplete: () => void }) {
  const { showToast } = useToast();
  const [personalToken, setPersonalToken] = useState<string | null>(null);
  const [passkeyCreated, setPasskeyCreated] = useState(false);
  const passkey = useMutation({
    mutationFn: () => registerPasskey("Appareil principal"),
    onSuccess: () => { setPasskeyCreated(true); showToast("Passkey ajoutée"); },
    onError: (error) => showToast(error.message, "error"),
  });
  const token = useMutation({
    mutationFn: () => api<ShareToken>("/api/v1/tokens", {
      method: "POST",
      body: JSON.stringify({
        name: "Token personnel",
        role: "owner",
        expires_in_days: 365,
      }),
    }),
    onSuccess: (created) => setPersonalToken(created.token ?? null),
    onError: (error) => showToast(error.message, "error"),
  });
  const finish = useMutation({
    mutationFn: () => api("/api/v1/auth/security-setup/complete", { method: "POST", body: "{}" }),
    onSuccess: onComplete,
    onError: (error) => showToast(error.message, "error"),
  });

  return (
    <Modal open={open} title="Sécuriser tes prochaines connexions" description="Choisis une ou plusieurs méthodes, ou continue simplement avec ton compte IMT." onClose={() => undefined} size="large" className="security-setup-modal">
      <div className="security-setup-scroll">
        <div className="security-setup-options">
          <section className="security-method recommended">
            <header><span><Fingerprint size={22} /></span><div><strong>Passkey</strong><small>Recommandé</small></div></header>
            <p>Connexion immédiate avec la sécurité de ton appareil, sans requête vers l'IMT.</p>
            <button className="primary-button" type="button" disabled={!passkeysSupported() || passkey.isPending || passkeyCreated} onClick={() => passkey.mutate()}>{passkeyCreated ? <CheckCircle2 size={17} /> : <Fingerprint size={17} />}{passkeyCreated ? "Passkey ajoutée" : "Ajouter une passkey"}</button>
          </section>
          <section className="security-method">
            <header><span><KeyRound size={22} /></span><div><strong>Token personnel</strong><small>Alternative révocable</small></div></header>
            <p>Un secret à conserver dans ton gestionnaire de mots de passe. Il donne les droits propriétaire.</p>
            {!personalToken ? <button className="secondary-button" type="button" disabled={token.isPending} onClick={() => token.mutate()}><KeyRound size={17} /> Générer</button> : <div className="personal-token-result"><code>{personalToken}</code><button className="icon-button" type="button" onClick={() => { void navigator.clipboard.writeText(personalToken); showToast("Token copié"); }} aria-label="Copier le token" title="Copier"><Copy size={17} /></button></div>}
          </section>
        </div>
        <div className="privacy-note"><ShieldCheck size={16} /><span>La passkey enregistre uniquement une clé publique. Le token n'est affiché qu'une fois et son secret est conservé sous forme d'empreinte côté serveur.</span></div>
      </div>
      <footer className="modal-actions"><button className="secondary-button" type="button" onClick={() => finish.mutate()} disabled={finish.isPending}>{passkeyCreated || personalToken ? "Terminer" : "Continuer sans ajout"}</button></footer>
    </Modal>
  );
}
