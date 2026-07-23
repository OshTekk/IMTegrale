import { ListChecks } from "lucide-react";
import { EmptyState } from "../../components/EmptyState";
import type { NoteItem } from "../../types";
import { selectEvaluations, type ResultsIndex } from "./resultsSelectors";
import type { ResultsState } from "./resultsState";
import { ResultsEvaluationItem } from "./ResultsEvaluationItem";
import { ResultsFilters } from "./ResultsFilters";

export function ResultsEvaluationsView({
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
  const evaluations = selectEvaluations(index, notes, state);
  const showSource = new Set(notes.map((note) => note.source)).size > 1;

  return (
    <div className="results-view">
      <ResultsFilters mode="evaluations" state={state} index={index} notes={notes} onChange={onChange} />
      <header className="results-view-heading">
        <div>
          <span className="section-kicker">Détail PASS</span>
          <h2>Évaluations</h2>
        </div>
        <p>
          {evaluations.length} résultat{evaluations.length > 1 ? "s" : ""}
        </p>
      </header>
      {evaluations.length ? (
        <ul className="results-evaluation-list results-evaluation-list-main">
          {evaluations.map((note) => (
            <ResultsEvaluationItem
              key={note.id}
              note={note}
              ue={index.ueByCode.get(note.ue_code)}
              returnSearch={returnSearch}
              showSource={showSource}
            />
          ))}
        </ul>
      ) : (
        <EmptyState
          icon={<ListChecks size={22} />}
          title="Aucune évaluation"
          detail="Aucun résultat ne correspond aux filtres sélectionnés."
        />
      )}
    </div>
  );
}
