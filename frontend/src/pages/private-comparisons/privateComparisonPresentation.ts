import type {
  PrivateComparisonCommonUeResponse,
  PrivateComparisonInvitationResponse,
  PrivateComparisonRelationResponse,
} from "../../generated/api/types.gen";
import { ApiError } from "../../lib/api";

export const PRIVATE_COMPARISON_DURATIONS = [7, 30, 60, 90] as const;
export const PRIVATE_COMPARISON_GRADES = ["A", "B", "C", "D", "E", "FX", "F"] as const;

const invitationTokenPattern = /^pcinv1_[A-Za-z0-9_-]{43}$/;
const privateComparisonPublicIdPattern = /^pc_[A-Za-z0-9_-]{24}$/;

export type InvitationFragmentResult =
  { state: "valid"; token: string } | { state: "missing" | "invalid"; token: null };

export function validInvitationToken(token: string): boolean {
  return invitationTokenPattern.test(token);
}

export function invitationFromFragment(hash: string): InvitationFragmentResult {
  if (!hash.startsWith("#")) return { state: "missing", token: null };
  const params = new URLSearchParams(hash.slice(1));
  const values = params.getAll("invite");
  if (values.length === 0) return { state: "missing", token: null };
  if (values.length !== 1 || !validInvitationToken(values[0]!)) {
    return { state: "invalid", token: null };
  }
  return { state: "valid", token: values[0]! };
}

export function validPrivateComparisonPublicId(publicId: string | undefined): publicId is string {
  return Boolean(publicId && privateComparisonPublicIdPattern.test(publicId));
}

export function invitationUrl(token: string, origin = window.location.origin): string {
  if (!validInvitationToken(token)) throw new Error("Invalid private comparison invitation token");
  return `${origin}/comparisons/accept#invite=${token}`;
}

export function freshnessLabel(value: "current" | "recommended" | "stale"): string {
  return {
    current: "À jour",
    recommended: "Actualisation conseillée",
    stale: "À actualiser",
  }[value];
}

export function invitationStatusLabel(status: PrivateComparisonInvitationResponse["status"]): string {
  return {
    active: "Active",
    consumed: "Utilisée",
    expired: "Expirée",
    revoked: "Révoquée",
  }[status];
}

export function comparisonStatusLabel(status: PrivateComparisonRelationResponse["status"]): string {
  return {
    active: "Active",
    expired: "Expirée",
    revoked: "Révoquée",
  }[status];
}

export function privateComparisonErrorMessage(
  error: unknown,
  context: "create" | "list" | "invitation" | "conflict" = "list",
): string {
  if (!(error instanceof ApiError)) {
    return context === "create"
      ? "Impossible de créer l’invitation pour le moment."
      : "Impossible de charger les comparaisons pour le moment.";
  }
  if (error.code === "PRIVATE_COMPARISON_INVITATION_LIMIT") {
    return "Tu as déjà atteint le nombre maximal d’invitations actives.";
  }
  if (error.code === "PRIVATE_COMPARISON_INVITATION_RATE_LIMIT" || error.status === 429) {
    return "Trop d’invitations ont été créées récemment. Réessaie plus tard.";
  }
  if (error.code === "PRIVATE_COMPARISON_CONFLICT" || error.code === "PRIVATE_COMPARISON_ALREADY_ACTIVE") {
    return "La comparaison a changé. Recharge son état avant de continuer.";
  }
  if (context === "invitation") return "Cette invitation n’est plus disponible.";
  if (context === "create") return "Impossible de créer l’invitation pour le moment.";
  return "Impossible de charger les comparaisons pour le moment.";
}

export function privateComparisonUnavailable(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.code === "PRIVATE_COMPARISON_UNAVAILABLE");
}

export function sortCommonUes(
  values: readonly PrivateComparisonCommonUeResponse[],
): PrivateComparisonCommonUeResponse[] {
  return [...values].sort((left, right) => {
    const semester = (left.current.semester ?? left.other.semester ?? "S99").localeCompare(
      right.current.semester ?? right.other.semester ?? "S99",
      "fr",
    );
    return semester || left.official_code.localeCompare(right.official_code, "fr", { sensitivity: "base" });
  });
}
