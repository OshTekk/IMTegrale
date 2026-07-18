import { describe, expect, it, vi } from "vitest";
import type { SimulationScenario } from "../types";
import {
  calculateDraftProjection,
  createDraftEntry,
  draftIsValid,
  mergeSavedIds,
  scenarioToDraft,
  simulationPayload,
} from "./simulations";

const scenario: SimulationScenario = {
  id: "scenario-id",
  name: "Projection",
  created_from: "academic",
  formula_version: "gpa-ects-v1",
  version: 2,
  source_revision: "revision",
  source_captured_at: "2026-07-18T10:00:00Z",
  rebase_available: false,
  created_at: "2026-07-18T10:00:00Z",
  updated_at: "2026-07-18T10:00:00Z",
  result: {
    status: "ready",
    gpa: 3.8,
    credits_entered: 4,
    credits_included: 4,
    ue_count: 1,
    graded_count: 1,
    pending_count: 0,
    missing_ects_count: 0,
    completion_rate: 100,
    semesters: [{ semester: "S1", gpa: 3.8, credits_included: 4, ue_count: 1 }],
    warnings: [],
    formula: {
      version: "gpa-ects-v1",
      label: "Projection GPA par ECTS",
      scale: "0 à 4",
      rounding: "Au centième",
      scope: "UE complètes",
      expression: "somme(points × ECTS) / somme(ECTS)",
      official: false,
    },
  },
  entries: [{
    id: "entry-id",
    lineage_key: "source:SIT130",
    semester: "S1",
    ue_code: "SIT130",
    title: "Mathématiques",
    credits_ects: 4,
    grade: "B",
    gpa_points: 3.8,
    status: "validated",
    nature: "imported",
    source: { ue_code: "SIT130", status: "current", grade_source: "competences", observed_at: "2026-07-18T10:00:00Z" },
    baseline: { semester: "S1", ue_code: "SIT130", title: "Mathématiques", credits_ects: 4, grade: "B" },
    created_at: "2026-07-18T10:00:00Z",
    updated_at: "2026-07-18T10:00:00Z",
  }],
};

describe("simulation drafts", () => {
  it("only sends editable fields back to the API", () => {
    const draft = scenarioToDraft(scenario);
    draft.entries[0]!.grade = "A";

    expect(simulationPayload(draft)).toEqual({
      version: 2,
      name: "Projection",
      entries: [{
        id: "entry-id",
        semester: "S1",
        ue_code: "SIT130",
        title: "Mathématiques",
        credits_ects: 4,
        grade: "A",
      }],
    });
  });

  it("keeps edits made during a save and attaches the returned server id", () => {
    vi.stubGlobal("crypto", { randomUUID: () => "local-id" });
    const draft = scenarioToDraft({ ...scenario, entries: [] });
    const local = createDraftEntry("S2");
    local.title = "UE future";
    local.credits_ects = "6";
    draft.entries.push(local);
    const sentKeys = [local.clientKey];
    local.grade = "A";
    const savedEntry = { ...scenario.entries[0]!, id: "persisted-id", title: "UE future" };

    const merged = mergeSavedIds(draft, { ...scenario, version: 3, entries: [savedEntry] }, sentKeys);

    expect(merged.version).toBe(3);
    expect(merged.entries[0]).toMatchObject({ id: "persisted-id", grade: "A" });
    vi.unstubAllGlobals();
  });

  it("rejects an empty UE or invalid ECTS before autosave", () => {
    const draft = scenarioToDraft({ ...scenario, entries: [] });
    draft.entries.push(createDraftEntry(null));
    expect(draftIsValid(draft)).toBe(false);
    draft.entries[0]!.title = "UE libre";
    draft.entries[0]!.credits_ects = "61";
    expect(draftIsValid(draft)).toBe(false);
    draft.entries[0]!.credits_ects = "3";
    expect(draftIsValid(draft)).toBe(true);
  });

  it("weights grades by ECTS and excludes a pending grade", () => {
    const draft = scenarioToDraft(scenario);
    const failed = createDraftEntry("S2");
    failed.title = "UE non validee";
    failed.credits_ects = "2";
    failed.grade = "FX";
    const pending = createDraftEntry("S2");
    pending.title = "UE en attente";
    pending.credits_ects = "6";
    draft.entries.push(failed, pending);

    expect(calculateDraftProjection(draft.entries)).toMatchObject({
      gpa: 2.53,
      creditsEntered: 12,
      creditsIncluded: 6,
      gradedCount: 2,
      pendingCount: 1,
    });
  });
});
