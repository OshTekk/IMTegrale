import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Clipboard, Clock3, KeyRound, Plus, ShieldCheck, ShieldOff, Trash2, Users } from "lucide-react";
import { type FormEvent, useState } from "react";
import { EmptyState } from "../components/EmptyState";
import { Modal } from "../components/Modal";
import { useToast } from "../components/Toast";
import { api } from "../lib/api";
import { formatDate, relativeDate } from "../lib/format";
import { queryKeys, useTokens } from "../lib/queries";
import type { ShareToken } from "../types";

async function copyText(value: string) {
  if (navigator.clipboard) return navigator.clipboard.writeText(value);
  const area = document.createElement("textarea");
  area.value = value;
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  document.execCommand("copy");
  area.remove();
}

function roleLabel(role: ShareToken["role"]): string {
  if (role === "owner") return "Propriétaire";
  return role === "editor" ? "Lecture (ancien accès)" : "Lecture";
}

function TokenReveal({ token, onClose }: { token: ShareToken; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await copyText(token.token ?? "");
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };
  return <Modal open title="Token créé" description="Ce secret n'est affiché qu'une seule fois. Transmets-le par un canal privé." onClose={onClose} size="large"><div className="token-reveal"><div className="token-secret"><code>{token.token}</code><button className="secondary-button" type="button" onClick={copy}>{copied ? <Check size={17} /> : <Clipboard size={17} />}{copied ? "Copié" : "Copier"}</button></div><div className="token-summary"><span><strong>{token.name}</strong>Nom de l'accès</span><span><strong>{roleLabel(token.role)}</strong>Droits</span><span><strong>{token.expires_at ? formatDate(token.expires_at, false) : "Sans expiration"}</strong>Expiration</span></div></div><footer className="modal-actions"><button className="primary-button" type="button" onClick={onClose}>J'ai conservé le token</button></footer></Modal>;
}

export function SharingPage() {
  const tokens = useTokens();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [name, setName] = useState("");
  const [expiry, setExpiry] = useState("30");
  const [created, setCreated] = useState<ShareToken | null>(null);
  const create = useMutation({
    mutationFn: () => api<ShareToken>("/api/v1/tokens", { method: "POST", body: JSON.stringify({ name, role: "viewer", expires_in_days: expiry === "never" ? null : Number(expiry) }) }),
    onSuccess: (token) => {
      setCreated(token);
      setName("");
      queryClient.invalidateQueries({ queryKey: queryKeys.account });
    },
    onError: (error) => showToast(error.message, "error")
  });
  const revoke = useMutation({
    mutationFn: (id: string) => api(`/api/v1/tokens/${id}`, { method: "DELETE" }),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: queryKeys.account }); showToast("Accès révoqué immédiatement"); },
    onError: (error) => showToast(error.message, "error")
  });
  const submit = (event: FormEvent) => { event.preventDefault(); create.mutate(); };
  const activeTokens = tokens.data?.filter((token) => !token.revoked_at && (!token.expires_at || new Date(token.expires_at) > new Date())).length ?? 0;

  return (
    <div className="page-stack sharing-page">
      <section className="sharing-summary">
        <div><span className="summary-icon"><Users size={22} /></span><div><strong>{activeTokens}</strong><span>accès actif{activeTokens > 1 ? "s" : ""}</span></div></div>
        <div><span className="summary-icon secure"><ShieldCheck size={22} /></span><div><strong>Hachés</strong><span>secrets non récupérables</span></div></div>
        <div><span className="summary-icon revoke"><ShieldOff size={22} /></span><div><strong>Révocables</strong><span>sessions coupées instantanément</span></div></div>
      </section>

      <section className="sharing-layout">
        <form className="create-token-panel" onSubmit={submit}>
          <header><span><Plus size={18} /></span><div><h2>Créer un accès</h2><p>Génère un token lié à ton compte.</p></div></header>
          <label>Nom de la personne ou de l'appareil<input value={name} onChange={(event) => setName(event.target.value)} placeholder="Justine · iPhone" required /></label>
          <div className="shared-access-scope"><ShieldCheck size={19} /><span><strong>Lecture seule</strong><small>Consulter les notes PASS, les GPA et les ECTS sans pouvoir les modifier.</small></span></div>
          <label>Expiration<select value={expiry} onChange={(event) => setExpiry(event.target.value)}><option value="7">Dans 7 jours</option><option value="30">Dans 30 jours</option><option value="90">Dans 90 jours</option><option value="365">Dans 1 an</option><option value="never">Sans expiration</option></select></label>
          <button className="primary-button" type="submit" disabled={create.isPending}>{create.isPending ? <span className="spinner" /> : <KeyRound size={18} />} Générer le token</button>
        </form>

        <section className="token-list-panel">
          <header><div><h2>Accès partagés</h2><p>Le token complet n'est jamais conservé sur le serveur.</p></div></header>
          {tokens.isPending ? <div className="skeleton token-list-skeleton" /> : tokens.data?.length ? <div className="token-list">{tokens.data.map((token) => {
            const expired = Boolean(token.expires_at && new Date(token.expires_at) <= new Date());
            const inactive = Boolean(token.revoked_at || expired);
            return <article className={`token-row ${inactive ? "inactive" : ""}`} key={token.id}><span className="token-icon"><KeyRound size={18} /></span><div className="token-main"><div><strong>{token.name}</strong><span className={`role-pill ${token.role}`}>{roleLabel(token.role)}</span>{inactive && <span className="status-pill danger">{token.revoked_at ? "Révoqué" : "Expiré"}</span>}</div><code>bn1_{token.prefix}_••••••••</code><small><Clock3 size={13} /> {token.last_used_at ? `Utilisé ${relativeDate(token.last_used_at)}` : "Jamais utilisé"} · {token.expires_at ? `expire le ${formatDate(token.expires_at, false)}` : "sans expiration"}</small></div>{!inactive && <button className="icon-button danger-icon" type="button" onClick={() => revoke.mutate(token.id)} disabled={revoke.isPending} aria-label={`Révoquer ${token.name}`} title="Révoquer"><Trash2 size={17} /></button>}</article>;
          })}</div> : <EmptyState title="Aucun accès partagé" detail="Crée un token pour donner un accès contrôlé à ce compte." />}
        </section>
      </section>
      {created && <TokenReveal token={created} onClose={() => setCreated(null)} />}
    </div>
  );
}
