import { Plus } from "lucide-react";
import type { SimulationSemester } from "../../types";

export function GpaSimulationSemesterFilter({
  semester,
  semesters,
  compact,
  visibleCount,
  disabled,
  limitReached,
  onChange,
  onAdd,
}: {
  semester: "all" | SimulationSemester;
  semesters: SimulationSemester[];
  compact: boolean;
  visibleCount: number;
  disabled: boolean;
  limitReached: boolean;
  onChange: (semester: "all" | SimulationSemester) => void;
  onAdd: () => void;
}) {
  return (
    <section className="gpa-semester-toolbar" aria-label="Filtrer et ajouter des UE">
      {compact ? (
        <label className="gpa-semester-select">
          <span>Semestre</span>
          <select value={semester} onChange={(event) => onChange(event.target.value as "all" | SimulationSemester)}>
            <option value="all">Tous les semestres</option>
            {semesters.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <div className="gpa-semester-tabs" role="tablist" aria-label="Filtrer par semestre">
          <button
            type="button"
            role="tab"
            aria-selected={semester === "all"}
            className={semester === "all" ? "active" : ""}
            onClick={() => onChange("all")}
          >
            Tous
          </button>
          {semesters.map((item) => (
            <button
              key={item}
              type="button"
              role="tab"
              aria-selected={semester === item}
              className={semester === item ? "active" : ""}
              onClick={() => onChange(item)}
            >
              {item}
            </button>
          ))}
        </div>
      )}
      <span className="gpa-visible-count">
        {visibleCount} UE affichée{visibleCount === 1 ? "" : "s"}
      </span>
      <button
        id="gpa-add-ue"
        className="primary-button gpa-add-ue"
        type="button"
        onClick={onAdd}
        disabled={disabled || limitReached}
        title={limitReached ? "Limite de 120 UE atteinte" : undefined}
      >
        <Plus size={17} />
        Ajouter une UE{semester === "all" ? "" : ` en ${semester}`}
      </button>
    </section>
  );
}
