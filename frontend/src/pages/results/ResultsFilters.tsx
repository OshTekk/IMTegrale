import { RotateCcw, Search, SlidersHorizontal } from "lucide-react";
import { useState } from "react";
import { yearLabel } from "../../lib/format";
import type { NoteItem } from "../../types";
import type { ResultsIndex } from "./resultsSelectors";
import type { ResultsState } from "./resultsState";

type FilterMode = "ues" | "evaluations" | "recent";

const sortOptions = [
  { value: "ue-coefficient", label: "UE puis coefficient" },
  { value: "semester-ue", label: "Semestre puis UE" },
  { value: "coefficient", label: "Coefficient décroissant" },
  { value: "score-desc", label: "Note décroissante" },
  { value: "score-asc", label: "Note croissante" },
  { value: "recent", label: "Détection récente" },
] as const;

export function ResultsFilters({
  mode,
  state,
  index,
  notes,
  onChange,
}: {
  mode: FilterMode;
  state: ResultsState;
  index: ResultsIndex;
  notes: readonly NoteItem[];
  onChange: (patch: Partial<ResultsState>) => void;
}) {
  const [open, setOpen] = useState(() => window.matchMedia("(min-width: 761px)").matches);
  const hasClassic = notes.some((note) => !note.is_resit);
  const hasResit = notes.some((note) => note.is_resit);
  const hasTypeFilter = hasClassic && hasResit && mode !== "ues";
  const filtered =
    Boolean(state.year || state.semester || state.ue) ||
    (mode === "evaluations" && Boolean(state.q || state.type !== "all" || state.sort !== "ue-coefficient")) ||
    (mode === "recent" && state.type !== "all");

  const reset = () =>
    onChange({
      year: null,
      semester: null,
      ue: null,
      type: "all",
      sort: "ue-coefficient",
      q: "",
    });

  return (
    <details className="results-filters" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>
        <span>
          <SlidersHorizontal size={17} /> Filtres
        </span>
        {filtered && <small>Actifs</small>}
      </summary>
      <div className="results-filter-fields">
        {mode === "evaluations" && (
          <label className="results-search">
            <span>Rechercher</span>
            <span>
              <Search size={17} />
              <input
                value={state.q}
                onChange={(event) => onChange({ q: event.target.value })}
                placeholder="Code, UE ou évaluation"
                type="search"
              />
            </span>
          </label>
        )}

        {index.years.length > 1 && (
          <label>
            <span>Année</span>
            <select value={state.year ?? ""} onChange={(event) => onChange({ year: event.target.value || null })}>
              <option value="">Toutes les années</option>
              {index.years.map((year) => (
                <option key={year} value={year}>
                  {yearLabel(year)}
                </option>
              ))}
            </select>
          </label>
        )}

        {index.semesters.length > 1 && (
          <label>
            <span>Semestre</span>
            <select
              value={state.semester ?? ""}
              onChange={(event) => onChange({ semester: event.target.value || null })}
            >
              <option value="">Tous les semestres</option>
              {index.semesters.map((semester) => (
                <option key={semester} value={semester}>
                  {semester}
                </option>
              ))}
            </select>
          </label>
        )}

        {index.ueCodes.length > 1 && (
          <label>
            <span>UE</span>
            <select value={state.ue ?? ""} onChange={(event) => onChange({ ue: event.target.value || null })}>
              <option value="">Toutes les UE</option>
              {index.ueCodes.map((code) => (
                <option key={code} value={code}>
                  {code}
                  {index.ueByCode.get(code)?.title ? ` · ${index.ueByCode.get(code)?.title}` : ""}
                </option>
              ))}
            </select>
          </label>
        )}

        {hasTypeFilter && (
          <label>
            <span>Type</span>
            <select
              value={state.type}
              onChange={(event) => onChange({ type: event.target.value as ResultsState["type"] })}
            >
              <option value="all">Tous les types</option>
              <option value="classic">Évaluation classique</option>
              <option value="resit">Rattrapage</option>
            </select>
          </label>
        )}

        {mode === "evaluations" && (
          <label>
            <span>Trier par</span>
            <select
              value={state.sort}
              onChange={(event) => onChange({ sort: event.target.value as ResultsState["sort"] })}
            >
              {sortOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        )}

        {filtered && (
          <button className="secondary-button results-filter-reset" type="button" onClick={reset}>
            <RotateCcw size={16} /> Réinitialiser
          </button>
        )}
      </div>
    </details>
  );
}
