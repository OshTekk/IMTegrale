import type {
  NoteSimulationAssessment,
  NoteSimulationScenario,
  NoteSimulationUe,
  SimulationGrade,
  SimulationSemester,
} from "../types";
import { gradePoints, SIMULATION_SEMESTERS } from "./simulations";

export interface NoteSimulationAssessmentDraft {
  clientKey: string;
  id: string | null;
  label: string;
  score: string;
  coefficient: string;
  is_resit: boolean;
  server: NoteSimulationAssessment | null;
}

export interface NoteSimulationUeDraft {
  clientKey: string;
  id: string | null;
  semester: SimulationSemester | null;
  ue_code: string;
  title: string;
  credits_ects: string;
  assessments: NoteSimulationAssessmentDraft[];
  server: NoteSimulationUe | null;
}

export interface NoteSimulationDraft {
  id: string;
  name: string;
  version: number;
  ues: NoteSimulationUeDraft[];
}

export interface NoteUeDraftProjection {
  average: number | null;
  grade: SimulationGrade | null;
  gpaPoints: number | null;
  usedResit: boolean;
  coefficientTotal: number;
  assessmentCount: number;
  scoredCount: number;
  pendingCount: number;
}

export interface NoteDraftAggregate {
  average: number | null;
  gpa: number | null;
  creditsEntered: number;
  creditsIncluded: number;
  ueCount: number;
  calculatedUeCount: number;
  assessmentCount: number;
  scoredCount: number;
  pendingCount: number;
  missingEctsCount: number;
  completionRate: number;
  semesters: Array<{
    semester: SimulationSemester;
    average: number | null;
    gpa: number | null;
    creditsIncluded: number;
    ueCount: number;
    calculatedUeCount: number;
    assessmentCount: number;
    scoredCount: number;
    pendingCount: number;
  }>;
}

export interface NoteSimulationSentKeys {
  ueKey: string;
  assessmentKeys: string[];
}

function decimalInput(value: number | null): string {
  return value === null ? "" : String(value);
}

function round(value: number): number {
  return Math.round((value + Number.EPSILON) * 100) / 100;
}

function isFiniteInRange(value: string, minimum: number, maximum: number): boolean {
  if (!value.trim()) return false;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= minimum && parsed <= maximum;
}

export function noteScenarioToDraft(scenario: NoteSimulationScenario): NoteSimulationDraft {
  return {
    id: scenario.id,
    name: scenario.name,
    version: scenario.version,
    ues: scenario.ues.map((ue) => ({
      clientKey: ue.id,
      id: ue.id,
      semester: ue.semester,
      ue_code: ue.ue_code ?? "",
      title: ue.title,
      credits_ects: decimalInput(ue.credits_ects),
      assessments: ue.assessments.map((assessment) => ({
        clientKey: assessment.id,
        id: assessment.id,
        label: assessment.label,
        score: decimalInput(assessment.score),
        coefficient: decimalInput(assessment.coefficient),
        is_resit: assessment.is_resit,
        server: assessment,
      })),
      server: ue,
    })),
  };
}

export function createNoteAssessment(
  label = "Nouvelle évaluation",
): NoteSimulationAssessmentDraft {
  return {
    clientKey: `assessment:${crypto.randomUUID()}`,
    id: null,
    label,
    score: "",
    coefficient: "1",
    is_resit: false,
    server: null,
  };
}

export function createNoteUe(
  semester: SimulationSemester | null,
): NoteSimulationUeDraft {
  return {
    clientKey: `ue:${crypto.randomUUID()}`,
    id: null,
    semester,
    ue_code: "",
    title: "",
    credits_ects: "",
    assessments: [createNoteAssessment()],
    server: null,
  };
}

export function noteSimulationPayload(draft: NoteSimulationDraft) {
  return {
    version: draft.version,
    name: draft.name.trim(),
    ues: draft.ues.map((ue) => ({
      ...(ue.id ? { id: ue.id } : {}),
      semester: ue.semester,
      ue_code: ue.ue_code.trim() || null,
      title: ue.title.trim() || null,
      credits_ects: ue.credits_ects === "" ? null : Number(ue.credits_ects),
      assessments: ue.assessments.map((assessment) => ({
        ...(assessment.id ? { id: assessment.id } : {}),
        label: assessment.label.trim(),
        score: assessment.score === "" ? null : Number(assessment.score),
        coefficient: Number(assessment.coefficient),
        is_resit: assessment.is_resit,
      })),
    })),
  };
}

export function noteDraftIsValid(draft: NoteSimulationDraft): boolean {
  if (!draft.name.trim() || draft.ues.length > 120) return false;
  let assessmentCount = 0;
  for (const ue of draft.ues) {
    if (!ue.ue_code.trim() && !ue.title.trim()) return false;
    if (ue.credits_ects && !isFiniteInRange(ue.credits_ects, Number.EPSILON, 60)) {
      return false;
    }
    if (ue.assessments.length > 60) return false;
    assessmentCount += ue.assessments.length;
    for (const assessment of ue.assessments) {
      if (!assessment.label.trim()) return false;
      if (assessment.score && !isFiniteInRange(assessment.score, 0, 20)) return false;
      if (!isFiniteInRange(assessment.coefficient, Number.EPSILON, 100)) return false;
    }
  }
  return assessmentCount <= 600;
}

export function noteSimulationSentKeys(
  draft: NoteSimulationDraft,
): NoteSimulationSentKeys[] {
  return draft.ues.map((ue) => ({
    ueKey: ue.clientKey,
    assessmentKeys: ue.assessments.map((assessment) => assessment.clientKey),
  }));
}

export function mergeSavedNoteIds(
  current: NoteSimulationDraft,
  saved: NoteSimulationScenario,
  sent: NoteSimulationSentKeys[],
): NoteSimulationDraft {
  const savedByClientKey = new Map(
    saved.ues.map((ue, index) => [sent[index]?.ueKey, { ue, keys: sent[index] }] as const),
  );
  return {
    ...current,
    version: saved.version,
    ues: current.ues.map((ue) => {
      const persisted = savedByClientKey.get(ue.clientKey);
      if (!persisted) return ue;
      const assessmentsByClientKey = new Map(
        persisted.ue.assessments.map((assessment, index) => [
          persisted.keys?.assessmentKeys[index],
          assessment,
        ] as const),
      );
      return {
        ...ue,
        id: persisted.ue.id,
        server: persisted.ue,
        assessments: ue.assessments.map((assessment) => {
          const savedAssessment = assessmentsByClientKey.get(assessment.clientKey);
          return savedAssessment
            ? { ...assessment, id: savedAssessment.id, server: savedAssessment }
            : assessment;
        }),
      };
    }),
  };
}

function gradeForAverage(
  average: number | null,
  usedResit: boolean,
): SimulationGrade | null {
  if (average === null) return null;
  if (usedResit && average >= 10) return "E";
  if (average >= 17) return "A";
  if (average >= 14) return "B";
  if (average >= 12) return "C";
  if (average >= 10) return "D";
  if (average >= 5) return "FX";
  return "F";
}

export function calculateNoteUeProjection(
  ue: NoteSimulationUeDraft,
): NoteUeDraftProjection {
  const scored = ue.assessments.filter((assessment) => assessment.score !== "");
  const resits = scored.filter((assessment) => assessment.is_resit);
  const normal = scored.filter((assessment) => !assessment.is_resit);
  let average: number | null = null;
  let rawAverage: number | null = null;
  let coefficientTotal = 0;
  if (resits.length) {
    const latest = resits.at(-1)!;
    rawAverage = Number(latest.score);
    average = round(rawAverage);
    coefficientTotal = Number(latest.coefficient);
  } else if (normal.length) {
    let weighted = 0;
    for (const assessment of normal) {
      const coefficient = Number(assessment.coefficient);
      weighted += Number(assessment.score) * coefficient;
      coefficientTotal += coefficient;
    }
    rawAverage = coefficientTotal ? weighted / coefficientTotal : null;
    average = rawAverage === null ? null : round(rawAverage);
  }
  const grade = gradeForAverage(rawAverage, Boolean(resits.length));
  return {
    average,
    grade,
    gpaPoints: gradePoints(grade),
    usedResit: Boolean(resits.length),
    coefficientTotal: round(coefficientTotal),
    assessmentCount: ue.assessments.length,
    scoredCount: scored.length,
    pendingCount: ue.assessments.length - scored.length,
  };
}

function aggregateUes(ues: NoteSimulationUeDraft[]) {
  let averageTotal = 0;
  let gpaTotal = 0;
  let creditsIncluded = 0;
  let calculatedUeCount = 0;
  let assessmentCount = 0;
  let scoredCount = 0;
  let pendingCount = 0;
  for (const ue of ues) {
    const projection = calculateNoteUeProjection(ue);
    assessmentCount += projection.assessmentCount;
    scoredCount += projection.scoredCount;
    pendingCount += projection.pendingCount;
    if (projection.average === null) continue;
    calculatedUeCount += 1;
    const credits = Number(ue.credits_ects);
    if (!ue.credits_ects || !Number.isFinite(credits) || credits <= 0) continue;
    averageTotal += projection.average * credits;
    gpaTotal += (projection.gpaPoints ?? 0) * credits;
    creditsIncluded += credits;
  }
  return {
    average: creditsIncluded ? round(averageTotal / creditsIncluded) : null,
    gpa: creditsIncluded ? round(gpaTotal / creditsIncluded) : null,
    creditsIncluded: round(creditsIncluded),
    ueCount: ues.length,
    calculatedUeCount,
    assessmentCount,
    scoredCount,
    pendingCount,
  };
}

export function calculateNoteDraftProjection(
  ues: NoteSimulationUeDraft[],
): NoteDraftAggregate {
  const global = aggregateUes(ues);
  const creditsEntered = ues.reduce((total, ue) => {
    const credits = Number(ue.credits_ects);
    return total + (ue.credits_ects && Number.isFinite(credits) && credits > 0 ? credits : 0);
  }, 0);
  const missingEctsCount = ues.filter((ue) => (
    calculateNoteUeProjection(ue).average !== null && !ue.credits_ects
  )).length;
  const bySemester = new Map<SimulationSemester, NoteSimulationUeDraft[]>();
  for (const ue of ues) {
    if (!ue.semester) continue;
    const values = bySemester.get(ue.semester) ?? [];
    values.push(ue);
    bySemester.set(ue.semester, values);
  }
  return {
    ...global,
    creditsEntered: round(creditsEntered),
    missingEctsCount,
    completionRate: global.assessmentCount
      ? Math.round((global.scoredCount / global.assessmentCount) * 100)
      : 0,
    semesters: SIMULATION_SEMESTERS
      .filter((semester) => bySemester.has(semester))
      .map((semester) => ({ semester, ...aggregateUes(bySemester.get(semester) ?? []) })),
  };
}

export function noteUeDisplayName(ue: Pick<NoteSimulationUe, "ue_code" | "title">): string {
  return ue.title || ue.ue_code || "UE sans intitulé";
}
