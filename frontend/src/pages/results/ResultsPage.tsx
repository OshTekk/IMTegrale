import { TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { isPrimaryOwnerSession } from "../../lib/auth";
import { useDashboard, useSession } from "../../lib/queries";
import { ResultsEvaluationsView } from "./ResultsEvaluationsView";
import { ResultsRecentView } from "./ResultsRecentView";
import { buildResultsIndex } from "./resultsSelectors";
import { parseResultsSearch, sanitizeResultsSearch, updateResultsSearch, type ResultsState } from "./resultsState";
import { ResultsSummary } from "./ResultsSummary";
import { ResultsTabs } from "./ResultsTabs";
import { ResultsUeView } from "./ResultsUeView";

export function ResultsPage() {
  const dashboard = useDashboard();
  const session = useSession();
  const [searchParams, setSearchParams] = useSearchParams();
  const search = searchParams.toString();
  const index = useMemo(
    () => buildResultsIndex(dashboard.data?.ues ?? [], dashboard.data?.notes ?? []),
    [dashboard.data?.notes, dashboard.data?.ues],
  );
  const canonicalParams = useMemo(() => {
    if (!dashboard.data) return searchParams;
    return sanitizeResultsSearch(searchParams, {
      years: new Set(index.years),
      semesters: new Set(index.semesters),
      ues: new Set(index.ueCodes),
    });
  }, [dashboard.data, index.semesters, index.ueCodes, index.years, searchParams]);
  const state = parseResultsSearch(canonicalParams).state;
  const canonicalSearch = canonicalParams.toString();
  const pendingParams = useRef(canonicalParams);

  useEffect(() => {
    pendingParams.current = canonicalParams;
    if (!dashboard.data || search === canonicalSearch) return;
    setSearchParams(canonicalParams, { replace: true });
  }, [canonicalParams, canonicalSearch, dashboard.data, search, setSearchParams]);

  const onChange = (patch: Partial<ResultsState>) => {
    const next = updateResultsSearch(pendingParams.current, patch);
    pendingParams.current = next;
    setSearchParams(next, {
      replace: Object.prototype.hasOwnProperty.call(patch, "q"),
    });
  };

  if (dashboard.isPending) {
    return (
      <div className="results-loading" role="status" aria-label="Chargement des résultats" aria-busy="true">
        <div className="skeleton" />
        <div className="skeleton" />
        <div className="skeleton" />
      </div>
    );
  }
  if (dashboard.isError || !dashboard.data) {
    return (
      <section className="error-panel results-error" role="alert">
        <TriangleAlert size={22} aria-hidden="true" />
        <div>
          <h2>Résultats indisponibles</h2>
          <p>{dashboard.error?.message ?? "Impossible de charger les résultats."}</p>
        </div>
        <button className="secondary-button" type="button" onClick={() => dashboard.refetch()}>
          Réessayer
        </button>
      </section>
    );
  }

  const returnSearch = `?${canonicalSearch}`;
  const canDownloadReport = Boolean(session.data && isPrimaryOwnerSession(session.data));

  return (
    <div className="results-page">
      <ResultsSummary summary={dashboard.data.summary} canDownloadReport={canDownloadReport} />
      <ResultsTabs active={state.view} onChange={(view) => onChange({ view })} />

      <section
        id="results-panel-ues"
        role="tabpanel"
        aria-labelledby="results-tab-ues"
        tabIndex={0}
        hidden={state.view !== "ues"}
      >
        {state.view === "ues" && (
          <ResultsUeView
            state={state}
            index={index}
            notes={dashboard.data.notes}
            returnSearch={returnSearch}
            onChange={onChange}
          />
        )}
      </section>
      <section
        id="results-panel-evaluations"
        role="tabpanel"
        aria-labelledby="results-tab-evaluations"
        tabIndex={0}
        hidden={state.view !== "evaluations"}
      >
        {state.view === "evaluations" && (
          <ResultsEvaluationsView
            state={state}
            index={index}
            notes={dashboard.data.notes}
            returnSearch={returnSearch}
            onChange={onChange}
          />
        )}
      </section>
      <section
        id="results-panel-recent"
        role="tabpanel"
        aria-labelledby="results-tab-recent"
        tabIndex={0}
        hidden={state.view !== "recent"}
      >
        {state.view === "recent" && (
          <ResultsRecentView
            state={state}
            index={index}
            notes={dashboard.data.notes}
            returnSearch={returnSearch}
            onChange={onChange}
          />
        )}
      </section>
    </div>
  );
}
