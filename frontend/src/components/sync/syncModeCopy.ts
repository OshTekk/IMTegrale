import type { SyncMode } from "../../generated/api/types.gen";

export interface SyncModePresentation {
  mode: SyncMode;
  title: string;
  summary: string;
  stored: string;
  benefit: string;
  limit: string;
  badge?: string;
}

export const syncModePresentations: readonly SyncModePresentation[] = [
  {
    mode: "manual",
    title: "À la demande",
    summary: "Tu choisis quand actualiser tes résultats.",
    stored: "Aucun mot de passe IMT. La session privée peut rester disponible pour tes demandes manuelles.",
    benefit: "Exposition minimale et aucun appel planifié.",
    limit: "Les résultats ne se mettent pas à jour seuls.",
  },
  {
    mode: "session_only",
    title: "Automatique avec session privée",
    summary: "IMTégrale actualise tes résultats tant que la session PASS/HUB reste valide.",
    stored: "Session PASS/HUB chiffrée, sans conserver ton mot de passe IMT.",
    benefit: "Synchronisation automatique avec un risque serveur plus limité.",
    limit: "Une reconnexion est nécessaire lorsque la session distante expire.",
    badge: "Recommandé pour limiter le risque",
  },
  {
    mode: "autonomous",
    title: "Automatique autonome",
    summary: "Le worker de synchronisation peut recréer une session après son expiration.",
    stored: "Session privée chiffrée et mot de passe IMT conservé sous enveloppe chiffrée.",
    benefit: "Meilleure continuité et moins de reconnexions.",
    limit: "Risque serveur supérieur. Quitter ce mode supprime irréversiblement le mot de passe conservé.",
    badge: "Plus pratique",
  },
] as const;

export function syncModeTitle(mode: SyncMode): string {
  return syncModePresentations.find((item) => item.mode === mode)?.title ?? "Synchronisation";
}
