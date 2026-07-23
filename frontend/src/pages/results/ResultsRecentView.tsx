import { Clock3 } from "lucide-react";
import { EmptyState } from "../../components/EmptyState";
import type { NoteItem } from "../../types";
import { groupRecentEvaluations, selectEvaluations, type ResultsIndex } from "./resultsSelectors";
import type { ResultsState } from "./resultsState";
import { ResultsEvaluationItem } from "./ResultsEvaluationItem";
import { ResultsFilters } from "./ResultsFilters";

export function ResultsRecentView({
  state,
  index,
  notes,
  returnSearch,
  onChange,
}: {
  state: ResultsState;
  index: ResultsIndex;
  notes: readonly NoteItem[];
  returnSearch: string;
  onChange: (patch: Partial<ResultsState>) => void;
}) {
  const recent = selectEvaluations(index, notes, { ...state, q: "", sort: "recent" });
  const groups = groupRecentEvaluations(recent);
  const showSource = new Set(notes.map((note) => note.source)).size > 1;

  return (
    <div className="results-view">
      <ResultsFilters mode="recent" state={state} index={index} notes={notes} onChange={onChange} />
      <header className="results-view-heading">
        <div>
          <span className="section-kicker">Ordre d'import</span>
          <h2>Nouveautés</h2>
        </div>
        <p>Du plus récent au plus ancien</p>
      </header>
      {groups.length ? (
        <div className="results-recent-groups">
          {groups.map((group) => (
            <section key={group.key} aria-labelledby={`results-recent-${group.key}`}>
              <h3 id={`results-recent-${group.key}`}>{group.label}</h3>
              <ul className="results-evaluation-list">
                {group.notes.map((note) => (
                  <ResultsEvaluationItem
                    key={note.id}
                    note={note}
                    ue={index.ueByCode.get(note.ue_code)}
                    returnSearch={returnSearch}
                    showSource={showSource}
                  />
                ))}
              </ul>
            </section>
          ))}
        </div>
      ) : (
        <EmptyState
          icon={<Clock3 size={22} />}
          title="Aucune nouveauté"
          detail="Aucun résultat importé ne correspond aux filtres sélectionnés."
        />
      )}
      <p className="results-detection-note">
        La date affichée correspond à l'import dans IMTégrale, pas à la date de l'évaluation.
      </p>
    </div>
  );
}
