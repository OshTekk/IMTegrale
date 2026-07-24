import type { NoteSimulationAssessmentDraft, NoteSimulationUeDraft } from "../../lib/noteSimulations";

export type NoteSimulationNature = "imported" | "modified" | "simulated";

export function numberOrNull(value: string): number | null {
  return value === "" ? null : Number(value);
}

export function ueNature(ue: NoteSimulationUeDraft): NoteSimulationNature {
  if (!ue.server || ue.server.nature === "simulated") return "simulated";
  const baseline = ue.server.baseline;
  if (!baseline) return "imported";
  const unchanged =
    ue.semester === baseline.semester &&
    (ue.ue_code || null) === baseline.ue_code &&
    ue.title === (baseline.title ?? "") &&
    numberOrNull(ue.credits_ects) === baseline.credits_ects;
  return unchanged ? "imported" : "modified";
}

export function assessmentNature(assessment: NoteSimulationAssessmentDraft): NoteSimulationNature {
  if (!assessment.server || assessment.server.nature === "simulated") return "simulated";
  const baseline = assessment.server.baseline;
  if (!baseline) return "imported";
  const unchanged =
    assessment.label === (baseline.label ?? "") &&
    numberOrNull(assessment.score) === baseline.score &&
    numberOrNull(assessment.coefficient) === baseline.coefficient &&
    assessment.is_resit === Boolean(baseline.is_resit);
  return unchanged ? "imported" : "modified";
}

export function natureLabel(nature: NoteSimulationNature): string {
  if (nature === "imported") return "Officielle importée";
  if (nature === "modified") return "Hypothèse modifiée";
  return "Valeur simulée";
}

export function hasAssessmentConflict(assessment: NoteSimulationAssessmentDraft): boolean {
  return assessment.server?.source?.status === "conflict";
}

export function hasUeConflict(ue: NoteSimulationUeDraft): boolean {
  return ue.server?.source?.status === "conflict" || ue.assessments.some(hasAssessmentConflict);
}

export function initialOpenUes(ues: NoteSimulationUeDraft[], compact: boolean): Set<string> {
  const conflicts = ues.filter(hasUeConflict).map((ue) => ue.clientKey);
  return new Set(compact ? conflicts.slice(0, 1) : conflicts);
}

export function domSafeKey(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]/g, "-");
}

export function sourceLabel(status: "current" | "conflict" | "unavailable" | undefined): string {
  if (status === "conflict") return "Action requise";
  if (status === "unavailable") return "Source indisponible";
  return "Source actuelle";
}
