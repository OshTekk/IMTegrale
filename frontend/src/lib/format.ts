const number = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 2 });

export function formatNumber(value: number | null | undefined, suffix = ""): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${number.format(value)}${suffix}`;
}

export function formatDate(value: string | null | undefined, withTime = true): string {
  if (!value) return "Jamais";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("fr-FR", {
    dateStyle: "medium",
    ...(withTime ? { timeStyle: "short" as const } : {})
  }).format(date);
}

export function relativeDate(value: string | null | undefined): string {
  if (!value) return "Jamais";
  const date = new Date(value);
  const seconds = Math.round((date.getTime() - Date.now()) / 1000);
  const formatter = new Intl.RelativeTimeFormat("fr-FR", { numeric: "auto" });
  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["year", 31_536_000],
    ["month", 2_592_000],
    ["day", 86_400],
    ["hour", 3_600],
    ["minute", 60]
  ];
  for (const [unit, divisor] of units) {
    if (Math.abs(seconds) >= divisor) return formatter.format(Math.round(seconds / divisor), unit);
  }
  return "à l'instant";
}

export function yearLabel(year: string): string {
  return { "1": "1re année", "2": "2e année", "3": "3e année", "": "Sans année" }[year] ?? `Année ${year}`;
}

export function eventLabel(kind: string, payload: Record<string, unknown>): string {
  if (kind === "note:new") return `Nouvelle note en ${String(payload.ue_code ?? "")}`;
  if (kind === "note:created") return `Note ajoutée en ${String(payload.ue_code ?? "")}`;
  if (kind === "note:updated") return `Note modifiée en ${String(payload.ue_code ?? "")}`;
  if (kind === "ue:updated") return `UE ${String(payload.ue_code ?? "")} mise à jour`;
  if (kind === "sync:completed") return "Synchronisation PASS terminée";
  if (kind === "sync:error") return "Échec de synchronisation PASS";
  if (kind === "token:created") return `Accès « ${String(payload.name ?? "")} » créé`;
  if (kind === "token:revoked") return `Accès « ${String(payload.name ?? "")} » révoqué`;
  if (kind === "auth:login") return "Nouvelle connexion";
  if (kind === "migration:completed") return "Données historiques migrées";
  return kind.replaceAll(":", " · ");
}
