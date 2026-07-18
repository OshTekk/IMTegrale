import type {
  SimulationEntry,
  SimulationGrade,
  SimulationScenario,
  SimulationSemester,
} from "../types";

export const SIMULATION_GRADES: Array<{ grade: SimulationGrade; points: number }> = [
  { grade: "A", points: 4 },
  { grade: "B", points: 3.8 },
  { grade: "C", points: 3.5 },
  { grade: "D", points: 3 },
  { grade: "E", points: 2.5 },
  { grade: "FX", points: 0 },
  { grade: "F", points: 0 },
];

export const SIMULATION_SEMESTERS: SimulationSemester[] = [
  "S1",
  "S2",
  "S3",
  "S4",
  "S5",
  "S6",
  "S7",
  "S8",
  "S9",
  "S10",
];

export interface SimulationDraftEntry {
  clientKey: string;
  id: string | null;
  semester: SimulationSemester | null;
  ue_code: string;
  title: string;
  credits_ects: string;
  grade: SimulationGrade | null;
  server: SimulationEntry | null;
}

export interface SimulationDraft {
  id: string;
  name: string;
  version: number;
  entries: SimulationDraftEntry[];
}

export interface DraftProjection {
  gpa: number | null;
  creditsEntered: number;
  creditsIncluded: number;
  ueCount: number;
  gradedCount: number;
  pendingCount: number;
  completionRate: number;
  semesters: Array<{
    semester: SimulationSemester;
    gpa: number | null;
    creditsIncluded: number;
    ueCount: number;
    gradedCount: number;
    pendingCount: number;
  }>;
}

function decimalInput(value: number | null): string {
  return value === null ? "" : String(value);
}

export function scenarioToDraft(scenario: SimulationScenario): SimulationDraft {
  return {
    id: scenario.id,
    name: scenario.name,
    version: scenario.version,
    entries: scenario.entries.map((entry) => ({
      clientKey: entry.id,
      id: entry.id,
      semester: entry.semester,
      ue_code: entry.ue_code ?? "",
      title: entry.title,
      credits_ects: decimalInput(entry.credits_ects),
      grade: entry.grade,
      server: entry,
    })),
  };
}

export function createDraftEntry(semester: SimulationSemester | null): SimulationDraftEntry {
  return {
    clientKey: `draft:${crypto.randomUUID()}`,
    id: null,
    semester,
    ue_code: "",
    title: "",
    credits_ects: "",
    grade: null,
    server: null,
  };
}

export function simulationPayload(draft: SimulationDraft) {
  return {
    version: draft.version,
    name: draft.name.trim(),
    entries: draft.entries.map((entry) => ({
      ...(entry.id ? { id: entry.id } : {}),
      semester: entry.semester,
      ue_code: entry.ue_code.trim() || null,
      title: entry.title.trim() || null,
      credits_ects: entry.credits_ects === "" ? null : Number(entry.credits_ects),
      grade: entry.grade,
    })),
  };
}

export function draftIsValid(draft: SimulationDraft): boolean {
  if (!draft.name.trim()) return false;
  return draft.entries.every((entry) => {
    const credits = entry.credits_ects === "" ? null : Number(entry.credits_ects);
    const identityPresent = Boolean(entry.ue_code.trim() || entry.title.trim());
    const creditsValid = credits === null || (Number.isFinite(credits) && credits > 0 && credits <= 60);
    return identityPresent && creditsValid;
  });
}

export function mergeSavedIds(
  current: SimulationDraft,
  saved: SimulationScenario,
  sentKeys: string[],
): SimulationDraft {
  const savedByClientKey = new Map(
    saved.entries.map((entry, index) => [sentKeys[index], entry] as const),
  );
  return {
    ...current,
    version: saved.version,
    entries: current.entries.map((entry) => {
      const persisted = savedByClientKey.get(entry.clientKey);
      return persisted
        ? { ...entry, id: persisted.id, server: persisted }
        : entry;
    }),
  };
}

export function gradePoints(grade: SimulationGrade | null): number | null {
  return SIMULATION_GRADES.find((item) => item.grade === grade)?.points ?? null;
}

function roundProjection(value: number): number {
  return Math.round((value + Number.EPSILON) * 100) / 100;
}

function aggregateDraft(entries: SimulationDraftEntry[]) {
  let points = 0;
  let creditsEntered = 0;
  let creditsIncluded = 0;
  let gradedCount = 0;
  let pendingCount = 0;
  for (const entry of entries) {
    const credits = Number(entry.credits_ects);
    const creditsValid = entry.credits_ects !== "" && Number.isFinite(credits) && credits > 0;
    if (creditsValid) creditsEntered += credits;
    if (entry.grade === null) {
      pendingCount += 1;
      continue;
    }
    gradedCount += 1;
    if (!creditsValid) continue;
    points += (gradePoints(entry.grade) ?? 0) * credits;
    creditsIncluded += credits;
  }
  return {
    gpa: creditsIncluded ? roundProjection(points / creditsIncluded) : null,
    creditsEntered: roundProjection(creditsEntered),
    creditsIncluded: roundProjection(creditsIncluded),
    ueCount: entries.length,
    gradedCount,
    pendingCount,
  };
}

export function calculateDraftProjection(entries: SimulationDraftEntry[]): DraftProjection {
  const global = aggregateDraft(entries);
  const semesterValues = new Map<SimulationSemester, SimulationDraftEntry[]>();
  for (const entry of entries) {
    if (!entry.semester) continue;
    const values = semesterValues.get(entry.semester) ?? [];
    values.push(entry);
    semesterValues.set(entry.semester, values);
  }
  const semesters = [...semesterValues.entries()]
    .sort(([left], [right]) => Number(left.slice(1)) - Number(right.slice(1)))
    .map(([semester, semesterEntries]) => ({
      semester,
      ...aggregateDraft(semesterEntries),
    }));
  return {
    ...global,
    completionRate: global.ueCount ? Math.round((global.gradedCount / global.ueCount) * 100) : 0,
    semesters,
  };
}

export function entryDisplayName(entry: SimulationEntry | SimulationDraftEntry): string {
  const code = entry.ue_code?.trim();
  const title = entry.title.trim();
  if (code && title && title !== code) return `${code} · ${title}`;
  return code || title || "UE sans intitulé";
}
