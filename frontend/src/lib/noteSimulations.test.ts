import { describe, expect, it, vi } from "vitest";
import type { NoteSimulationScenario } from "../types";
import {
  calculateNoteDraftProjection,
  calculateNoteUeProjection,
  createNoteAssessment,
  createNoteUe,
  mergeSavedNoteIds,
  noteDraftIsValid,
  noteScenarioToDraft,
  noteSimulationPayload,
  noteSimulationSentKeys,
} from "./noteSimulations";

const timestamp = "2026-07-18T10:00:00Z";
const scenario: NoteSimulationScenario = {
  id: "scenario-id",
  name: "Semestre cible",
  created_from: "academic",
  formula_version: "notes-weighted-v1",
  version: 2,
  source_revision: "revision",
  source_captured_at: timestamp,
  rebase_available: false,
  created_at: timestamp,
  updated_at: timestamp,
  result: {
    status: "ready",
    average: 16,
    gpa: 3.8,
    credits_entered: 4,
    credits_included: 4,
    ue_count: 1,
    calculated_ue_count: 1,
    assessment_count: 2,
    scored_count: 2,
    pending_count: 0,
    missing_ects_count: 0,
    completion_rate: 100,
    semesters: [{
      semester: "S5",
      average: 16,
      gpa: 3.8,
      credits_included: 4,
      ue_count: 1,
      calculated_ue_count: 1,
      assessment_count: 2,
      scored_count: 2,
      pending_count: 0,
    }],
    warnings: [],
    formula: {
      version: "notes-weighted-v1",
      label: "Projection de notes pondérées",
      scale: "0 à 20, puis GPA sur 4",
      rounding: "Au centième",
      scope: "Évaluations coefficientées",
      ue_expression: "notes / coefficients",
      average_expression: "moyennes / ECTS",
      gpa_expression: "grades / ECTS",
      official: false,
    },
  },
  ues: [{
    id: "ue-id",
    lineage_key: "source:SIT130",
    semester: "S5",
    ue_code: "SIT130",
    title: "Outils mathématiques",
    credits_ects: 4,
    nature: "imported",
    projection: {
      average: 16,
      grade: "B",
      gpa_points: 3.8,
      used_resit: false,
      coefficient_total: 3,
      assessment_count: 2,
      scored_count: 2,
      pending_count: 0,
    },
    source: { ue_code: "SIT130", status: "current", observed_at: timestamp },
    baseline: { semester: "S5", ue_code: "SIT130", title: "Outils mathématiques", credits_ects: 4 },
    created_at: timestamp,
    updated_at: timestamp,
    assessments: [
      {
        id: "assessment-1",
        lineage_key: "source:note-1",
        label: "Contrôle",
        score: 12,
        coefficient: 1,
        is_resit: false,
        nature: "imported",
        source: { note_key: "note-1", status: "current", observed_at: timestamp },
        baseline: { label: "Contrôle", score: 12, coefficient: 1, is_resit: false },
        created_at: timestamp,
        updated_at: timestamp,
      },
      {
        id: "assessment-2",
        lineage_key: "source:note-2",
        label: "Projet",
        score: 18,
        coefficient: 2,
        is_resit: false,
        nature: "imported",
        source: { note_key: "note-2", status: "current", observed_at: timestamp },
        baseline: { label: "Projet", score: 18, coefficient: 2, is_resit: false },
        created_at: timestamp,
        updated_at: timestamp,
      },
    ],
  }],
};

describe("note simulation drafts", () => {
  it("sends only nested editable fields to the API", () => {
    const draft = noteScenarioToDraft(scenario);
    draft.ues[0]!.assessments[0]!.score = "15";

    expect(noteSimulationPayload(draft)).toEqual({
      version: 2,
      name: "Semestre cible",
      ues: [{
        id: "ue-id",
        semester: "S5",
        ue_code: "SIT130",
        title: "Outils mathématiques",
        credits_ects: 4,
        assessments: [
          { id: "assessment-1", label: "Contrôle", score: 15, coefficient: 1, is_resit: false },
          { id: "assessment-2", label: "Projet", score: 18, coefficient: 2, is_resit: false },
        ],
      }],
    });
  });

  it("weights assessments, then weights UE averages and grades by ECTS", () => {
    const draft = noteScenarioToDraft(scenario);
    const second = createNoteUe("S6");
    second.title = "Réseaux";
    second.credits_ects = "6";
    second.assessments = [
      { ...createNoteAssessment("Examen"), score: "10", coefficient: "1" },
    ];
    draft.ues.push(second);

    expect(calculateNoteUeProjection(draft.ues[0]!)).toMatchObject({
      average: 16,
      grade: "B",
      gpaPoints: 3.8,
    });
    expect(calculateNoteDraftProjection(draft.ues)).toMatchObject({
      average: 12.4,
      gpa: 3.32,
      creditsIncluded: 10,
      calculatedUeCount: 2,
    });
  });

  it("uses the latest scored resit and maps a passed resit to grade E", () => {
    const draft = noteScenarioToDraft(scenario);
    const ue = draft.ues[0]!;
    ue.assessments.push(
      { ...createNoteAssessment("Rattrapage 1"), score: "8", is_resit: true },
      { ...createNoteAssessment("Rattrapage 2"), score: "11", is_resit: true },
    );

    expect(calculateNoteUeProjection(ue)).toMatchObject({
      average: 11,
      grade: "E",
      gpaPoints: 2.5,
      usedResit: true,
    });
  });

  it("excludes pending scores without treating them as zero", () => {
    const draft = noteScenarioToDraft(scenario);
    draft.ues[0]!.assessments.push(createNoteAssessment("Partiel futur"));

    expect(calculateNoteUeProjection(draft.ues[0]!)).toMatchObject({
      average: 16,
      scoredCount: 2,
      pendingCount: 1,
    });
  });

  it("validates all numeric limits before autosave", () => {
    const draft = noteScenarioToDraft(scenario);
    expect(noteDraftIsValid(draft)).toBe(true);
    draft.ues[0]!.assessments[0]!.score = "21";
    expect(noteDraftIsValid(draft)).toBe(false);
    draft.ues[0]!.assessments[0]!.score = "";
    draft.ues[0]!.assessments[0]!.coefficient = "0";
    expect(noteDraftIsValid(draft)).toBe(false);
  });

  it("keeps edits made during save while attaching nested server ids", () => {
    vi.stubGlobal("crypto", { randomUUID: () => "local-id" });
    const draft = noteScenarioToDraft({ ...scenario, ues: [] });
    const ue = createNoteUe("S7");
    ue.title = "UE future";
    ue.credits_ects = "5";
    const sent = noteSimulationSentKeys({ ...draft, ues: [ue] });
    ue.assessments[0]!.score = "17";
    draft.ues.push(ue);
    const savedUe = {
      ...scenario.ues[0]!,
      id: "persisted-ue",
      title: "UE future",
      assessments: [{ ...scenario.ues[0]!.assessments[0]!, id: "persisted-assessment" }],
    };

    const merged = mergeSavedNoteIds(draft, { ...scenario, version: 3, ues: [savedUe] }, sent);

    expect(merged.version).toBe(3);
    expect(merged.ues[0]).toMatchObject({ id: "persisted-ue" });
    expect(merged.ues[0]!.assessments[0]).toMatchObject({ id: "persisted-assessment", score: "17" });
    vi.unstubAllGlobals();
  });
});
