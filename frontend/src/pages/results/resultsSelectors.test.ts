import { describe, expect, it } from "vitest";
import type { NoteItem, UeItem } from "../../types";
import {
  buildResultsIndex,
  groupRecentEvaluations,
  groupUesByAcademicPeriod,
  selectEvaluations,
  selectUes,
  sortUeEvaluations,
} from "./resultsSelectors";
import type { ResultsState } from "./resultsState";

const ues: UeItem[] = [
  {
    code: "ANA-FICTIF",
    title: "Analyse numérique fictive",
    year: "1",
    semester: "S5",
    official_code: "FICTIF-S5-ANA",
    credits_ects: 6,
    earned_credits_ects: 6,
    metadata_source: "competences",
    metadata_refreshed_at: "2026-01-04T08:00:00Z",
    average: 15,
    grade: "B",
    grade_description: "[14-17[",
    grade_source: "competences",
    gpa: 3.8,
    validated: true,
    used_resit: false,
    note_count: 2,
  },
  {
    code: "RES-FICTIF",
    title: "Réseaux entièrement imaginaires",
    year: "1",
    semester: "S6",
    official_code: "FICTIF-S6-RES",
    credits_ects: 5,
    earned_credits_ects: 5,
    metadata_source: "competences",
    metadata_refreshed_at: "2026-01-05T08:00:00Z",
    average: 10,
    grade: "E",
    grade_description: "Rattrapage",
    grade_source: "pass_calculated",
    gpa: 2.5,
    validated: true,
    used_resit: true,
    note_count: 2,
  },
  {
    code: "ART-FICTIF",
    title: "Création synthétique",
    year: "2",
    semester: "S7",
    official_code: null,
    credits_ects: null,
    earned_credits_ects: null,
    metadata_source: "manual",
    metadata_refreshed_at: null,
    average: null,
    grade: null,
    grade_description: null,
    grade_source: "manual_calculated",
    gpa: null,
    validated: false,
    used_resit: false,
    note_count: 0,
  },
  {
    code: "LIBRE-FICTIF",
    title: "Projet sans semestre",
    year: "2",
    semester: null,
    official_code: null,
    credits_ects: 2,
    earned_credits_ects: 0,
    metadata_source: "manual",
    metadata_refreshed_at: null,
    average: 8,
    grade: "FX",
    grade_description: "[5-10[",
    grade_source: "pass_calculated",
    gpa: 0,
    validated: false,
    used_resit: false,
    note_count: 1,
  },
];

const notes: NoteItem[] = [
  {
    id: "note-ana-projet",
    source: "pass",
    ue_code: "ANA-FICTIF",
    label: "Projet fictif",
    score: 16,
    coefficient: 4,
    is_resit: false,
    has_override: false,
    editable: false,
    detected_at: "2026-01-03T08:00:00Z",
    updated_at: "2026-01-03T08:00:00Z",
  },
  {
    id: "note-ana-controle",
    source: "pass",
    ue_code: "ANA-FICTIF",
    label: "Contrôle synthétique",
    score: 13,
    coefficient: 2,
    is_resit: false,
    has_override: false,
    editable: false,
    detected_at: "2026-01-02T08:00:00Z",
    updated_at: "2026-01-02T08:00:00Z",
  },
  {
    id: "note-res-classique",
    source: "pass",
    ue_code: "RES-FICTIF",
    label: "Évaluation réseau fictive",
    score: 8,
    coefficient: 3,
    is_resit: false,
    has_override: false,
    editable: false,
    detected_at: "2026-01-01T08:00:00Z",
    updated_at: "2026-01-01T08:00:00Z",
  },
  {
    id: "note-res-rattrapage",
    source: "pass",
    ue_code: "RES-FICTIF",
    label: "Session de rattrapage fictive",
    score: 11,
    coefficient: 1,
    is_resit: true,
    has_override: false,
    editable: false,
    detected_at: "2026-01-05T08:00:00Z",
    updated_at: "2026-01-05T08:00:00Z",
  },
  {
    id: "note-libre",
    source: "pass",
    ue_code: "LIBRE-FICTIF",
    label: "Présentation fictive",
    score: 8,
    coefficient: 1,
    is_resit: false,
    has_override: false,
    editable: false,
    detected_at: "2026-01-04T08:00:00Z",
    updated_at: "2026-01-04T08:00:00Z",
  },
];

const defaultState: ResultsState = {
  view: "evaluations",
  year: null,
  semester: null,
  ue: null,
  type: "all",
  sort: "ue-coefficient",
  q: "",
};

describe("resultsSelectors", () => {
  const index = buildResultsIndex(ues, notes);

  it("searches accents and case insensitively across UE and evaluation labels", () => {
    const result = selectEvaluations(index, notes, {
      ...defaultState,
      q: "EVALUATION RESEAU",
    });
    expect(result.map((note) => note.id)).toEqual(["note-res-classique"]);
  });

  it("filters by year, semester, UE and evaluation type", () => {
    expect(
      selectEvaluations(index, notes, {
        ...defaultState,
        year: "1",
        semester: "S6",
        ue: "RES-FICTIF",
        type: "resit",
      }).map((note) => note.id),
    ).toEqual(["note-res-rattrapage"]);
    expect(selectEvaluations(index, notes, { ...defaultState, type: "classic" }).some((note) => note.is_resit)).toBe(
      false,
    );
  });

  it("supports every documented evaluation sort", () => {
    expect(selectEvaluations(index, notes, defaultState).map((note) => note.id)).toEqual([
      "note-ana-projet",
      "note-ana-controle",
      "note-libre",
      "note-res-rattrapage",
      "note-res-classique",
    ]);
    expect(selectEvaluations(index, notes, { ...defaultState, sort: "semester-ue" }).map((note) => note.id)).toEqual([
      "note-ana-projet",
      "note-ana-controle",
      "note-res-rattrapage",
      "note-res-classique",
      "note-libre",
    ]);
    expect(selectEvaluations(index, notes, { ...defaultState, sort: "coefficient" })[0]?.id).toBe("note-ana-projet");
    expect(selectEvaluations(index, notes, { ...defaultState, sort: "score-desc" })[0]?.score).toBe(16);
    expect(selectEvaluations(index, notes, { ...defaultState, sort: "score-asc" })[0]?.score).toBe(8);
    expect(selectEvaluations(index, notes, { ...defaultState, sort: "recent" })[0]?.id).toBe("note-res-rattrapage");
  });

  it("orders rattrapages before coefficient inside an UE", () => {
    expect(sortUeEvaluations(notes.filter((note) => note.ue_code === "RES-FICTIF")).map((note) => note.id)).toEqual([
      "note-res-rattrapage",
      "note-res-classique",
    ]);
  });

  it("keeps UE without notes, ECTS or semester and preserves authoritative grades", () => {
    const selected = selectUes(index, { ...defaultState, view: "ues" });
    expect(selected.find((ue) => ue.code === "ART-FICTIF")).toMatchObject({
      credits_ects: null,
      note_count: 0,
      grade: null,
    });
    expect(selected.find((ue) => ue.code === "ANA-FICTIF")?.grade_source).toBe("competences");
    expect(selected.find((ue) => ue.code === "RES-FICTIF")?.grade_source).toBe("pass_calculated");
    expect(
      groupUesByAcademicPeriod(selected)
        .flatMap((year) => year.semesters)
        .some((semester) => semester.label === "Sans semestre"),
    ).toBe(true);
  });

  it("groups recent imports in newest-first input order", () => {
    const recent = selectEvaluations(index, notes, {
      ...defaultState,
      sort: "recent",
    });
    const groups = groupRecentEvaluations(recent);
    expect(groups[0]?.notes[0]?.id).toBe("note-res-rattrapage");
    expect(groups.at(-1)?.notes[0]?.id).toBe("note-res-classique");
  });

  it("indexes 100 UE and 500 evaluations without dropping entries", () => {
    const manyUes = Array.from({ length: 100 }, (_, position) => ({
      ...ues[0]!,
      code: `FICTIF-${String(position).padStart(3, "0")}`,
      title: `UE fictive ${position}`,
      note_count: 5,
    }));
    const manyNotes = manyUes.flatMap((ue, uePosition) =>
      Array.from({ length: 5 }, (_, notePosition) => ({
        ...notes[0]!,
        id: `note-fictive-${uePosition}-${notePosition}`,
        ue_code: ue.code,
        label: `Évaluation fictive ${notePosition}`,
      })),
    );
    const largeIndex = buildResultsIndex(manyUes, manyNotes);
    expect(largeIndex.ueByCode.size).toBe(100);
    expect(
      selectEvaluations(largeIndex, manyNotes, {
        ...defaultState,
        q: "evaluation fictive",
      }),
    ).toHaveLength(500);
  });
});
