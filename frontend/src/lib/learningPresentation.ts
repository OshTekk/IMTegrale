import type {
  LearningCatalogNode,
  LearningCatalogNodeKind,
  LearningDifficulty,
  LearningReaderVisibility,
  LearningReviewStatus,
  LearningSection,
} from "../types";

const statusPrefix =
  /^(?:(?:brouillon(?:\s+privé)?|private[ _-]?preview|en\s+revue|relu|reviewed|publié|retiré)\s*[—–:|-]\s*)/i;
const missingTitle = /^(?:titre|title)\s+(?:non\s+renseign[ée]|manquant|indisponible)$/i;
const missingTitleSuffix = /\s*[—–|-]\s*(?:titre|title)\s+(?:non\s+renseign[ée]|manquant|indisponible)$/i;
const internalAudience = /(?:private[ _-]?preview|personal:|release[_ -]?id|audience[_ -]?id)/i;

const kindLabels: Record<LearningCatalogNodeKind, string> = {
  audience: "Espace pédagogique",
  curriculum: "Cursus",
  promotion: "Promotion",
  level: "Niveau",
  semester: "Semestre",
  ue: "UE",
  module: "Module",
  chapter: "Chapitre",
  concept: "Concept",
  lesson: "Leçon",
  exercise: "Exercice",
  pc_td: "PC / TD",
  past_exam: "Annale",
  source: "Document",
};

const defaultSectionByKind: Partial<Record<LearningCatalogNodeKind, LearningSection>> = {
  chapter: "course",
  lesson: "course",
  exercise: "practice",
  pc_td: "practice",
  past_exam: "exam",
  concept: "glossary",
  source: "sources",
};

function stripStatusPrefixes(value: string): string {
  let result = value.trim();
  for (let index = 0; index < 4; index += 1) {
    const next = result.replace(statusPrefix, "").trim();
    if (next === result) break;
    result = next;
  }
  return result;
}

function safeDisplayCode(value: string | null | undefined): string | null {
  const code = value?.trim();
  if (!code || code.length > 48 || !/^[A-Za-z0-9][A-Za-z0-9 ./_-]*$/.test(code)) return null;
  return code;
}

export function learningKindLabel(kind: LearningCatalogNodeKind): string {
  return kindLabels[kind];
}

export function readerTitle(
  rawTitle: string | null | undefined,
  kind: LearningCatalogNodeKind,
  explicitCode?: string | null,
): string {
  const title = stripStatusPrefixes(rawTitle ?? "");
  const code = safeDisplayCode(explicitCode);
  if (!title || missingTitle.test(title))
    return code ? `${kindLabels[kind]} ${code}` : `${kindLabels[kind]} pédagogique`;
  if (missingTitleSuffix.test(title)) {
    const inferredCode = safeDisplayCode(title.replace(missingTitleSuffix, "").trim());
    return `${kindLabels[kind]} ${code ?? inferredCode ?? "pédagogique"}`;
  }
  return title;
}

export function readerCatalogTitle(node: Pick<LearningCatalogNode, "title" | "kind" | "code">): string {
  return readerTitle(node.title, node.kind, node.code);
}

export function readerAudienceSubtitle(
  audienceLabel: string | null | undefined,
  levelLabel: string | null | undefined,
) {
  const parts = [audienceLabel, levelLabel]
    .map((value) => value?.trim() ?? "")
    .filter((value) => value && !internalAudience.test(value));
  return [...new Set(parts)].join(" · ") || "Espace pédagogique personnel";
}

export function resolvedLearningSection(node: Pick<LearningCatalogNode, "kind" | "section">): LearningSection | null {
  return node.section ?? defaultSectionByKind[node.kind] ?? null;
}

export function resolvedReaderVisibility(
  node: Pick<LearningCatalogNode, "kind" | "reader_visibility">,
): LearningReaderVisibility {
  return node.reader_visibility ?? (node.kind === "concept" || node.kind === "source" ? "secondary" : "primary");
}

export function readerVisible(node: Pick<LearningCatalogNode, "kind" | "reader_visibility">): boolean {
  return resolvedReaderVisibility(node) !== "hidden";
}

export function reviewStatusLabel(status: LearningReviewStatus): string {
  const labels: Record<LearningReviewStatus, string> = {
    draft: "Brouillon",
    in_review: "En revue",
    reviewed: "Relu",
    private_preview: "Version de travail",
    published: "Publié",
    retired: "Retiré",
  };
  return labels[status];
}

export function difficultyLabel(difficulty: LearningDifficulty | null): string | null {
  if (difficulty === "introductory") return "Découverte";
  if (difficulty === "standard") return "Intermédiaire";
  if (difficulty === "advanced") return "Avancé";
  return null;
}
