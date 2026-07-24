import { formatNumber, relativeDate } from "../../lib/format";
import { calculateDraftProjection, gradePoints, type SimulationDraftEntry } from "../../lib/simulations";
import type { SimulationSemester } from "../../types";

export type GpaEntryNature = "imported" | "modified" | "simulated";

export function entryNature(entry: SimulationDraftEntry): GpaEntryNature {
  if (!entry.server || entry.server.nature === "simulated") return "simulated";
  const baseline = entry.server.baseline;
  if (!baseline) return "imported";
  const credits = entry.credits_ects === "" ? null : Number(entry.credits_ects);
  const unchanged =
    entry.semester === baseline.semester &&
    (entry.ue_code || null) === baseline.ue_code &&
    entry.title === (baseline.title ?? "") &&
    credits === baseline.credits_ects &&
    entry.grade === baseline.grade;
  return unchanged ? "imported" : "modified";
}

export function natureLabel(nature: GpaEntryNature): string {
  if (nature === "imported") return "Officielle importée";
  if (nature === "modified") return "Hypothèse modifiée";
  return "UE simulée";
}

export function sourceLabel(entry: SimulationDraftEntry): string | null {
  const source = entry.server?.source;
  if (!source) return null;
  const origin =
    source.grade_source === "competences"
      ? "Grade COMPETENCES"
      : source.grade_source === "pass_calculated"
        ? "Grade calculé depuis PASS"
        : "Base académique";
  return source.observed_at ? `${origin} · ${relativeDate(source.observed_at)}` : origin;
}

export function numberOrNull(value: string): number | null {
  return value === "" ? null : Number(value);
}

export function entryIsValid(entry: SimulationDraftEntry): boolean {
  const credits = numberOrNull(entry.credits_ects);
  return (
    Boolean(entry.ue_code.trim() || entry.title.trim()) &&
    (credits === null || (Number.isFinite(credits) && credits > 0 && credits <= 60))
  );
}

export function entryHasConflict(entry: SimulationDraftEntry): boolean {
  return entry.server?.source?.status === "conflict";
}

export function entryIsIncomplete(entry: SimulationDraftEntry): boolean {
  return !entryIsValid(entry) || entry.grade === null || entry.credits_ects === "";
}

export function domSafeKey(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]/g, "-");
}

export function accessibleEntryLabel(entry: SimulationDraftEntry): string {
  const name = entry.title || entry.ue_code || "UE à compléter";
  const grade = entry.grade ? `grade ${entry.grade}` : "grade en attente";
  const credits = entry.credits_ects ? `${formatNumber(Number(entry.credits_ects))} ECTS` : "ECTS non renseignés";
  const points = gradePoints(entry.grade);
  const gpa = points === null ? "points GPA en attente" : `${formatNumber(points)} points GPA`;
  return `Modifier ${name}, ${grade}, ${credits}, ${gpa}`;
}

export function projectionForSemester(entries: SimulationDraftEntry[], semester: "all" | SimulationSemester) {
  const projection = calculateDraftProjection(entries);
  if (semester === "all") return { global: projection, selected: projection };
  return {
    global: projection,
    selected: projection.semesters.find((item) => item.semester === semester) ?? {
      gpa: null,
      creditsIncluded: 0,
      ueCount: 0,
      gradedCount: 0,
      pendingCount: 0,
    },
  };
}
