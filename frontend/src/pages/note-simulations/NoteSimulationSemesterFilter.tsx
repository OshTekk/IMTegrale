import { Plus } from "lucide-react";
import type { SimulationSemester } from "../../types";

export function NoteSimulationSemesterFilter({
  value,
  semesters,
  visibleCount,
  compact,
  canAdd,
  onChange,
  onAdd,
}: {
  value: "all" | SimulationSemester;
  semesters: SimulationSemester[];
  visibleCount: number;
  compact: boolean;
  canAdd: boolean;
  onChange: (value: "all" | SimulationSemester) => void;
  onAdd: () => void;
}) {
  return (
    <div className="note-workbench-toolbar">
      {compact ? (
        <label className="note-semester-select">
          <span>Semestre</span>
          <select value={value} onChange={(event) => onChange(event.target.value as "all" | SimulationSemester)}>
            <option value="all">Tous les semestres</option>
            {semesters.map((semester) => (
              <option key={semester} value={semester}>
                {semester}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <div className="note-semester-tabs" role="tablist" aria-label="Filtrer par semestre">
          <button
            type="button"
            role="tab"
            aria-selected={value === "all"}
            className={value === "all" ? "active" : ""}
            onClick={() => onChange("all")}
          >
            Tous
          </button>
          {semesters.map((semester) => (
            <button
              key={semester}
              type="button"
              role="tab"
              aria-selected={value === semester}
              className={value === semester ? "active" : ""}
              onClick={() => onChange(semester)}
            >
              {semester}
            </button>
          ))}
        </div>
      )}
      <span className="note-visible-count">
        {visibleCount} UE affichée{visibleCount === 1 ? "" : "s"}
      </span>
      <button id="note-add-ue" className="primary-button note-add-ue" type="button" onClick={onAdd} disabled={!canAdd}>
        <Plus size={17} />
        Ajouter une UE{value === "all" ? "" : ` en ${value}`}
      </button>
    </div>
  );
}
