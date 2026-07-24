import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeftRight, Check, Info, LoaderCircle } from "lucide-react";
import { EmptyState } from "../../components/EmptyState";
import { GradeBadge } from "../../components/GradeBadge";
import { Modal } from "../../components/Modal";
import { simulationsSimulationCompare } from "../../generated/api/sdk.gen";
import { formatNumber } from "../../lib/format";
import { apiData, throwOnApiError } from "../../lib/generatedApi";
import { queryKeys } from "../../lib/queries";
import { entryDisplayName } from "../../lib/simulations";
import type { SimulationScenarioSummary } from "../../types";

export function GpaSimulationComparison({
  open,
  accountId,
  left,
  scenarios,
  rightId,
  setRightId,
  onClose,
}: {
  open: boolean;
  accountId: string;
  left: SimulationScenarioSummary;
  scenarios: SimulationScenarioSummary[];
  rightId: string;
  setRightId: (id: string) => void;
  onClose: () => void;
}) {
  const comparison = useQuery({
    queryKey: [...queryKeys.simulations(accountId), "compare", left.id, rightId],
    queryFn: () =>
      apiData(
        simulationsSimulationCompare({
          query: { left_id: left.id, right_id: rightId },
          throwOnError: throwOnApiError,
        }),
      ),
    enabled: open && Boolean(rightId) && rightId !== left.id,
    staleTime: 0,
  });
  const data = comparison.data;
  return (
    <Modal
      open={open}
      title="Comparer deux projections GPA"
      description="Les écarts décrivent uniquement les hypothèses de ces scénarios."
      onClose={onClose}
      size="large"
      className="gpa-comparison-modal"
    >
      <div className="gpa-compare-controls">
        <label>
          <span>Scénario de référence</span>
          <input value={left.name} disabled />
        </label>
        <ArrowLeftRight size={18} aria-hidden="true" />
        <label>
          <span>Comparer avec</span>
          <select value={rightId} onChange={(event) => setRightId(event.target.value)}>
            {scenarios
              .filter((item) => item.id !== left.id)
              .map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
          </select>
        </label>
      </div>
      {comparison.isPending ? (
        <div className="simulation-compare-loading" aria-busy="true">
          <LoaderCircle className="spin" size={21} /> Calcul de la comparaison…
        </div>
      ) : comparison.isError ? (
        <div className="inline-warning">
          <AlertTriangle size={17} /> {comparison.error.message}
        </div>
      ) : (
        data && (
          <div className="gpa-comparison">
            <section className="gpa-comparison-score" aria-label="GPA des scénarios">
              <div>
                <span>{data.left.name}</span>
                <strong>{formatNumber(data.left.result.gpa)}</strong>
                <small>GPA / 4</small>
              </div>
              <div className="is-delta">
                <span>Écart</span>
                <strong>
                  {data.gpa_delta === null ? "—" : `${data.gpa_delta > 0 ? "+" : ""}${formatNumber(data.gpa_delta)}`}
                </strong>
                <small>point{Math.abs(data.gpa_delta ?? 0) > 1 ? "s" : ""}</small>
              </div>
              <div>
                <span>{data.right.name}</span>
                <strong>{formatNumber(data.right.result.gpa)}</strong>
                <small>GPA / 4</small>
              </div>
            </section>
            <section className="gpa-comparison-differences" aria-label="Différences">
              <header>
                <strong>
                  {data.differences.length} différence{data.differences.length === 1 ? "" : "s"}
                </strong>
                <span>UE, grade ou crédits</span>
              </header>
              {data.differences.length ? (
                data.differences.map((difference) => {
                  const representative = difference.right ?? difference.left;
                  return (
                    <article key={difference.lineage_key}>
                      <div>
                        <strong>{representative ? entryDisplayName(representative) : "UE"}</strong>
                        <small>
                          {difference.kind === "left_only"
                            ? `Uniquement dans ${data.left.name}`
                            : difference.kind === "right_only"
                              ? `Uniquement dans ${data.right.name}`
                              : difference.fields.join(" · ")}
                        </small>
                      </div>
                      <div className="gpa-comparison-values">
                        <span>
                          <b>{data.left.name}</b>
                          {difference.left ? (
                            <>
                              <GradeBadge grade={difference.left.grade} />
                              <small>{formatNumber(difference.left.credits_ects, " ECTS")}</small>
                            </>
                          ) : (
                            <i>Absente</i>
                          )}
                        </span>
                        <span>
                          <b>{data.right.name}</b>
                          {difference.right ? (
                            <>
                              <GradeBadge grade={difference.right.grade} />
                              <small>{formatNumber(difference.right.credits_ects, " ECTS")}</small>
                            </>
                          ) : (
                            <i>Absente</i>
                          )}
                        </span>
                      </div>
                    </article>
                  );
                })
              ) : (
                <EmptyState
                  icon={<Check size={20} />}
                  title="Aucun écart"
                  detail="Ces deux simulations contiennent exactement les mêmes hypothèses."
                />
              )}
            </section>
            <div className="simulation-comparison-method">
              <Info size={16} />
              <span>
                <strong>Règle IMTégrale {data.formula.version}</strong> · GPA pondéré par ECTS, arrondi au centième. Les
                UE sans grade ou sans ECTS sont exclues.
              </span>
            </div>
          </div>
        )
      )}
      <footer className="modal-actions gpa-comparison-actions">
        <button className="primary-button" type="button" onClick={onClose}>
          Fermer
        </button>
      </footer>
    </Modal>
  );
}
