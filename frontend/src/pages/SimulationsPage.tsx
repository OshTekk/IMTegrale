import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeftRight,
  BadgeCheck,
  BookOpenCheck,
  Check,
  ChevronDown,
  CloudOff,
  Copy,
  EllipsisVertical,
  FilePlus2,
  FlaskConical,
  History,
  Info,
  LoaderCircle,
  Plus,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Trash2,
  TriangleAlert,
  X,
} from "lucide-react";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { EmptyState } from "../components/EmptyState";
import { GradeBadge } from "../components/GradeBadge";
import { Modal } from "../components/Modal";
import {
  SimulationConfirmationModal as ConfirmationModal,
  type SimulationConfirmation as Confirmation,
} from "../components/simulations/SimulationConfirmationModal";
import {
  SimulationSaveIndicator as SaveIndicator,
  type SimulationSaveState as SaveState,
} from "../components/simulations/SimulationSaveIndicator";
import { useToast } from "../components/Toast";
import {
  simulationsSimulationCompare,
  simulationsSimulationCreate,
  simulationsSimulationDelete,
  simulationsSimulationDuplicate,
  simulationsSimulationRebase,
  simulationsSimulationReset,
  simulationsSimulationResolveConflict,
  simulationsSimulationSave,
} from "../generated/api/sdk.gen";
import { ApiError } from "../lib/api";
import { formatNumber, relativeDate } from "../lib/format";
import { apiData, throwOnApiError } from "../lib/generatedApi";
import { queryKeys, useSession, useSimulation, useSimulations } from "../lib/queries";
import {
  calculateDraftProjection,
  createDraftEntry,
  draftIsValid,
  entryDisplayName,
  gradePoints,
  mergeSavedIds,
  scenarioToDraft,
  SIMULATION_GRADES,
  SIMULATION_SEMESTERS,
  simulationPayload,
  type SimulationDraft,
  type SimulationDraftEntry,
} from "../lib/simulations";
import type {
  SimulationGrade,
  SimulationList,
  SimulationScenario,
  SimulationScenarioSummary,
  SimulationSemester,
} from "../types";

function scenarioSummary(scenario: SimulationScenario): SimulationScenarioSummary {
  const { entries: _entries, ...summary } = scenario;
  return summary;
}

function rowNature(entry: SimulationDraftEntry): "imported" | "modified" | "simulated" {
  if (!entry.server || entry.server.nature === "simulated") return "simulated";
  const baseline = entry.server.baseline;
  if (!baseline) return "imported";
  const credits = entry.credits_ects === "" ? null : Number(entry.credits_ects);
  const unchanged =
    entry.semester === baseline.semester &&
    (entry.ue_code || null) === baseline.ue_code &&
    entry.title === (baseline.title ?? "") &&
    credits === baseline.credits_ects &&
    entry.grade === baseline.grade;
  return unchanged ? "imported" : "modified";
}

function natureLabel(nature: ReturnType<typeof rowNature>) {
  if (nature === "imported") return "Officielle importée";
  if (nature === "modified") return "Hypothèse modifiée";
  return "UE simulée";
}

function sourceLabel(entry: SimulationDraftEntry) {
  const source = entry.server?.source;
  if (!source) return null;
  const origin =
    source.grade_source === "competences"
      ? "Grade COMPETENCES"
      : source.grade_source === "pass_calculated"
        ? "Grade calculé depuis PASS"
        : "Base académique";
  return source.observed_at ? `${origin} · ${relativeDate(source.observed_at)}` : origin;
}

function CreationModal({
  open,
  sourceCount,
  sourceGradedCount,
  pending,
  onClose,
  onCreate,
}: {
  open: boolean;
  sourceCount: number;
  sourceGradedCount: number;
  pending: boolean;
  onClose: () => void;
  onCreate: (name: string, importCurrent: boolean) => void;
}) {
  const [name, setName] = useState("Nouvelle projection");
  const [mode, setMode] = useState<"blank" | "academic">(sourceCount ? "academic" : "blank");
  useEffect(() => {
    if (!open) return;
    setName("Nouvelle projection");
    setMode(sourceCount ? "academic" : "blank");
  }, [open, sourceCount]);
  return (
    <Modal
      open={open}
      title="Créer une simulation"
      description="Choisis uniquement le point de départ. Toutes les valeurs resteront modifiables dans ce scénario."
      onClose={onClose}
      size="large"
    >
      <form
        className="simulation-create-form"
        onSubmit={(event) => {
          event.preventDefault();
          onCreate(name, mode === "academic");
        }}
      >
        <label className="simulation-name-field">
          <span>Nom du scénario</span>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={80}
            autoComplete="off"
            required
          />
        </label>
        <div className="simulation-start-options" role="radiogroup" aria-label="Point de départ">
          <button
            type="button"
            className={mode === "academic" ? "active" : ""}
            onClick={() => setMode("academic")}
            disabled={!sourceCount}
            role="radio"
            aria-checked={mode === "academic"}
          >
            <span>
              <BookOpenCheck size={21} />
            </span>
            <strong>Partir de mes UE</strong>
            <small>
              {sourceCount
                ? `${sourceCount} UE disponibles · ${sourceGradedCount} gradées`
                : "Aucune UE officielle disponible"}
            </small>
            <i>{mode === "academic" && <Check size={14} />}</i>
          </button>
          <button
            type="button"
            className={mode === "blank" ? "active" : ""}
            onClick={() => setMode("blank")}
            role="radio"
            aria-checked={mode === "blank"}
          >
            <span>
              <FilePlus2 size={21} />
            </span>
            <strong>Commencer à zéro</strong>
            <small>Un scénario entièrement libre</small>
            <i>{mode === "blank" && <Check size={14} />}</i>
          </button>
        </div>
        <div className="simulation-private-note">
          <ShieldCheck size={17} />
          <span>Privé à ton compte. Les tokens de partage n’y ont jamais accès.</span>
        </div>
        <footer className="modal-actions">
          <button className="secondary-button" type="button" onClick={onClose}>
            Annuler
          </button>
          <button className="primary-button" type="submit" disabled={pending || !name.trim()}>
            {pending ? <LoaderCircle className="spin" size={17} /> : <Plus size={17} />} Créer
          </button>
        </footer>
      </form>
    </Modal>
  );
}

function ComparisonModal({
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
      title="Comparer deux projections"
      description="Les écarts portent uniquement sur les hypothèses de simulation."
      onClose={onClose}
      size="large"
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
          <div className="simulation-comparison">
            <div className="simulation-comparison-score">
              <div>
                <span>{data.left.name}</span>
                <strong>{formatNumber(data.left.result.gpa)}</strong>
                <small>GPA / 4</small>
              </div>
              <div
                className={
                  data.gpa_delta === null
                    ? "neutral"
                    : data.gpa_delta > 0
                      ? "positive"
                      : data.gpa_delta < 0
                        ? "negative"
                        : "neutral"
                }
              >
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
            </div>
            <div className="simulation-differences">
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
                    <div key={difference.lineage_key}>
                      <span>
                        {representative ? entryDisplayName(representative) : "UE"}
                        <small>
                          {difference.kind === "left_only"
                            ? `Uniquement dans ${data.left.name}`
                            : difference.kind === "right_only"
                              ? `Uniquement dans ${data.right.name}`
                              : difference.fields.join(" · ")}
                        </small>
                      </span>
                      <div>
                        {difference.left ? (
                          <>
                            <GradeBadge grade={difference.left.grade} />
                            <small>{formatNumber(difference.left.credits_ects, " ECTS")}</small>
                          </>
                        ) : (
                          <i>Absente</i>
                        )}
                        <ArrowLeftRight size={14} />
                        {difference.right ? (
                          <>
                            <GradeBadge grade={difference.right.grade} />
                            <small>{formatNumber(difference.right.credits_ects, " ECTS")}</small>
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
                  detail="Ces deux simulations contiennent exactement les mêmes hypothèses."
                />
              )}
            </div>
            <div className="simulation-comparison-method">
              <Info size={16} />
              <span>
                <strong>Règle IMTégrale {data.formula.version}</strong> · GPA pondéré par ECTS, arrondi au centième. Les
                UE sans grade ou sans ECTS sont exclues du résultat concerné.
              </span>
            </div>
          </div>
        )
      )}
      <footer className="modal-actions">
        <button className="primary-button" type="button" onClick={onClose}>
          Fermer
        </button>
      </footer>
    </Modal>
  );
}

function SourceConflict({
  entry,
  disabled,
  onResolve,
}: {
  entry: SimulationDraftEntry;
  disabled: boolean;
  onResolve: (resolution: "source" | "simulation") => void;
}) {
  const baseline = entry.server?.baseline;
  if (!baseline || entry.server?.source?.status !== "conflict") return null;
  return (
    <div className="simulation-row-conflict">
      <TriangleAlert size={17} />
      <div>
        <strong>La source officielle a changé</strong>
        <span>
          Source : {baseline.grade ?? "sans grade"} · {formatNumber(baseline.credits_ects, " ECTS")} · Hypothèse :{" "}
          {entry.grade ?? "sans grade"} · {entry.credits_ects || "—"} ECTS
        </span>
      </div>
      <div>
        <button type="button" className="secondary-button" onClick={() => onResolve("source")} disabled={disabled}>
          Utiliser la source
        </button>
        <button type="button" className="primary-button" onClick={() => onResolve("simulation")} disabled={disabled}>
          Garder l’hypothèse
        </button>
      </div>
    </div>
  );
}

function SimulationRow({
  entry,
  disabled,
  onChange,
  onRemove,
  onResolve,
}: {
  entry: SimulationDraftEntry;
  disabled: boolean;
  onChange: (changes: Partial<SimulationDraftEntry>) => void;
  onRemove: () => void;
  onResolve: (resolution: "source" | "simulation") => void;
}) {
  const nature = rowNature(entry);
  const sourceStatus = entry.server?.source?.status;
  const points = gradePoints(entry.grade);
  const provenance = sourceLabel(entry);
  const credits = entry.credits_ects === "" ? null : Number(entry.credits_ects);
  const incomplete = !entry.ue_code.trim() && !entry.title.trim();
  const invalidCredits = credits !== null && (!Number.isFinite(credits) || credits <= 0 || credits > 60);
  return (
    <Fragment>
      <div
        className={`simulation-editor-row nature-${nature}${sourceStatus === "conflict" ? " has-conflict" : ""}${incomplete || invalidCredits ? " is-incomplete" : ""}`}
      >
        <label className="simulation-semester-field">
          <span className="sr-only">Semestre</span>
          <select
            value={entry.semester ?? ""}
            onChange={(event) => onChange({ semester: (event.target.value || null) as SimulationSemester | null })}
            disabled={disabled}
          >
            <option value="">—</option>
            {SIMULATION_SEMESTERS.map((semester) => (
              <option key={semester}>{semester}</option>
            ))}
          </select>
        </label>
        <div className="simulation-ue-fields">
          <label>
            <span className="sr-only">Code UE</span>
            <input
              value={entry.ue_code}
              onChange={(event) => onChange({ ue_code: event.target.value.toUpperCase() })}
              placeholder="Code UE"
              maxLength={32}
              disabled={disabled}
              aria-invalid={incomplete}
            />
          </label>
          <label>
            <span className="sr-only">Intitulé de l’UE</span>
            <input
              value={entry.title}
              onChange={(event) => onChange({ title: event.target.value })}
              placeholder="Intitulé de l’UE"
              maxLength={200}
              disabled={disabled}
              aria-invalid={incomplete}
            />
          </label>
        </div>
        <label className="simulation-ects-field">
          <span className="sr-only">Crédits ECTS</span>
          <input
            type="number"
            value={entry.credits_ects}
            onChange={(event) => onChange({ credits_ects: event.target.value })}
            placeholder="—"
            min="0.01"
            max="60"
            step="0.5"
            inputMode="decimal"
            disabled={disabled}
            aria-invalid={invalidCredits}
          />
          <small>ECTS</small>
        </label>
        <label className="simulation-grade-field">
          <span className="sr-only">Grade potentiel</span>
          <select
            value={entry.grade ?? ""}
            onChange={(event) => onChange({ grade: (event.target.value || null) as SimulationGrade | null })}
            disabled={disabled}
          >
            <option value="">En attente</option>
            {SIMULATION_GRADES.map(({ grade, points: value }) => (
              <option key={grade} value={grade}>
                {grade} · {formatNumber(value)}
              </option>
            ))}
          </select>
        </label>
        <div className="simulation-points">
          <strong>{formatNumber(points)}</strong>
          <small>pts</small>
        </div>
        <div className="simulation-origin">
          <span className={`simulation-origin-pill ${nature}`}>
            {nature === "imported" ? (
              <BadgeCheck size={13} />
            ) : nature === "modified" ? (
              <Sparkles size={13} />
            ) : (
              <FlaskConical size={13} />
            )}
            {natureLabel(nature)}
          </span>
          {provenance && (
            <small className="simulation-source-meta" title={provenance}>
              {provenance}
            </small>
          )}
          {sourceStatus === "unavailable" && (
            <small className="source-unavailable">
              <AlertTriangle size={12} /> Source indisponible
            </small>
          )}
        </div>
        <button
          className="icon-button simulation-remove-row"
          type="button"
          onClick={onRemove}
          aria-label={`Supprimer ${entryDisplayName(entry)}`}
          title="Supprimer l’UE"
          disabled={disabled}
        >
          <X size={17} />
        </button>
      </div>
      <SourceConflict entry={entry} disabled={disabled} onResolve={onResolve} />
    </Fragment>
  );
}

export function SimulationsPage() {
  const session = useSession();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const accountId = session.data?.account?.id ?? "anonymous";
  const simulations = useSimulations();
  const [activeId, setActiveId] = useState<string | null>(null);
  const scenario = useSimulation(activeId);
  const [draft, setDraft] = useState<SimulationDraft | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const [semester, setSemester] = useState<"all" | SimulationSemester>("all");
  const [creationOpen, setCreationOpen] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation>(null);
  const [comparisonOpen, setComparisonOpen] = useState(false);
  const [comparisonId, setComparisonId] = useState("");
  const revision = useRef(0);
  const draftRef = useRef<SimulationDraft | null>(null);
  const saveStateRef = useRef<SaveState>("saved");
  const savePendingRef = useRef(false);
  const scenarios = useMemo(() => simulations.data?.scenarios ?? [], [simulations.data?.scenarios]);

  const cacheScenario = (next: SimulationScenario) => {
    queryClient.setQueryData(queryKeys.simulation(accountId, next.id), next);
    queryClient.setQueryData<SimulationList>(queryKeys.simulations(accountId), (current) =>
      current
        ? {
            ...current,
            scenarios: (current.scenarios.some((item) => item.id === next.id)
              ? current.scenarios.map((item) => (item.id === next.id ? scenarioSummary(next) : item))
              : [scenarioSummary(next), ...current.scenarios]
            ).sort((left, right) => new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime()),
          }
        : current,
    );
  };

  useEffect(() => {
    if (!simulations.data) return;
    if (activeId && simulations.data.scenarios.some((item) => item.id === activeId)) return;
    setActiveId(simulations.data.scenarios[0]?.id ?? null);
  }, [activeId, simulations.data]);

  useEffect(() => {
    if (!scenario.data) {
      if (!activeId) setDraft(null);
      return;
    }
    const switching = draft?.id !== scenario.data.id;
    const serverAhead = draft && scenario.data.version > draft.version;
    if (switching || (!draft && scenario.data) || (serverAhead && saveState === "saved")) {
      revision.current = 0;
      setDraft(scenarioToDraft(scenario.data));
      setSaveState("saved");
      setSemester("all");
    }
  }, [activeId, draft, saveState, scenario.data]);

  useEffect(() => {
    if (!comparisonOpen || (comparisonId && comparisonId !== activeId)) return;
    setComparisonId(scenarios.find((item) => item.id !== activeId)?.id ?? "");
  }, [activeId, comparisonId, comparisonOpen, scenarios]);

  const createMutation = useMutation({
    mutationFn: ({ name, importCurrent }: { name: string; importCurrent: boolean }) =>
      apiData(
        simulationsSimulationCreate({
          body: { name, import_current: importCurrent },
          throwOnError: throwOnApiError,
        }),
      ),
    onSuccess: (created) => {
      cacheScenario(created);
      void queryClient.invalidateQueries({ queryKey: queryKeys.simulations(accountId) });
      setCreationOpen(false);
      setActiveId(created.id);
      setDraft(scenarioToDraft(created));
      setSaveState("saved");
      showToast(created.created_from === "academic" ? "UE actuelles importées dans la simulation" : "Simulation créée");
    },
    onError: (error) => showToast(error.message, "error"),
  });

  const saveMutation = useMutation({
    mutationFn: ({
      id,
      body,
    }: {
      id: string;
      body: ReturnType<typeof simulationPayload>;
      localRevision: number;
      sentKeys: string[];
    }) =>
      apiData(
        simulationsSimulationSave({
          path: { scenario_id: id },
          body,
          throwOnError: throwOnApiError,
        }),
      ),
    onMutate: () => setSaveState("saving"),
    onSuccess: (saved, variables) => {
      cacheScenario(saved);
      if (revision.current === variables.localRevision) {
        setDraft(scenarioToDraft(saved));
        setSaveState("saved");
      } else {
        setDraft((current) => (current ? mergeSavedIds(current, saved, variables.sentKeys) : current));
        setSaveState("dirty");
      }
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === "simulation_version_conflict") {
        setSaveState("conflict");
        return;
      }
      setSaveState("error");
      showToast(error.message, "error");
    },
  });

  draftRef.current = draft;
  saveStateRef.current = saveState;
  savePendingRef.current = saveMutation.isPending;

  useEffect(() => {
    if (saveState === "saved") return;
    const warnBeforeLeaving = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeLeaving);
    return () => window.removeEventListener("beforeunload", warnBeforeLeaving);
  }, [saveState]);

  useEffect(
    () => () => {
      const current = draftRef.current;
      if (!current || saveStateRef.current !== "dirty" || savePendingRef.current || !draftIsValid(current)) return;
      void apiData(
        simulationsSimulationSave({
          path: { scenario_id: current.id },
          body: simulationPayload(current),
          throwOnError: throwOnApiError,
        }),
      ).catch(() => undefined);
    },
    [],
  );

  const validDraft = Boolean(draft && draftIsValid(draft));
  useEffect(() => {
    if (!draft || saveState !== "dirty" || !validDraft || saveMutation.isPending) return;
    const timer = window.setTimeout(() => {
      saveMutation.mutate({
        id: draft.id,
        body: simulationPayload(draft),
        localRevision: revision.current,
        sentKeys: draft.entries.map((entry) => entry.clientKey),
      });
    }, 700);
    return () => window.clearTimeout(timer);
  }, [draft, saveMutation, saveState, validDraft]);

  const actionMutation = useMutation({
    mutationFn: async ({
      action,
      payload,
    }: {
      action: "duplicate" | "reset" | "delete" | "rebase";
      payload?: { name?: string };
    }) => {
      if (!activeId || !draft) throw new Error("Simulation introuvable");
      if (action === "delete") {
        await apiData(
          simulationsSimulationDelete({
            path: { scenario_id: activeId },
            query: { version: draft.version },
            throwOnError: throwOnApiError,
          }),
        );
        return { action, scenario: null };
      }
      const options = {
        path: { scenario_id: activeId },
        body: { version: draft.version, ...payload },
        throwOnError: throwOnApiError,
      };
      const scenario =
        action === "duplicate"
          ? await apiData(simulationsSimulationDuplicate(options))
          : action === "rebase"
            ? await apiData(simulationsSimulationRebase(options))
            : await apiData(simulationsSimulationReset(options));
      return { action, scenario };
    },
    onSuccess: ({ action, scenario: next }) => {
      setConfirmation(null);
      if (action === "delete") {
        const replacement = scenarios.find((item) => item.id !== activeId)?.id ?? null;
        queryClient.removeQueries({ queryKey: queryKeys.simulation(accountId, activeId ?? "none") });
        setActiveId(replacement);
        setDraft(null);
        void queryClient.invalidateQueries({ queryKey: queryKeys.simulations(accountId) });
        showToast("Simulation supprimée");
        return;
      }
      if (!next) return;
      cacheScenario(next);
      void queryClient.invalidateQueries({ queryKey: queryKeys.simulations(accountId) });
      if (action === "duplicate") {
        setActiveId(next.id);
        setDraft(scenarioToDraft(next));
        showToast("Simulation dupliquée");
      } else {
        cacheScenario(next);
        setDraft(scenarioToDraft(next));
        setSaveState("saved");
        showToast(action === "rebase" ? "Source officielle actualisée" : "Simulation réinitialisée");
      }
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === "simulation_version_conflict") {
        setConfirmation(null);
        setSaveState("conflict");
        return;
      }
      showToast(error.message, "error");
    },
  });

  const resolveMutation = useMutation({
    mutationFn: ({ entryId, resolution }: { entryId: string; resolution: "source" | "simulation" }) => {
      if (!activeId || !draft) throw new Error("Simulation introuvable");
      return apiData(
        simulationsSimulationResolveConflict({
          path: { scenario_id: activeId, entry_id: entryId },
          body: { version: draft.version, resolution },
          throwOnError: throwOnApiError,
        }),
      );
    },
    onSuccess: (next) => {
      cacheScenario(next);
      setDraft(scenarioToDraft(next));
      setSaveState("saved");
      showToast("Conflit résolu");
    },
    onError: (error) => showToast(error.message, "error"),
  });

  const preserveConflictMutation = useMutation({
    mutationFn: async () => {
      if (!draft) throw new Error("Simulation introuvable");
      const created = await apiData(
        simulationsSimulationCreate({
          body: { name: `${draft.name.slice(0, 64)} - copie locale`, import_current: false },
          throwOnError: throwOnApiError,
        }),
      );
      const body = simulationPayload({ ...draft, id: created.id, version: created.version });
      body.entries = body.entries.map(({ id: _id, ...entry }) => entry);
      try {
        return await apiData(
          simulationsSimulationSave({
            path: { scenario_id: created.id },
            body,
            throwOnError: throwOnApiError,
          }),
        );
      } catch (error) {
        await apiData(
          simulationsSimulationDelete({
            path: { scenario_id: created.id },
            query: { version: created.version },
            throwOnError: throwOnApiError,
          }),
        ).catch(() => undefined);
        throw error;
      }
    },
    onSuccess: (created) => {
      queryClient.setQueryData(queryKeys.simulation(accountId, created.id), created);
      void queryClient.invalidateQueries({ queryKey: queryKeys.simulations(accountId) });
      setActiveId(created.id);
      setDraft(scenarioToDraft(created));
      setSaveState("saved");
      showToast("Modifications conservées dans une copie");
    },
    onError: (error) => showToast(error.message, "error"),
  });

  const reloadServerVersion = async () => {
    const refreshed = await scenario.refetch();
    if (!refreshed.data) return;
    revision.current = 0;
    setDraft(scenarioToDraft(refreshed.data));
    setSaveState("saved");
    showToast("Version serveur rechargée");
  };

  const updateDraft = (updater: (current: SimulationDraft) => SimulationDraft) => {
    revision.current += 1;
    setDraft((current) => (current ? updater(current) : current));
    setSaveState("dirty");
  };

  const updateEntry = (clientKey: string, changes: Partial<SimulationDraftEntry>) =>
    updateDraft((current) => ({
      ...current,
      entries: current.entries.map((entry) => (entry.clientKey === clientKey ? { ...entry, ...changes } : entry)),
    }));
  const removeEntry = (clientKey: string) =>
    updateDraft((current) => ({
      ...current,
      entries: current.entries.filter((entry) => entry.clientKey !== clientKey),
    }));
  const addEntry = () =>
    updateDraft((current) => ({
      ...current,
      entries: [...current.entries, createDraftEntry(semester === "all" ? null : semester)],
    }));

  const projection = useMemo(() => calculateDraftProjection(draft?.entries ?? []), [draft?.entries]);
  const selectedProjection =
    semester === "all"
      ? projection
      : (projection.semesters.find((item) => item.semester === semester) ?? {
          gpa: null,
          creditsIncluded: 0,
          ueCount: 0,
          gradedCount: 0,
          pendingCount: 0,
        });
  const visibleEntries = draft?.entries.filter((entry) => semester === "all" || entry.semester === semester) ?? [];
  const availableSemesters = useMemo(
    () =>
      [
        ...new Set(
          (draft?.entries ?? [])
            .map((entry) => entry.semester)
            .filter((value): value is SimulationSemester => Boolean(value)),
        ),
      ].sort((left, right) => Number(left.slice(1)) - Number(right.slice(1))),
    [draft?.entries],
  );
  const currentSummary = scenarios.find((item) => item.id === activeId);
  const editorDisabled = saveState === "conflict";

  if (simulations.isPending) {
    return (
      <div className="simulation-page-loading" aria-busy="true">
        <div className="skeleton simulation-tabs-skeleton" />
        <div className="skeleton simulation-summary-skeleton" />
        <div className="skeleton simulation-editor-skeleton" />
      </div>
    );
  }
  if (simulations.isError) {
    return (
      <section className="content-panel">
        <EmptyState
          icon={<AlertTriangle size={21} />}
          title="Simulations indisponibles"
          detail={simulations.error.message}
          action={
            <button className="secondary-button" type="button" onClick={() => simulations.refetch()}>
              <RefreshCw size={16} /> Réessayer
            </button>
          }
        />
      </section>
    );
  }

  return (
    <div className="simulations-page">
      <section className="simulation-privacy-band">
        <span>
          <ShieldCheck size={20} />
        </span>
        <div>
          <strong>Espace de projection privé</strong>
          <p>Les hypothèses restent dans IMTégrale et ne modifient jamais PASS ou COMPETENCES.</p>
        </div>
        <div>
          <i>Formule indicative</i>
          <small>GPA pondéré par ECTS</small>
        </div>
      </section>

      <section className="simulation-scenario-bar" aria-label="Scénarios">
        <div className="simulation-tabs" role="tablist" aria-label="Choisir une simulation">
          {scenarios.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={item.id === activeId}
              className={item.id === activeId ? "active" : ""}
              onClick={() => setActiveId(item.id)}
              disabled={item.id !== activeId && saveState !== "saved"}
              title={item.id !== activeId && saveState !== "saved" ? "Attends la fin de l’enregistrement" : undefined}
            >
              <span>{item.name}</span>
              <small>
                {item.result.gpa === null ? "GPA —" : `GPA ${formatNumber(item.result.gpa)}`} · {item.result.ue_count}{" "}
                UE
              </small>
              {item.rebase_available && <i aria-label="Source actualisée" title="Source actualisée" />}
            </button>
          ))}
        </div>
        <button
          className="icon-button simulation-add-scenario"
          type="button"
          onClick={() => setCreationOpen(true)}
          disabled={scenarios.length >= (simulations.data?.limit ?? 5) || saveState !== "saved"}
          aria-label="Créer une simulation"
          title={
            scenarios.length >= (simulations.data?.limit ?? 5)
              ? "Limite de cinq simulations atteinte"
              : saveState !== "saved"
                ? "Attends la fin de l’enregistrement"
                : "Nouvelle simulation"
          }
        >
          <Plus size={19} />
        </button>
        <span className="simulation-limit">
          {scenarios.length}/{simulations.data?.limit ?? 5}
        </span>
      </section>

      {!scenarios.length ? (
        <section className="simulation-empty-panel">
          <EmptyState
            icon={<FlaskConical size={23} />}
            title="Aucune simulation"
            detail="Crée un scénario vide ou pars de tes UE académiques actuelles."
            action={
              <button className="primary-button" type="button" onClick={() => setCreationOpen(true)}>
                <Plus size={17} /> Créer ma première simulation
              </button>
            }
          />
        </section>
      ) : scenario.isPending || !draft || !currentSummary ? (
        <div className="simulation-page-loading" aria-busy="true">
          <div className="skeleton simulation-summary-skeleton" />
          <div className="skeleton simulation-editor-skeleton" />
        </div>
      ) : (
        <>
          {currentSummary.rebase_available && (
            <section className="simulation-rebase-banner">
              <History size={20} />
              <div>
                <strong>Tes données académiques ont évolué</strong>
                <p>
                  Actualise la base officielle du scénario. Les hypothèses déjà modifiées seront conservées et signalées
                  en cas de conflit.
                </p>
              </div>
              <button
                className="secondary-button"
                type="button"
                onClick={() => actionMutation.mutate({ action: "rebase" })}
                disabled={saveState !== "saved" || actionMutation.isPending}
              >
                {actionMutation.isPending ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}{" "}
                Actualiser la base
              </button>
            </section>
          )}
          {saveState === "conflict" && (
            <section className="simulation-version-banner">
              <CloudOff size={20} />
              <div>
                <strong>Une version plus récente existe</strong>
                <p>Tes changements locaux sont toujours affichés mais n’ont pas écrasé l’autre onglet.</p>
              </div>
              <div>
                <button className="secondary-button" type="button" onClick={reloadServerVersion}>
                  Recharger
                </button>
                <button
                  className="primary-button"
                  type="button"
                  onClick={() => preserveConflictMutation.mutate()}
                  disabled={preserveConflictMutation.isPending}
                >
                  {preserveConflictMutation.isPending ? (
                    <LoaderCircle className="spin" size={16} />
                  ) : (
                    <Copy size={16} />
                  )}{" "}
                  Conserver en copie
                </button>
              </div>
            </section>
          )}
          {saveState === "error" && (
            <section className="simulation-save-error-banner">
              <CloudOff size={20} />
              <div>
                <strong>L’enregistrement n’a pas abouti</strong>
                <p>Tes modifications sont toujours présentes dans cette page.</p>
              </div>
              <button className="secondary-button" type="button" onClick={() => setSaveState("dirty")}>
                <RefreshCw size={16} /> Réessayer
              </button>
            </section>
          )}

          <section className="simulation-workspace">
            <header className="simulation-workspace-header">
              <div className="simulation-title-wrap">
                <label>
                  <span className="sr-only">Nom de la simulation</span>
                  <input
                    value={draft.name}
                    onChange={(event) => updateDraft((current) => ({ ...current, name: event.target.value }))}
                    maxLength={80}
                    disabled={editorDisabled}
                  />
                </label>
                <SaveIndicator state={saveState} valid={validDraft} />
                <small>Mis à jour {relativeDate(currentSummary.updated_at)}</small>
              </div>
              <div className="simulation-workspace-actions">
                <button
                  className="secondary-button"
                  type="button"
                  onClick={() => setComparisonOpen(true)}
                  disabled={scenarios.length < 2 || saveState !== "saved"}
                >
                  <ArrowLeftRight size={16} /> Comparer
                </button>
                <details className="simulation-overflow">
                  <summary className="icon-button" aria-label="Actions sur la simulation" title="Plus d’actions">
                    <EllipsisVertical size={18} />
                  </summary>
                  <div>
                    <button
                      type="button"
                      onClick={(event) => {
                        event.currentTarget.closest("details")?.removeAttribute("open");
                        actionMutation.mutate({
                          action: "duplicate",
                          payload: { name: `${draft.name.slice(0, 68)} - copie` },
                        });
                      }}
                      disabled={saveState !== "saved"}
                    >
                      <Copy size={16} /> Dupliquer
                    </button>
                    <button
                      type="button"
                      onClick={(event) => {
                        event.currentTarget.closest("details")?.removeAttribute("open");
                        setConfirmation("reset");
                      }}
                      disabled={saveState !== "saved"}
                    >
                      <RotateCcw size={16} /> Réinitialiser
                    </button>
                    <button
                      className="danger"
                      type="button"
                      onClick={(event) => {
                        event.currentTarget.closest("details")?.removeAttribute("open");
                        setConfirmation("delete");
                      }}
                      disabled={saveState !== "saved"}
                    >
                      <Trash2 size={16} /> Supprimer
                    </button>
                  </div>
                </details>
              </div>
            </header>

            <div className="simulation-summary-band">
              <div className="simulation-gpa-primary">
                <span>{semester === "all" ? "GPA global simulé" : `GPA simulé · ${semester}`}</span>
                <strong>{formatNumber(selectedProjection.gpa)}</strong>
                <small>sur 4,00</small>
              </div>
              <div>
                <span>ECTS pondérés</span>
                <strong>{formatNumber(selectedProjection.creditsIncluded)}</strong>
                <small>grades renseignés</small>
              </div>
              <div>
                <span>UE gradées</span>
                <strong>
                  {selectedProjection.gradedCount}
                  <i>/{selectedProjection.ueCount}</i>
                </strong>
                <small>{semester === "all" ? `${projection.completionRate} % du scénario` : "dans ce semestre"}</small>
              </div>
              <div>
                <span>En attente</span>
                <strong>{selectedProjection.pendingCount}</strong>
                <small>exclue{selectedProjection.pendingCount === 1 ? "" : "s"} du calcul</small>
              </div>
            </div>

            <div className="simulation-semester-toolbar">
              <div className="simulation-semester-tabs" role="tablist" aria-label="Filtrer par semestre">
                <button
                  type="button"
                  role="tab"
                  aria-selected={semester === "all"}
                  className={semester === "all" ? "active" : ""}
                  onClick={() => setSemester("all")}
                >
                  Tous
                </button>
                {availableSemesters.map((item) => (
                  <button
                    key={item}
                    type="button"
                    role="tab"
                    aria-selected={semester === item}
                    className={semester === item ? "active" : ""}
                    onClick={() => setSemester(item)}
                  >
                    {item}
                  </button>
                ))}
              </div>
              <span>
                {visibleEntries.length} UE affichée{visibleEntries.length === 1 ? "" : "s"}
              </span>
            </div>

            <div className="simulation-editor">
              <div className="simulation-editor-head" aria-hidden="true">
                <span>Sem.</span>
                <span>Unité d’enseignement</span>
                <span>Crédits</span>
                <span>Grade potentiel</span>
                <span>GPA</span>
                <span>Nature</span>
                <span />
              </div>
              <div className="simulation-editor-body">
                {visibleEntries.length ? (
                  visibleEntries.map((entry) => (
                    <SimulationRow
                      key={entry.clientKey}
                      entry={entry}
                      disabled={editorDisabled}
                      onChange={(changes) => updateEntry(entry.clientKey, changes)}
                      onRemove={() => removeEntry(entry.clientKey)}
                      onResolve={(resolution) => entry.id && resolveMutation.mutate({ entryId: entry.id, resolution })}
                    />
                  ))
                ) : (
                  <EmptyState
                    icon={<FlaskConical size={20} />}
                    title={semester === "all" ? "Scénario vide" : `Aucune UE en ${semester}`}
                    detail={
                      semester === "all"
                        ? "Ajoute une UE pour commencer ta projection."
                        : "Ajoute une UE, elle sera directement placée dans ce semestre."
                    }
                  />
                )}
              </div>
              <footer className="simulation-editor-footer">
                <button
                  className="secondary-button"
                  type="button"
                  onClick={addEntry}
                  disabled={editorDisabled || draft.entries.length >= 120}
                >
                  <Plus size={17} /> Ajouter une UE{semester === "all" ? "" : ` en ${semester}`}
                </button>
                <span>
                  <Info size={14} /> Une UE sans grade ne compte pas comme zéro.
                </span>
              </footer>
            </div>

            <details className="simulation-formula">
              <summary>
                <span>
                  <Info size={16} /> Barème et formule
                </span>
                <ChevronDown size={17} />
              </summary>
              <div>
                <p>
                  <strong>GPA = somme des points GPA × ECTS ÷ somme des ECTS.</strong> Seules les UE avec un grade et
                  des ECTS renseignés sont incluses. Résultat arrondi au centième.
                </p>
                <div className="simulation-grade-scale">
                  {SIMULATION_GRADES.map(({ grade, points }) => (
                    <span key={grade}>
                      <GradeBadge grade={grade} />
                      <strong>{formatNumber(points)}</strong>
                    </span>
                  ))}
                </div>
                <small>
                  Règle IMTégrale {currentSummary.formula_version} · projection indicative, jamais publiée dans le
                  classement.
                </small>
              </div>
            </details>
          </section>
        </>
      )}

      <CreationModal
        open={creationOpen}
        sourceCount={simulations.data?.source.ue_count ?? 0}
        sourceGradedCount={simulations.data?.source.graded_count ?? 0}
        pending={createMutation.isPending}
        onClose={() => setCreationOpen(false)}
        onCreate={(name, importCurrent) => createMutation.mutate({ name, importCurrent })}
      />
      <ConfirmationModal
        action={confirmation}
        name={draft?.name ?? ""}
        pending={actionMutation.isPending}
        resetDescription="Les UE importées retrouveront leurs valeurs de départ et les UE ajoutées seront retirées."
        deleteBody="Cette action ne touche ni PASS, ni COMPETENCES, ni tes autres simulations."
        resetBody="Le scénario reste disponible avec sa dernière base officielle."
        onClose={() => setConfirmation(null)}
        onConfirm={() => confirmation && actionMutation.mutate({ action: confirmation })}
      />
      {currentSummary && (
        <ComparisonModal
          open={comparisonOpen}
          accountId={accountId}
          left={currentSummary}
          scenarios={scenarios}
          rightId={comparisonId}
          setRightId={setComparisonId}
          onClose={() => setComparisonOpen(false)}
        />
      )}
    </div>
  );
}
