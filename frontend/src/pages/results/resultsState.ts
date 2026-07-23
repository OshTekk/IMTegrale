export const resultsViews = ["ues", "evaluations", "recent"] as const;
export type ResultsView = (typeof resultsViews)[number];

export const evaluationTypes = ["all", "classic", "resit"] as const;
export type EvaluationType = (typeof evaluationTypes)[number];

export const evaluationSorts = [
  "ue-coefficient",
  "semester-ue",
  "coefficient",
  "score-desc",
  "score-asc",
  "recent",
] as const;
export type EvaluationSort = (typeof evaluationSorts)[number];

export interface ResultsState {
  view: ResultsView;
  year: string | null;
  semester: string | null;
  ue: string | null;
  type: EvaluationType;
  sort: EvaluationSort;
  q: string;
}

export interface ParsedResultsState {
  state: ResultsState;
  invalidKeys: string[];
}

const viewSet = new Set<string>(resultsViews);
const typeSet = new Set<string>(evaluationTypes);
const sortSet = new Set<string>(evaluationSorts);

function optionalParam(params: URLSearchParams, key: string): string | null {
  const value = params.get(key)?.trim();
  return value ? value : null;
}

export function parseResultsSearch(params: URLSearchParams): ParsedResultsState {
  const invalidKeys: string[] = [];
  const rawView = optionalParam(params, "view");
  const view = rawView && viewSet.has(rawView) ? (rawView as ResultsView) : "ues";
  if (rawView && !viewSet.has(rawView)) invalidKeys.push("view");

  const rawType = optionalParam(params, "type");
  const type = rawType && typeSet.has(rawType) ? (rawType as EvaluationType) : "all";
  if (rawType && !typeSet.has(rawType)) invalidKeys.push("type");

  const rawSort = optionalParam(params, "sort");
  const sort = rawSort && sortSet.has(rawSort) ? (rawSort as EvaluationSort) : "ue-coefficient";
  if (rawSort && !sortSet.has(rawSort)) invalidKeys.push("sort");

  return {
    state: {
      view,
      year: optionalParam(params, "year"),
      semester: optionalParam(params, "semester"),
      ue: optionalParam(params, "ue"),
      type,
      sort,
      q: optionalParam(params, "q") ?? "",
    },
    invalidKeys,
  };
}

function setOptional(params: URLSearchParams, key: string, value: string | null): void {
  if (value) params.set(key, value);
  else params.delete(key);
}

export function resultsSearchForState(state: ResultsState): URLSearchParams {
  const params = new URLSearchParams();
  params.set("view", state.view);
  setOptional(params, "year", state.year);
  setOptional(params, "semester", state.semester);
  setOptional(params, "ue", state.ue);

  if (state.view === "evaluations") {
    if (state.type !== "all") params.set("type", state.type);
    if (state.sort !== "ue-coefficient") params.set("sort", state.sort);
    setOptional(params, "q", state.q.trim() || null);
  } else if (state.view === "recent" && state.type !== "all") {
    params.set("type", state.type);
  }
  return params;
}

export function updateResultsSearch(current: URLSearchParams, patch: Partial<ResultsState>): URLSearchParams {
  const next = { ...parseResultsSearch(current).state, ...patch };
  return resultsSearchForState(next);
}

export function legacyResultsSearch(search: string, view: ResultsView): string {
  const params = new URLSearchParams(search);
  params.set("view", view);
  return `?${params.toString()}`;
}

export function sanitizeResultsSearch(
  current: URLSearchParams,
  allowed: {
    years: ReadonlySet<string>;
    semesters: ReadonlySet<string>;
    ues: ReadonlySet<string>;
  },
): URLSearchParams {
  const { state } = parseResultsSearch(current);
  const sanitized: ResultsState = {
    ...state,
    year: state.year && allowed.years.has(state.year) ? state.year : null,
    semester: state.semester && allowed.semesters.has(state.semester) ? state.semester : null,
    ue: state.ue && allowed.ues.has(state.ue) ? state.ue : null,
  };
  return resultsSearchForState(sanitized);
}
