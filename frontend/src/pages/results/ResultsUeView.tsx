import { BookOpenCheck } from "lucide-react";
import { EmptyState } from "../../components/EmptyState";
import { yearLabel } from "../../lib/format";
import type { NoteItem } from "../../types";
import { groupUesByAcademicPeriod, selectUes, type ResultsIndex } from "./resultsSelectors";
import type { ResultsState } from "./resultsState";
import { ResultsFilters } from "./ResultsFilters";
import { ResultsUeCard } from "./ResultsUeCard";

export function ResultsUeView({
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
  const selectedUes = selectUes(index, state);
  const years = groupUesByAcademicPeriod(selectedUes);
  const showEvaluationSource = new Set(notes.map((note) => note.source)).size > 1;

  return (
    <div className="results-view">
      <ResultsFilters mode="ues" state={state} index={index} notes={notes} onChange={onChange} />
      <header className="results-view-heading">
        <div>
          <span className="section-kicker">Lecture académique</span>
          <h2>Unités d'enseignement</h2>
        </div>
        <p>
          {selectedUes.length} UE · {selectedUes.filter((ue) => ue.validated).length} validées
        </p>
      </header>

      {years.length ? (
        <div className="results-academic-years">
          {years.map((year) => (
            <section key={year.key} className="results-year-group" aria-labelledby={`results-year-${year.key}`}>
              <header>
                <h3 id={`results-year-${year.key}`}>
                  {year.key === "without-year" ? year.label : yearLabel(year.label)}
                </h3>
                <span>{year.semesters.reduce((total, semester) => total + semester.ues.length, 0)} UE</span>
              </header>
              {year.semesters.map((semester) => (
                <section
                  key={semester.key}
                  className="results-semester-group"
                  aria-labelledby={`results-semester-${year.key}-${semester.key}`}
                >
                  <div className="results-semester-heading">
                    <h4 id={`results-semester-${year.key}-${semester.key}`}>{semester.label}</h4>
                    <span>
                      {semester.ues.length} UE · {semester.ues.reduce((total, ue) => total + (ue.credits_ects ?? 0), 0)}{" "}
                      ECTS alloués
                    </span>
                  </div>
                  <div className="results-ue-grid">
                    {semester.ues.map((ue) => (
                      <ResultsUeCard
                        key={ue.code}
                        ue={ue}
                        notes={index.notesByUe.get(ue.code) ?? []}
                        returnSearch={returnSearch}
                        showEvaluationSource={showEvaluationSource}
                      />
                    ))}
                  </div>
                </section>
              ))}
            </section>
          ))}
        </div>
      ) : (
        <EmptyState
          icon={<BookOpenCheck size={22} />}
          title="Aucune UE"
          detail="Aucune unité d'enseignement ne correspond aux filtres sélectionnés."
        />
      )}
    </div>
  );
}
