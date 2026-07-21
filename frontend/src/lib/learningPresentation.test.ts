import { describe, expect, it } from "vitest";
import type { LearningCatalogNode } from "../types";
import {
  readerAudienceSubtitle,
  readerTitle,
  resolvedLearningSection,
  resolvedReaderVisibility,
} from "./learningPresentation";

describe("learning reader presentation", () => {
  it.each([
    "Brouillon privé — [FICTIF] Leçon alpha",
    "private-preview: [FICTIF] Leçon alpha",
    "En revue | [FICTIF] Leçon alpha",
    "Publié - [FICTIF] Leçon alpha",
    "Brouillon privé — Brouillon privé — [FICTIF] Leçon alpha",
  ])("removes manufacturing status prefixes from reader titles: %s", (title) => {
    expect(readerTitle(title, "lesson")).toBe("[FICTIF] Leçon alpha");
  });

  it("uses a neutral code-based title when the bundle title is missing", () => {
    expect(readerTitle("titre non renseigné", "ue", "UE-FIC100")).toBe("UE UE-FIC100");
    expect(readerTitle("Brouillon privé — titre non renseigné", "module", null)).toBe("Module pédagogique");
  });

  it("never exposes an internal audience identifier in the global subtitle", () => {
    expect(readerAudienceSubtitle("FIC100-private-preview", "[FICTIF] 2A")).toBe("[FICTIF] 2A");
    expect(readerAudienceSubtitle("personal:fictive-owner", "release_id:fictive-r1")).toBe(
      "Espace pédagogique personnel",
    );
  });

  it.each([
    ["lesson", "course", "primary"],
    ["exercise", "practice", "primary"],
    ["past_exam", "exam", "primary"],
    ["concept", "glossary", "secondary"],
    ["source", "sources", "secondary"],
  ] as const)("derives v1 presentation for %s", (kind, section, visibility) => {
    const node = { kind, section: null, reader_visibility: undefined } as unknown as LearningCatalogNode;

    expect(resolvedLearningSection(node)).toBe(section);
    expect(resolvedReaderVisibility(node)).toBe(visibility);
  });
});
