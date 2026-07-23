import type { NoteItem, UeItem } from "../../types";
import type { ResultsState } from "./resultsState";

export interface ResultsIndex {
  ueByCode: Map<string, UeItem>;
  notesByUe: Map<string, NoteItem[]>;
  noteSearchText: Map<string, string>;
  years: string[];
  semesters: string[];
  ueCodes: string[];
}

export interface UeGroup {
  key: string;
  label: string;
  ues: UeItem[];
}

export interface AcademicYearGroup {
  key: string;
  label: string;
  semesters: UeGroup[];
}

export interface RecentGroup {
  key: string;
  label: string;
  notes: NoteItem[];
}

export function normalizeResultsText(value: string): string {
  return value
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLocaleLowerCase("fr")
    .trim();
}

function semesterRank(value: string | null | undefined): number {
  const match = value?.match(/^S(\d+)$/i);
  return match ? Number(match[1]) : Number.MAX_SAFE_INTEGER;
}

function compareText(left: string, right: string): number {
  return left.localeCompare(right, "fr", { sensitivity: "base", numeric: true });
}

function compareNoteIdentity(left: NoteItem, right: NoteItem): number {
  return compareText(left.label, right.label) || compareText(left.id, right.id);
}

export function sortUeEvaluations(notes: readonly NoteItem[]): NoteItem[] {
  return [...notes].sort(
    (left, right) =>
      Number(right.is_resit) - Number(left.is_resit) ||
      right.coefficient - left.coefficient ||
      compareNoteIdentity(left, right),
  );
}

export function buildResultsIndex(ues: readonly UeItem[], notes: readonly NoteItem[]): ResultsIndex {
  const ueByCode = new Map(ues.map((ue) => [ue.code, ue]));
  const notesByUe = new Map<string, NoteItem[]>();
  const noteSearchText = new Map<string, string>();

  for (const note of notes) {
    const grouped = notesByUe.get(note.ue_code) ?? [];
    grouped.push(note);
    notesByUe.set(note.ue_code, grouped);
    const ue = ueByCode.get(note.ue_code);
    noteSearchText.set(note.id, normalizeResultsText(`${note.ue_code} ${ue?.title ?? ""} ${note.label}`));
  }
  for (const [code, grouped] of notesByUe) notesByUe.set(code, sortUeEvaluations(grouped));

  return {
    ueByCode,
    notesByUe,
    noteSearchText,
    years: [...new Set(ues.map((ue) => ue.year).filter(Boolean))].sort(compareText),
    semesters: [
      ...new Set(
        ues.map((ue) => ue.semester).filter((value): value is NonNullable<UeItem["semester"]> => value !== null),
      ),
    ].sort((left, right) => semesterRank(left) - semesterRank(right) || compareText(left, right)),
    ueCodes: [...ueByCode.keys()].sort(compareText),
  };
}

function matchesCommonFilters(ue: UeItem | undefined, note: NoteItem | undefined, state: ResultsState): boolean {
  if (state.year && ue?.year !== state.year) return false;
  if (state.semester && ue?.semester !== state.semester) return false;
  if (state.ue && (note?.ue_code ?? ue?.code) !== state.ue) return false;
  if (note && state.type === "classic" && note.is_resit) return false;
  if (note && state.type === "resit" && !note.is_resit) return false;
  return true;
}

export function selectUes(index: ResultsIndex, state: ResultsState): UeItem[] {
  return [...index.ueByCode.values()]
    .filter((ue) => matchesCommonFilters(ue, undefined, state))
    .sort(
      (left, right) => semesterRank(left.semester) - semesterRank(right.semester) || compareText(left.code, right.code),
    );
}

function compareEvaluationSort(left: NoteItem, right: NoteItem, index: ResultsIndex, state: ResultsState): number {
  const leftUe = index.ueByCode.get(left.ue_code);
  const rightUe = index.ueByCode.get(right.ue_code);
  const stable = () => compareText(left.ue_code, right.ue_code) || compareNoteIdentity(left, right);

  if (state.sort === "semester-ue") {
    return (
      semesterRank(leftUe?.semester) - semesterRank(rightUe?.semester) ||
      compareText(left.ue_code, right.ue_code) ||
      Number(right.is_resit) - Number(left.is_resit) ||
      right.coefficient - left.coefficient ||
      compareNoteIdentity(left, right)
    );
  }
  if (state.sort === "coefficient") return right.coefficient - left.coefficient || stable();
  if (state.sort === "score-desc") return right.score - left.score || stable();
  if (state.sort === "score-asc") return left.score - right.score || stable();
  if (state.sort === "recent") {
    return (
      new Date(right.detected_at).getTime() - new Date(left.detected_at).getTime() || compareText(right.id, left.id)
    );
  }
  return (
    compareText(left.ue_code, right.ue_code) ||
    Number(right.is_resit) - Number(left.is_resit) ||
    right.coefficient - left.coefficient ||
    compareNoteIdentity(left, right)
  );
}

export function selectEvaluations(index: ResultsIndex, notes: readonly NoteItem[], state: ResultsState): NoteItem[] {
  const query = normalizeResultsText(state.q);
  return notes
    .filter((note) => {
      const ue = index.ueByCode.get(note.ue_code);
      return matchesCommonFilters(ue, note, state) && (!query || index.noteSearchText.get(note.id)?.includes(query));
    })
    .sort((left, right) => compareEvaluationSort(left, right, index, state));
}

export function groupUesBySemester(ues: readonly UeItem[]): UeGroup[] {
  const groups = new Map<string, UeItem[]>();
  for (const ue of ues) {
    const key = ue.semester ?? "without-semester";
    const grouped = groups.get(key) ?? [];
    grouped.push(ue);
    groups.set(key, grouped);
  }
  return [...groups.entries()]
    .sort(
      ([left], [right]) =>
        semesterRank(left === "without-semester" ? null : left) -
          semesterRank(right === "without-semester" ? null : right) || compareText(left, right),
    )
    .map(([key, grouped]) => ({
      key,
      label: key === "without-semester" ? "Sans semestre" : key,
      ues: grouped,
    }));
}

export function groupUesByAcademicPeriod(ues: readonly UeItem[]): AcademicYearGroup[] {
  const years = new Map<string, UeItem[]>();
  for (const ue of ues) {
    const key = ue.year || "without-year";
    const grouped = years.get(key) ?? [];
    grouped.push(ue);
    years.set(key, grouped);
  }
  return [...years.entries()]
    .sort(([left], [right]) => compareText(left, right))
    .map(([key, grouped]) => ({
      key,
      label: key === "without-year" ? "Année non renseignée" : key,
      semesters: groupUesBySemester(grouped),
    }));
}

function detectionDay(value: string): { key: string; label: string } {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return { key: "unknown", label: "Date indisponible" };
  const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(
    2,
    "0",
  )}`;
  return {
    key,
    label: new Intl.DateTimeFormat("fr-FR", { dateStyle: "long" }).format(date),
  };
}

export function groupRecentEvaluations(notes: readonly NoteItem[]): RecentGroup[] {
  const groups = new Map<string, RecentGroup>();
  for (const note of notes) {
    const day = detectionDay(note.detected_at);
    const current = groups.get(day.key) ?? { ...day, notes: [] };
    current.notes.push(note);
    groups.set(day.key, current);
  }
  return [...groups.values()];
}
