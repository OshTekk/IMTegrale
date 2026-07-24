import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  BarChart3,
  CloudOff,
  Copy,
  History,
  Info,
  LoaderCircle,
  Plus,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { EmptyState } from "../../components/EmptyState";
import {
  SimulationConfirmationModal,
  type SimulationConfirmation,
} from "../../components/simulations/SimulationConfirmationModal";
import type { SimulationSaveState } from "../../components/simulations/SimulationSaveIndicator";
import { useToast } from "../../components/Toast";
import {
  noteSimulationsResolveAssessmentConflict,
  noteSimulationsResolveUeConflict,
  noteSimulationsScenarioCreate,
  noteSimulationsScenarioDelete,
  noteSimulationsScenarioDuplicate,
  noteSimulationsScenarioRebase,
  noteSimulationsScenarioReset,
  noteSimulationsScenarioSave,
} from "../../generated/api/sdk.gen";
import { ApiError } from "../../lib/api";
import { apiData, throwOnApiError } from "../../lib/generatedApi";
import {
  calculateNoteDraftProjection,
  mergeSavedNoteIds,
  noteDraftIsValid,
  noteScenarioToDraft,
  noteSimulationPayload,
  noteSimulationSentKeys,
  type NoteSimulationAssessmentDraft,
  type NoteSimulationDraft,
  type NoteSimulationUeDraft,
} from "../../lib/noteSimulations";
import { queryKeys, useNoteSimulation, useNoteSimulations, useSession } from "../../lib/queries";
import { SIMULATION_SEMESTERS } from "../../lib/simulations";
import type {
  NoteSimulationList,
  NoteSimulationScenario,
  NoteSimulationScenarioSummary,
  SimulationSemester,
} from "../../types";
import { NoteSimulationAssessmentEditor } from "./NoteSimulationAssessmentEditor";
import { NoteSimulationComparison } from "./NoteSimulationComparison";
import type { NoteSimulationResolution } from "./NoteSimulationConflictPanel";
import { NoteSimulationCreationModal } from "./NoteSimulationCreationModal";
import { NoteSimulationHeader } from "./NoteSimulationHeader";
import { NoteSimulationScenarioSelector } from "./NoteSimulationScenarioSelector";
import { NoteSimulationSemesterFilter } from "./NoteSimulationSemesterFilter";
import { NoteSimulationSummary } from "./NoteSimulationSummary";
import { NoteSimulationUeEditor } from "./NoteSimulationUeEditor";
import { NoteSimulationUeList } from "./NoteSimulationUeList";
import { domSafeKey, hasUeConflict, initialOpenUes } from "./noteSimulationPresentation";
import "./noteSimulations.css";
import { useCompactSimulationLayout } from "./useCompactSimulationLayout";

const ACTIVE_SCENARIO_KEY = "imtegrale.note-simulations.active";

type UeEditorState =
  { mode: "add"; defaultSemester: SimulationSemester | null } | { mode: "edit"; ueKey: string } | null;

type AssessmentEditorState = {
  ueKey: string;
  assessmentKey: string | null;
} | null;

function scenarioSummary(scenario: NoteSimulationScenario): NoteSimulationScenarioSummary {
  const { ues: _ues, ...summary } = scenario;
  return summary;
}

function projectionForSemester(
  projection: ReturnType<typeof calculateNoteDraftProjection>,
  semester: "all" | SimulationSemester,
) {
  if (semester === "all") return projection;
  return (
    projection.semesters.find((item) => item.semester === semester) ?? {
      average: null,
      gpa: null,
      creditsIncluded: 0,
      ueCount: 0,
      calculatedUeCount: 0,
      assessmentCount: 0,
      scoredCount: 0,
      pendingCount: 0,
    }
  );
}

export function NoteSimulationsPage() {
  const session = useSession();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const compact = useCompactSimulationLayout();
  const accountId = session.data?.account?.id ?? "anonymous";
  const simulations = useNoteSimulations();
  const [activeId, setActiveId] = useState<string | null>(() => window.localStorage.getItem(ACTIVE_SCENARIO_KEY));
  const scenario = useNoteSimulation(activeId);
  const [draft, setDraft] = useState<NoteSimulationDraft | null>(null);
  const [saveState, setSaveState] = useState<SimulationSaveState>("saved");
  const [semester, setSemester] = useState<"all" | SimulationSemester>("all");
  const [openUes, setOpenUes] = useState<Set<string>>(new Set());
  const [creationOpen, setCreationOpen] = useState(false);
  const [confirmation, setConfirmation] = useState<SimulationConfirmation>(null);
  const [comparisonOpen, setComparisonOpen] = useState(false);
  const [comparisonId, setComparisonId] = useState("");
  const [ueEditor, setUeEditor] = useState<UeEditorState>(null);
  const [assessmentEditor, setAssessmentEditor] = useState<AssessmentEditorState>(null);
  const revision = useRef(0);
  const draftRef = useRef<NoteSimulationDraft | null>(null);
  const saveStateRef = useRef<SimulationSaveState>("saved");
  const savePendingRef = useRef(false);
  const pendingFocusId = useRef<string | null>(null);
  const scenarios = useMemo(() => simulations.data?.scenarios ?? [], [simulations.data?.scenarios]);

  const cacheScenario = (next: NoteSimulationScenario) => {
    queryClient.setQueryData(queryKeys.noteSimulation(accountId, next.id), next);
    queryClient.setQueryData<NoteSimulationList>(queryKeys.noteSimulations(accountId), (current) =>
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
    if (activeId && simulations.data.scenarios.some((item) => item.id === activeId)) {
      return;
    }
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
      const next = noteScenarioToDraft(scenario.data);
      revision.current = 0;
      setDraft(next);
      setSaveState("saved");
      setSemester("all");
      setOpenUes(initialOpenUes(next.ues, compact));
      setUeEditor(null);
      setAssessmentEditor(null);
    }
  }, [activeId, compact, draft, saveState, scenario.data]);

  useEffect(() => {
    if (!compact) return;
    setOpenUes((current) => {
      if (current.size <= 1) return current;
      const preferred =
        draft?.ues.find((ue) => current.has(ue.clientKey) && hasUeConflict(ue))?.clientKey ??
        current.values().next().value;
      return preferred ? new Set([preferred]) : new Set();
    });
  }, [compact, draft?.ues]);

  useEffect(() => {
    if (!comparisonOpen || (comparisonId && comparisonId !== activeId)) {
      return;
    }
    setComparisonId(scenarios.find((item) => item.id !== activeId)?.id ?? "");
  }, [activeId, comparisonId, comparisonOpen, scenarios]);

  useEffect(() => {
    const id = pendingFocusId.current;
    if (!id) return;
    const frame = window.requestAnimationFrame(() => {
      const target = document.getElementById(id);
      if (!target) return;
      target.focus({ preventScroll: true });
      target.scrollIntoView({ block: "nearest", behavior: "auto" });
      pendingFocusId.current = null;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [assessmentEditor, draft, ueEditor]);

  const createMutation = useMutation({
    mutationFn: ({ name, importCurrent }: { name: string; importCurrent: boolean }) =>
      apiData(
        noteSimulationsScenarioCreate({
          body: { name, import_current: importCurrent },
          throwOnError: throwOnApiError,
        }),
      ),
    onSuccess: (created) => {
      const next = noteScenarioToDraft(created);
      cacheScenario(created);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.noteSimulations(accountId),
      });
      setCreationOpen(false);
      setActiveId(created.id);
      setDraft(next);
      setOpenUes(initialOpenUes(next.ues, compact));
      setSaveState("saved");
      showToast(
        created.created_from === "academic" ? "Notes actuelles importées dans la simulation" : "Simulation créée",
      );
    },
    onError: (error) => showToast(error.message, "error"),
  });

  const saveMutation = useMutation({
    mutationFn: ({
      id,
      body,
    }: {
      id: string;
      body: ReturnType<typeof noteSimulationPayload>;
      localRevision: number;
      sentKeys: ReturnType<typeof noteSimulationSentKeys>;
    }) =>
      apiData(
        noteSimulationsScenarioSave({
          path: { scenario_id: id },
          body,
          throwOnError: throwOnApiError,
        }),
      ),
    onMutate: () => setSaveState("saving"),
    onSuccess: (saved, variables) => {
      cacheScenario(saved);
      setDraft((current) =>
        current && current.id === saved.id ? mergeSavedNoteIds(current, saved, variables.sentKeys) : current,
      );
      setSaveState(revision.current === variables.localRevision ? "saved" : "dirty");
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
      if (!current || saveStateRef.current !== "dirty" || savePendingRef.current || !noteDraftIsValid(current)) {
        return;
      }
      void apiData(
        noteSimulationsScenarioSave({
          path: { scenario_id: current.id },
          body: noteSimulationPayload(current),
          throwOnError: throwOnApiError,
        }),
      ).catch(() => undefined);
    },
    [],
  );

  const validDraft = Boolean(draft && noteDraftIsValid(draft));
  useEffect(() => {
    if (!draft || saveState !== "dirty" || !validDraft || saveMutation.isPending) {
      return;
    }
    const timer = window.setTimeout(() => {
      saveMutation.mutate({
        id: draft.id,
        body: noteSimulationPayload(draft),
        localRevision: revision.current,
        sentKeys: noteSimulationSentKeys(draft),
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
          noteSimulationsScenarioDelete({
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
      const next =
        action === "duplicate"
          ? await apiData(noteSimulationsScenarioDuplicate(options))
          : action === "rebase"
            ? await apiData(noteSimulationsScenarioRebase(options))
            : await apiData(noteSimulationsScenarioReset(options));
      return { action, scenario: next };
    },
    onSuccess: ({ action, scenario: next }) => {
      setConfirmation(null);
      if (action === "delete") {
        const replacement = scenarios.find((item) => item.id !== activeId)?.id ?? null;
        queryClient.removeQueries({
          queryKey: queryKeys.noteSimulation(accountId, activeId ?? "none"),
        });
        setActiveId(replacement);
        setDraft(null);
        setOpenUes(new Set());
        void queryClient.invalidateQueries({
          queryKey: queryKeys.noteSimulations(accountId),
        });
        showToast("Simulation supprimée");
        return;
      }
      if (!next) return;
      const nextDraft = noteScenarioToDraft(next);
      cacheScenario(next);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.noteSimulations(accountId),
      });
      if (action === "duplicate") setActiveId(next.id);
      setDraft(nextDraft);
      setOpenUes(action === "rebase" ? initialOpenUes(nextDraft.ues, compact) : new Set());
      setUeEditor(null);
      setAssessmentEditor(null);
      setSaveState("saved");
      showToast(
        action === "duplicate"
          ? "Simulation dupliquée"
          : action === "rebase"
            ? "Source officielle actualisée"
            : "Simulation réinitialisée",
      );
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
    mutationFn: ({
      target,
      id,
      resolution,
    }: {
      target: "ues" | "assessments";
      id: string;
      resolution: NoteSimulationResolution;
    }) => {
      if (!activeId || !draft) throw new Error("Simulation introuvable");
      const options = {
        path: { scenario_id: activeId },
        body: { version: draft.version, resolution },
        throwOnError: throwOnApiError,
      };
      return target === "ues"
        ? apiData(
            noteSimulationsResolveUeConflict({
              ...options,
              path: { ...options.path, ue_id: id },
            }),
          )
        : apiData(
            noteSimulationsResolveAssessmentConflict({
              ...options,
              path: { ...options.path, assessment_id: id },
            }),
          );
    },
    onSuccess: (next) => {
      cacheScenario(next);
      setDraft(noteScenarioToDraft(next));
      setSaveState("saved");
      showToast("Conflit résolu");
    },
    onError: (error) => showToast(error.message, "error"),
  });

  const preserveConflictMutation = useMutation({
    mutationFn: async () => {
      if (!draft) throw new Error("Simulation introuvable");
      const created = await apiData(
        noteSimulationsScenarioCreate({
          body: {
            name: `${draft.name.slice(0, 64)} - copie locale`,
            import_current: false,
          },
          throwOnError: throwOnApiError,
        }),
      );
      const body = noteSimulationPayload({
        ...draft,
        id: created.id,
        version: created.version,
      });
      body.ues = body.ues.map(({ id: _ueId, ...ue }) => ({
        ...ue,
        assessments: ue.assessments.map(({ id: _assessmentId, ...assessment }) => assessment),
      }));
      try {
        return await apiData(
          noteSimulationsScenarioSave({
            path: { scenario_id: created.id },
            body,
            throwOnError: throwOnApiError,
          }),
        );
      } catch (error) {
        await apiData(
          noteSimulationsScenarioDelete({
            path: { scenario_id: created.id },
            query: { version: created.version },
            throwOnError: throwOnApiError,
          }),
        ).catch(() => undefined);
        throw error;
      }
    },
    onSuccess: (created) => {
      queryClient.setQueryData(queryKeys.noteSimulation(accountId, created.id), created);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.noteSimulations(accountId),
      });
      const next = noteScenarioToDraft(created);
      setActiveId(created.id);
      setDraft(next);
      setOpenUes(initialOpenUes(next.ues, compact));
      setSaveState("saved");
      showToast("Modifications conservées dans une copie");
    },
    onError: (error) => showToast(error.message, "error"),
  });

  const reloadServerVersion = async () => {
    const refreshed = await scenario.refetch();
    if (!refreshed.data) return;
    const next = noteScenarioToDraft(refreshed.data);
    revision.current = 0;
    setDraft(next);
    setOpenUes(initialOpenUes(next.ues, compact));
    setUeEditor(null);
    setAssessmentEditor(null);
    setSaveState("saved");
    showToast("Version serveur rechargée");
  };

  const updateDraft = (updater: (current: NoteSimulationDraft) => NoteSimulationDraft) => {
    revision.current += 1;
    setDraft((current) => (current ? updater(current) : current));
    setSaveState("dirty");
  };

  const saveUeEditor = (nextUe: NoteSimulationUeDraft) => {
    const adding = ueEditor?.mode === "add";
    updateDraft((current) => ({
      ...current,
      ues: adding
        ? [...current.ues, nextUe]
        : current.ues.map((ue) => (ue.clientKey === nextUe.clientKey ? nextUe : ue)),
    }));
    setOpenUes((current) => {
      if (compact) return new Set([nextUe.clientKey]);
      const next = new Set(current);
      next.add(nextUe.clientKey);
      return next;
    });
    pendingFocusId.current = `note-ue-trigger-${domSafeKey(nextUe.clientKey)}`;
    setUeEditor(null);
  };

  const deleteUe = (ue: NoteSimulationUeDraft) => {
    updateDraft((current) => ({
      ...current,
      ues: current.ues.filter((item) => item.clientKey !== ue.clientKey),
    }));
    setOpenUes((current) => {
      const next = new Set(current);
      next.delete(ue.clientKey);
      return next;
    });
    pendingFocusId.current = "note-add-ue";
    setUeEditor(null);
  };

  const saveAssessmentEditor = (nextAssessment: NoteSimulationAssessmentDraft) => {
    if (!assessmentEditor) return;
    const { ueKey, assessmentKey } = assessmentEditor;
    updateDraft((current) => ({
      ...current,
      ues: current.ues.map((ue) =>
        ue.clientKey === ueKey
          ? {
              ...ue,
              assessments: assessmentKey
                ? ue.assessments.map((assessment) =>
                    assessment.clientKey === assessmentKey ? nextAssessment : assessment,
                  )
                : [...ue.assessments, nextAssessment],
            }
          : ue,
      ),
    }));
    pendingFocusId.current = `assessment-${domSafeKey(nextAssessment.clientKey)}`;
    setAssessmentEditor(null);
  };

  const deleteAssessment = (assessment: NoteSimulationAssessmentDraft) => {
    if (!assessmentEditor) return;
    const { ueKey } = assessmentEditor;
    updateDraft((current) => ({
      ...current,
      ues: current.ues.map((ue) =>
        ue.clientKey === ueKey
          ? {
              ...ue,
              assessments: ue.assessments.filter((item) => item.clientKey !== assessment.clientKey),
            }
          : ue,
      ),
    }));
    pendingFocusId.current = `note-ue-trigger-${domSafeKey(ueKey)}`;
    setAssessmentEditor(null);
  };

  const handleSemesterChange = (nextSemester: "all" | SimulationSemester) => {
    setSemester(nextSemester);
    if (nextSemester === "all") return;
    const visibleKeys = new Set(
      draft?.ues.filter((ue) => ue.semester === nextSemester).map((ue) => ue.clientKey) ?? [],
    );
    setOpenUes((current) => new Set([...current].filter((key) => visibleKeys.has(key))));
  };

  const toggleUe = (ue: NoteSimulationUeDraft) => {
    setOpenUes((current) => {
      if (current.has(ue.clientKey)) {
        const next = new Set(current);
        next.delete(ue.clientKey);
        return next;
      }
      if (compact) return new Set([ue.clientKey]);
      const next = new Set(current);
      next.add(ue.clientKey);
      return next;
    });
  };

  const projection = useMemo(() => calculateNoteDraftProjection(draft?.ues ?? []), [draft?.ues]);
  const selectedProjection = projectionForSemester(projection, semester);
  const visibleUes = draft?.ues.filter((ue) => semester === "all" || ue.semester === semester) ?? [];
  const availableSemesters = useMemo(
    () => SIMULATION_SEMESTERS.filter((value) => draft?.ues.some((ue) => ue.semester === value)),
    [draft?.ues],
  );
  const currentSummary = scenarios.find((item) => item.id === activeId);
  const editorDisabled = saveState === "conflict";
  const editedUe =
    ueEditor?.mode === "edit" ? (draft?.ues.find((ue) => ue.clientKey === ueEditor.ueKey) ?? null) : null;
  const assessmentUe = assessmentEditor
    ? (draft?.ues.find((ue) => ue.clientKey === assessmentEditor.ueKey) ?? null)
    : null;
  const editedAssessment =
    assessmentEditor?.assessmentKey && assessmentUe
      ? (assessmentUe.assessments.find((assessment) => assessment.clientKey === assessmentEditor.assessmentKey) ?? null)
      : null;

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
    <div className="simulations-page note-simulations-page">
      <h1 className="sr-only">Simulation de notes</h1>
      <section className="simulation-privacy-band">
        <span>
          <ShieldCheck size={20} />
        </span>
        <div>
          <strong>Laboratoire de notes privé</strong>
          <p>Teste librement des résultats futurs sans toucher aux données officielles.</p>
        </div>
        <div>
          <i>Calcul instantané</i>
          <small>Coefficients puis ECTS</small>
        </div>
      </section>

      <NoteSimulationScenarioSelector
        scenarios={scenarios}
        activeId={activeId}
        compact={compact}
        saveState={saveState}
        limit={simulations.data?.limit ?? 5}
        activeAverage={draft ? projection.average : null}
        activeUeCount={draft?.ues.length ?? 0}
        onSelect={(id) => {
          if (saveState !== "saved") return;
          window.localStorage.setItem(ACTIVE_SCENARIO_KEY, id);
          setActiveId(id);
        }}
        onCreate={() => setCreationOpen(true)}
      />

      {!scenarios.length ? (
        <section className="simulation-empty-panel">
          <EmptyState
            icon={<BarChart3 size={23} />}
            title="Aucune simulation de notes"
            detail="Importe tes résultats actuels ou construis un futur semestre à partir de zéro."
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
                <strong>Tes notes officielles ont évolué</strong>
                <p>
                  Actualise la base du scénario. Tes hypothèses restent conservées et les divergences seront signalées.
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
                <p>Tes changements locaux restent affichés sans écraser l’autre onglet.</p>
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

          <section className="note-workbench">
            <NoteSimulationHeader
              scenario={currentSummary}
              name={draft.name}
              saveState={saveState}
              valid={validDraft}
              canCompare={scenarios.length >= 2}
              actionPending={actionMutation.isPending}
              onNameChange={(name) => updateDraft((current) => ({ ...current, name }))}
              onCompare={() => setComparisonOpen(true)}
              onDuplicate={() =>
                actionMutation.mutate({
                  action: "duplicate",
                  payload: {
                    name: `${draft.name.slice(0, 68)} - copie`,
                  },
                })
              }
              onConfirm={setConfirmation}
            />

            <NoteSimulationSummary
              projection={selectedProjection}
              completionRate={
                semester === "all"
                  ? projection.completionRate
                  : selectedProjection.assessmentCount
                    ? Math.round((selectedProjection.scoredCount / selectedProjection.assessmentCount) * 100)
                    : 0
              }
              semester={semester}
            />

            <NoteSimulationSemesterFilter
              value={semester}
              semesters={availableSemesters}
              visibleCount={visibleUes.length}
              compact={compact}
              canAdd={!editorDisabled && draft.ues.length < 120}
              onChange={handleSemesterChange}
              onAdd={() =>
                setUeEditor({
                  mode: "add",
                  defaultSemester: semester === "all" ? null : semester,
                })
              }
            />

            <NoteSimulationUeList
              ues={visibleUes}
              openUes={openUes}
              compact={compact}
              disabled={editorDisabled}
              emptyTitle={semester === "all" ? "Scénario vide" : `Aucune UE en ${semester}`}
              emptyDetail={
                semester === "all"
                  ? "Ajoute une UE pour commencer ta projection."
                  : "Ajoute une UE, elle sera directement placée dans ce semestre."
              }
              onToggle={toggleUe}
              onCollapseAll={() => setOpenUes(new Set())}
              onEditUe={(ue) => setUeEditor({ mode: "edit", ueKey: ue.clientKey })}
              onEditAssessment={(ue, assessment) =>
                setAssessmentEditor({
                  ueKey: ue.clientKey,
                  assessmentKey: assessment.clientKey,
                })
              }
              onAddAssessment={(ue) =>
                setAssessmentEditor({
                  ueKey: ue.clientKey,
                  assessmentKey: null,
                })
              }
              onResolveUe={(ue, resolution) =>
                ue.id &&
                resolveMutation.mutate({
                  target: "ues",
                  id: ue.id,
                  resolution,
                })
              }
              onResolveAssessment={(assessment, resolution) =>
                assessment.id &&
                resolveMutation.mutate({
                  target: "assessments",
                  id: assessment.id,
                  resolution,
                })
              }
            />

            <details className="simulation-formula note-workbench-formula">
              <summary>
                <span>
                  <Info size={16} /> Méthode de calcul
                </span>
              </summary>
              <div className="note-formula-content">
                <p>
                  <strong>Moyenne UE = somme des notes × coefficients ÷ somme des coefficients.</strong> La moyenne
                  générale pondère ensuite chaque moyenne d’UE par ses ECTS. Une note vide est exclue, jamais remplacée
                  par zéro.
                </p>
                <div>
                  <span>
                    <strong>Rattrapage</strong>
                    <small>
                      La dernière note de rattrapage saisie remplace la moyenne normale. Si elle valide l’UE, le grade
                      potentiel devient E.
                    </small>
                  </span>
                  <span>
                    <strong>GPA dérivé</strong>
                    <small>
                      Calculé sur 4 à partir du grade potentiel de chaque UE. Il reste indicatif et n’alimente jamais le
                      classement.
                    </small>
                  </span>
                </div>
                <small>
                  Règle IMTégrale {currentSummary.formula_version} · échelle 0–20 puis 0–4 · arrondi au centième,
                  demi-supérieur · simulation non officielle.
                </small>
              </div>
            </details>
          </section>
        </>
      )}

      <NoteSimulationCreationModal
        open={creationOpen}
        sourceUeCount={simulations.data?.source.ue_count ?? 0}
        sourceAssessmentCount={simulations.data?.source.assessment_count ?? 0}
        pending={createMutation.isPending}
        onClose={() => setCreationOpen(false)}
        onCreate={(name, importCurrent) => createMutation.mutate({ name, importCurrent })}
      />
      <SimulationConfirmationModal
        action={confirmation}
        name={draft?.name ?? ""}
        pending={actionMutation.isPending}
        resetDescription="Les valeurs importées retrouveront leur état initial et les ajouts manuels disparaîtront."
        deleteBody="Cette action ne touche ni tes notes officielles ni tes autres simulations."
        resetBody="Le scénario conserve sa base académique importée."
        onClose={() => setConfirmation(null)}
        onConfirm={() => confirmation && actionMutation.mutate({ action: confirmation })}
      />
      <NoteSimulationUeEditor
        open={Boolean(ueEditor)}
        ue={editedUe}
        defaultSemester={ueEditor?.mode === "add" ? ueEditor.defaultSemester : null}
        onClose={() => setUeEditor(null)}
        onSave={saveUeEditor}
        onDelete={editedUe ? deleteUe : undefined}
      />
      <NoteSimulationAssessmentEditor
        open={Boolean(assessmentEditor && assessmentUe)}
        assessment={editedAssessment}
        ueName={assessmentUe?.title || assessmentUe?.ue_code || "Unité d’enseignement"}
        onClose={() => setAssessmentEditor(null)}
        onSave={saveAssessmentEditor}
        onDelete={editedAssessment ? deleteAssessment : undefined}
      />
      {currentSummary && (
        <NoteSimulationComparison
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
