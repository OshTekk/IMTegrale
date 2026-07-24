import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, FlaskConical, Plus, RefreshCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { EmptyState } from "../../components/EmptyState";
import {
  SimulationConfirmationModal,
  type SimulationConfirmation,
} from "../../components/simulations/SimulationConfirmationModal";
import { useToast } from "../../components/Toast";
import { queryKeys, useSession, useSimulation, useSimulations } from "../../lib/queries";
import {
  calculateDraftProjection,
  scenarioToDraft,
  SIMULATION_SEMESTERS,
  type SimulationDraft,
  type SimulationDraftEntry,
} from "../../lib/simulations";
import type { SimulationList, SimulationScenario, SimulationScenarioSummary, SimulationSemester } from "../../types";
import { GpaSimulationComparison } from "./GpaSimulationComparison";
import { GpaSimulationCreationModal } from "./GpaSimulationCreationModal";
import { GpaSimulationScenarioSelector } from "./GpaSimulationScenarioSelector";
import { GpaSimulationStatusBanners } from "./GpaSimulationStatusBanners";
import { GpaSimulationWorkbench } from "./GpaSimulationWorkbench";
import { domSafeKey, projectionForSemester } from "./gpaSimulationPresentation";
import type { GpaSimulationEditorState } from "./gpaSimulationState";
import "./gpaSimulations.css";
import { useGpaConflictCommands } from "./useGpaConflictCommands";
import { useGpaScenarioCommands } from "./useGpaScenarioCommands";
import { useGpaSimulationAutosave } from "./useGpaSimulationAutosave";
import { useSimulationContainerMode } from "./useSimulationContainerMode";

const ACTIVE_SCENARIO_KEY = "imtegrale.gpa-simulations.active";

function scenarioSummary(scenario: SimulationScenario): SimulationScenarioSummary {
  const { entries: _entries, ...summary } = scenario;
  return summary;
}

export function GpaSimulationsPage() {
  const session = useSession();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [container, setContainer] = useState<HTMLDivElement | null>(null);
  const layoutMode = useSimulationContainerMode(container);
  const compact = layoutMode === "compact";
  const accountId = session.data?.account?.id ?? "anonymous";
  const simulations = useSimulations();
  const [activeId, setActiveId] = useState<string | null>(() => window.localStorage.getItem(ACTIVE_SCENARIO_KEY));
  const scenario = useSimulation(activeId);
  const [draft, setDraft] = useState<SimulationDraft | null>(null);
  const [semester, setSemester] = useState<"all" | SimulationSemester>("all");
  const [creationOpen, setCreationOpen] = useState(false);
  const [confirmation, setConfirmation] = useState<SimulationConfirmation>(null);
  const [comparisonOpen, setComparisonOpen] = useState(false);
  const [comparisonId, setComparisonId] = useState("");
  const [editor, setEditor] = useState<GpaSimulationEditorState>(null);
  const pendingFocusId = useRef<string | null>(null);
  const scenarios = useMemo(() => simulations.data?.scenarios ?? [], [simulations.data?.scenarios]);

  const cacheScenario = useCallback(
    (next: SimulationScenario) => {
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
    },
    [accountId, queryClient],
  );
  const { resetRevision, saveState, setSaveState, updateDraft, validDraft } = useGpaSimulationAutosave({
    draft,
    setDraft,
    cacheScenario,
    showToast,
  });

  useEffect(() => {
    if (!simulations.data) return;
    if (activeId && simulations.data.scenarios.some((item) => item.id === activeId)) return;
    const remembered = window.localStorage.getItem(ACTIVE_SCENARIO_KEY);
    const next =
      simulations.data.scenarios.find((item) => item.id === remembered)?.id ??
      simulations.data.scenarios[0]?.id ??
      null;
    setActiveId(next);
  }, [activeId, simulations.data]);

  useEffect(() => {
    if (activeId) window.localStorage.setItem(ACTIVE_SCENARIO_KEY, activeId);
    else window.localStorage.removeItem(ACTIVE_SCENARIO_KEY);
  }, [activeId]);

  useEffect(() => {
    if (!scenario.data) {
      if (!activeId) setDraft(null);
      return;
    }
    const switching = draft?.id !== scenario.data.id;
    const serverAhead = Boolean(draft && scenario.data.version > draft.version);
    if (switching || !draft || (serverAhead && saveState === "saved")) {
      resetRevision();
      setDraft(scenarioToDraft(scenario.data));
      setSaveState("saved");
      setSemester("all");
      setEditor(null);
    }
  }, [activeId, draft, resetRevision, saveState, scenario.data, setSaveState]);

  useEffect(() => {
    if (!comparisonOpen || (comparisonId && comparisonId !== activeId)) return;
    setComparisonId(scenarios.find((item) => item.id !== activeId)?.id ?? "");
  }, [activeId, comparisonId, comparisonOpen, scenarios]);

  useEffect(() => {
    const targetId = pendingFocusId.current;
    if (!targetId) return;
    const frame = window.requestAnimationFrame(() => {
      const target = document.getElementById(targetId);
      if (!target) return;
      target.focus({ preventScroll: true });
      target.scrollIntoView({ block: "nearest", behavior: "auto" });
      pendingFocusId.current = null;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [draft, editor]);

  const { actionMutation, createMutation } = useGpaScenarioCommands({
    accountId,
    activeId,
    draft,
    editor,
    scenarios,
    cacheScenario,
    setActiveId,
    setDraft,
    setEditor,
    setSaveState,
    closeCreation: () => setCreationOpen(false),
    closeConfirmation: () => setConfirmation(null),
    showToast,
  });
  const { preserveConflictMutation, reloadServerVersion, resolveMutation } = useGpaConflictCommands({
    accountId,
    activeId,
    draft,
    editor,
    cacheScenario,
    fetchServerVersion: async () => (await scenario.refetch()).data,
    resetRevision,
    setActiveId,
    setDraft,
    setEditor,
    setSaveState,
    showToast,
  });

  const applyEntry = (nextEntry: SimulationDraftEntry) => {
    const adding = editor?.mode === "add";
    updateDraft((current) => ({
      ...current,
      entries: adding
        ? [...current.entries, nextEntry]
        : current.entries.map((entry) => (entry.clientKey === nextEntry.clientKey ? nextEntry : entry)),
    }));
    pendingFocusId.current = `gpa-ue-trigger-${domSafeKey(nextEntry.clientKey)}`;
    setEditor(compact ? null : { mode: "edit", entryKey: nextEntry.clientKey });
  };

  const deleteEntry = (entry: SimulationDraftEntry) => {
    updateDraft((current) => ({
      ...current,
      entries: current.entries.filter((item) => item.clientKey !== entry.clientKey),
    }));
    pendingFocusId.current = "gpa-add-ue";
    setEditor(null);
  };

  const closeEditor = () => {
    if (editor?.mode === "edit") {
      pendingFocusId.current = `gpa-ue-trigger-${domSafeKey(editor.entryKey)}`;
    } else {
      pendingFocusId.current = "gpa-add-ue";
    }
    setEditor(null);
  };

  const handleSemesterChange = (next: "all" | SimulationSemester) => {
    setSemester(next);
    if (editor?.mode !== "edit" || next === "all") return;
    const selected = draft?.entries.find((entry) => entry.clientKey === editor.entryKey);
    if (selected?.semester !== next) setEditor(null);
  };

  const projection = useMemo(() => calculateDraftProjection(draft?.entries ?? []), [draft?.entries]);
  const { selected: selectedProjection } = useMemo(
    () => projectionForSemester(draft?.entries ?? [], semester),
    [draft?.entries, semester],
  );
  const visibleEntries = useMemo(
    () => draft?.entries.filter((entry) => semester === "all" || entry.semester === semester) ?? [],
    [draft?.entries, semester],
  );
  const availableSemesters = useMemo(
    () => SIMULATION_SEMESTERS.filter((value) => draft?.entries.some((entry) => entry.semester === value)),
    [draft?.entries],
  );
  const currentSummary = scenarios.find((item) => item.id === activeId);
  const editorDisabled = saveState === "conflict";
  const editedEntry =
    editor?.mode === "edit" ? (draft?.entries.find((entry) => entry.clientKey === editor.entryKey) ?? null) : null;

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
    <div ref={setContainer} className="simulations-page gpa-simulations-page" data-layout={layoutMode}>
      <h1 className="sr-only">Simulation GPA</h1>
      <section className="simulation-privacy-band">
        <span>
          <ShieldCheck size={20} />
        </span>
        <div>
          <strong>Espace de projection privé</strong>
          <p>Teste des grades futurs sans modifier PASS ou COMPETENCES.</p>
        </div>
        <div>
          <i>Formule indicative</i>
          <small>GPA pondéré par ECTS</small>
        </div>
      </section>

      <GpaSimulationScenarioSelector
        scenarios={scenarios}
        activeId={activeId}
        compact={compact}
        saveState={saveState}
        limit={simulations.data?.limit ?? 5}
        activeGpa={draft ? projection.gpa : null}
        activeUeCount={draft?.entries.length ?? 0}
        onSelect={(id) => {
          if (saveState !== "saved") return;
          setActiveId(id);
        }}
        onCreate={() => setCreationOpen(true)}
      />

      {!scenarios.length ? (
        <section className="simulation-empty-panel">
          <EmptyState
            icon={<FlaskConical size={23} />}
            title="Aucune simulation GPA"
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
          <GpaSimulationStatusBanners
            rebaseAvailable={currentSummary.rebase_available}
            saveState={saveState}
            actionPending={actionMutation.isPending}
            preservePending={preserveConflictMutation.isPending}
            onRebase={() => actionMutation.mutate({ action: "rebase" })}
            onReload={reloadServerVersion}
            onPreserve={() => preserveConflictMutation.mutate()}
            onRetry={() => setSaveState("dirty")}
          />
          <GpaSimulationWorkbench
            scenario={currentSummary}
            draft={draft}
            saveState={saveState}
            validDraft={validDraft}
            canCompare={scenarios.length >= 2}
            compact={compact}
            semester={semester}
            semesters={availableSemesters}
            projection={projection}
            selectedProjection={selectedProjection}
            visibleEntries={visibleEntries}
            editor={editor}
            editedEntry={editedEntry}
            editorDisabled={editorDisabled}
            actionPending={actionMutation.isPending}
            conflictPending={resolveMutation.isPending}
            onNameChange={(name) => updateDraft((current) => ({ ...current, name }))}
            onCompare={() => setComparisonOpen(true)}
            onDuplicate={() =>
              actionMutation.mutate({
                action: "duplicate",
                payload: { name: `${draft.name.slice(0, 68)} - copie` },
              })
            }
            onConfirm={setConfirmation}
            onSemesterChange={handleSemesterChange}
            onAdd={() => setEditor({ mode: "add" })}
            onOpen={(entry) => setEditor({ mode: "edit", entryKey: entry.clientKey })}
            onCloseEditor={closeEditor}
            onApplyEntry={applyEntry}
            onDeleteEntry={deleteEntry}
            onResolve={(entry, resolution) => entry.id && resolveMutation.mutate({ entryId: entry.id, resolution })}
          />
        </>
      )}

      <GpaSimulationCreationModal
        open={creationOpen}
        sourceCount={simulations.data?.source.ue_count ?? 0}
        sourceGradedCount={simulations.data?.source.graded_count ?? 0}
        pending={createMutation.isPending}
        onClose={() => setCreationOpen(false)}
        onCreate={(name, importCurrent) => createMutation.mutate({ name, importCurrent })}
      />
      <SimulationConfirmationModal
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
        <GpaSimulationComparison
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
