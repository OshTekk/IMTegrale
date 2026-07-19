import { ApiError } from "./api";
import type { LearningCatalogNodeKind, Session } from "../types";

export type LearningRouteState = "allowed" | "probe" | "reverify" | "hidden";
export type LearningErrorState = "reverify" | "catalog-unavailable" | "hidden" | "error";

export function learningEntryVisible(session: Session | undefined): boolean {
  return (
    session?.authenticated === true &&
    session.role === "owner" &&
    (session.auth_method === "imt" || session.auth_method === "passkey") &&
    session.learning?.available === true
  );
}

export function learningRouteState(session: Session | undefined): LearningRouteState {
  const primarySession =
    session?.authenticated === true &&
    session.role === "owner" &&
    (session.auth_method === "imt" || session.auth_method === "passkey");
  if (!primarySession) return "hidden";
  if (session.learning?.reverify_required === true) return "reverify";
  if (learningEntryVisible(session)) return "allowed";
  return session.learning?.available === false &&
    Boolean(session.learning.audience_label && session.learning.level_label)
    ? "probe"
    : "hidden";
}

export function learningErrorState(error: unknown): LearningErrorState {
  if (error instanceof ApiError) {
    if (error.code === "STUDENT_REVERIFICATION_REQUIRED") return "reverify";
    if (error.code === "LEARNING_CATALOG_UNAVAILABLE") return "catalog-unavailable";
    if (error.status === 404) return "hidden";
  }
  return "error";
}

export function isSafeLearningId(value: string): boolean {
  return value.length > 0 && value.length <= 128 && /^[a-z0-9]+(?:[._:-][a-z0-9]+)*$/.test(value);
}

export function safeLearningId(value: string | undefined): string | null {
  return value && isSafeLearningId(value) ? value : null;
}

export function learningContentHref(kind: LearningCatalogNodeKind, id: string): string | null {
  if (!isSafeLearningId(id)) return null;
  const encoded = encodeURIComponent(id);
  if (kind === "ue") return `/parcours/ues/${encoded}`;
  if (kind === "module") return `/parcours/modules/${encoded}`;
  if (kind === "source") return `/parcours/sources/${encoded}`;
  if (kind === "exercise" || kind === "pc_td" || kind === "past_exam") {
    return `/parcours/exercices/${encoded}`;
  }
  if (kind === "concept" || kind === "lesson") return `/parcours/lecons/${encoded}`;
  return null;
}

export function learningContentMode(kind: LearningCatalogNodeKind): "lesson" | "exercise" | null {
  if (kind === "concept" || kind === "lesson") return "lesson";
  if (kind === "exercise" || kind === "pc_td" || kind === "past_exam") return "exercise";
  return null;
}

export function learningResumeHref(
  kind: LearningCatalogNodeKind,
  id: string,
  position: { last_section_id: string | null; last_page: number | null },
): string | null {
  const href = learningContentHref(kind, id);
  if (!href) return null;
  if (kind === "source" && Number.isInteger(position.last_page) && (position.last_page ?? 0) > 0) {
    return `${href}?page=${position.last_page}`;
  }
  const sectionId = safeLearningId(position.last_section_id ?? undefined);
  return sectionId ? `${href}#${encodeURIComponent(sectionId)}` : href;
}

export function learningAssetUrl(assetId: string): string | null {
  return isSafeLearningId(assetId) ? `/api/v1/learning/assets/${encodeURIComponent(assetId)}` : null;
}

export function learningDocumentTitle(pathname: string): string | undefined {
  if (!pathname.startsWith("/parcours")) return undefined;
  if (pathname.startsWith("/parcours/recherche")) return "Recherche Parcours";
  if (pathname.startsWith("/parcours/progression")) return "Progression Parcours";
  if (pathname.startsWith("/parcours/sources") || pathname.startsWith("/parcours/references")) return "Source Parcours";
  if (pathname.startsWith("/parcours/exercices")) return "Exercice Parcours";
  if (pathname.startsWith("/parcours/lecons")) return "Leçon Parcours";
  if (pathname.startsWith("/parcours/modules")) return "Module Parcours";
  if (pathname.startsWith("/parcours/ues")) return "UE Parcours";
  return "Parcours";
}

export const learningErrorCopy: Record<LearningErrorState, { title: string; message: string }> = {
  reverify: {
    title: "Vérification IMT nécessaire",
    message: "Reconnecte-toi ponctuellement avec ton compte IMT pour confirmer ton statut étudiant.",
  },
  "catalog-unavailable": {
    title: "Parcours temporairement indisponible",
    message: "Le catalogue pédagogique ne peut pas être chargé en toute sécurité. Réessaie plus tard.",
  },
  hidden: {
    title: "Page introuvable",
    message: "Cette page n'est pas disponible.",
  },
  error: {
    title: "Chargement impossible",
    message: "Une erreur est survenue sans exposer de contenu privé. Réessaie.",
  },
};
