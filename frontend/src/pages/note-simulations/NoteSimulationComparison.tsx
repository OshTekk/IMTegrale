import { AlertTriangle, ArrowLeftRight, Check, Info, LoaderCircle } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { EmptyState } from "../../components/EmptyState";
import { Modal } from "../../components/Modal";
import { noteSimulationsScenarioCompare } from "../../generated/api/sdk.gen";
import { formatNumber } from "../../lib/format";
import { apiData, throwOnApiError } from "../../lib/generatedApi";
import { noteUeDisplayName } from "../../lib/noteSimulations";
import { queryKeys } from "../../lib/queries";
import type { NoteSimulationScenarioSummary } from "../../types";

const comparisonFieldLabels = {
  presence: "Présence",
  semester: "Semestre",
  ue: "UE",
  credits_ects: "ECTS",
  assessments: "Évaluations",
} as const;

export function NoteSimulationComparison({
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
  left: NoteSimulationScenarioSummary;
  scenarios: NoteSimulationScenarioSummary[];
  rightId: string;
  setRightId: (id: string) => void;
  onClose: () => void;
}) {
  const comparison = useQuery({
    queryKey: [...queryKeys.noteSimulations(accountId), "compare", left.id, rightId],
    queryFn: () =>
      apiData(
        noteSimulationsScenarioCompare({
          query: { left_id: left.id, right_id: rightId },
          throwOnError: throwOnApiError,
        }),
      ),
    enabled: open && Boolean(rightId) && rightId !== left.id,
    staleTime: 0,
  });
  const data = comparison.data;
  const warningGroups = data ? [data.left, data.right].filter((item) => item.result.warnings.length > 0) : [];

  return (
    <Modal
      open={open}
      title="Comparer deux simulations de notes"
      description="Mesure l’effet exact de tes hypothèses sur la moyenne et le GPA dérivé."
      onClose={onClose}
      size="large"
      className="note-comparison-modal"
    >
      <div className="simulation-compare-controls">
        <label>
          <span>Scénario de référence</span>
          <input value={left.name} disabled />
        </label>
        <ArrowLeftRight size={18} />
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
        <div className="simulation-compare-loading">
          <LoaderCircle className="spin" size={21} /> Calcul de la comparaison…
        </div>
      ) : comparison.isError ? (
        <div className="inline-warning">
          <AlertTriangle size={17} /> {comparison.error.message}
        </div>
      ) : (
        data && (
          <div className="simulation-comparison note-simulation-comparison">
            <div className="simulation-comparison-score">
              <div>
                <span>{data.left.name}</span>
                <strong>{formatNumber(data.left.result.average)}</strong>
                <small>moyenne /20</small>
              </div>
              <div
                className={
                  data.average_delta === null
                    ? "neutral"
                    : data.average_delta > 0
                      ? "positive"
                      : data.average_delta < 0
                        ? "negative"
                        : "neutral"
                }
              >
                <span>Écart</span>
                <strong>
                  {data.average_delta === null
                    ? "—"
                    : `${data.average_delta > 0 ? "+" : ""}${formatNumber(data.average_delta)}`}
                </strong>
                <small>point{Math.abs(data.average_delta ?? 0) > 1 ? "s" : ""}</small>
              </div>
              <div>
                <span>{data.right.name}</span>
                <strong>{formatNumber(data.right.result.average)}</strong>
                <small>moyenne /20</small>
              </div>
            </div>
            <div className="note-comparison-gpa">
              <span>GPA dérivé</span>
              <strong>
                {formatNumber(data.left.result.gpa)} <ArrowLeftRight size={14} /> {formatNumber(data.right.result.gpa)}
              </strong>
              <small>
                {data.gpa_delta === null
                  ? "Écart indisponible"
                  : `${data.gpa_delta > 0 ? "+" : ""}${formatNumber(data.gpa_delta)} point`}
              </small>
            </div>
            {warningGroups.length > 0 && (
              <div className="note-comparison-warnings">
                <AlertTriangle size={17} />
                <div>
                  <strong>Résultats partiels ou données exclues</strong>
                  {warningGroups.map((item) => (
                    <p key={item.id}>
                      <b>{item.name}</b> ·{" "}
                      {item.result.warnings
                        .map((warning) => `${warning.message.replace(/\.$/, "")} (${warning.count})`)
                        .join(" · ")}
                    </p>
                  ))}
                </div>
              </div>
            )}
            <div className="simulation-differences">
              <header>
                <strong>
                  {data.differences.length} différence
                  {data.differences.length === 1 ? "" : "s"}
                </strong>
                <span>UE, ECTS ou évaluations</span>
              </header>
              {data.differences.length ? (
                data.differences.map((difference) => {
                  const representative = difference.right ?? difference.left;
                  return (
                    <div key={difference.lineage_key}>
                      <span>
                        {representative ? noteUeDisplayName(representative) : "UE"}
                        <small>
                          {difference.kind === "left_only"
                            ? `Uniquement dans ${data.left.name}`
                            : difference.kind === "right_only"
                              ? `Uniquement dans ${data.right.name}`
                              : difference.fields.map((field) => comparisonFieldLabels[field]).join(" · ")}
                        </small>
                      </span>
                      <div className="note-comparison-values">
                        {difference.left ? (
                          <>
                            <strong>{formatNumber(difference.left.projection.average)}</strong>
                            <small>/20</small>
                          </>
                        ) : (
                          <i>Absente</i>
                        )}
                        <ArrowLeftRight size={14} />
                        {difference.right ? (
                          <>
                            <strong>{formatNumber(difference.right.projection.average)}</strong>
                            <small>/20</small>
                          </>
                        ) : (
                          <i>Absente</i>
                        )}
                      </div>
                    </div>
                  );
                })
              ) : (
                <EmptyState
                  icon={<Check size={20} />}
                  title="Aucun écart"
                  detail="Ces deux scénarios contiennent les mêmes hypothèses."
                />
              )}
            </div>
            <div className="simulation-comparison-method">
              <Info size={16} />
              <span>
                <strong>Règle IMTégrale {data.formula.version}</strong> · {data.formula.scale} · {data.formula.rounding}
                .
              </span>
            </div>
          </div>
        )
      )}
      <footer className="modal-actions note-comparison-actions">
        <button className="primary-button" type="button" onClick={onClose}>
          Fermer
        </button>
      </footer>
    </Modal>
  );
}
