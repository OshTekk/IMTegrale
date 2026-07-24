import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { Dispatch, SetStateAction } from "react";
import type { SimulationSaveState } from "../../components/simulations/SimulationSaveIndicator";
import {
  simulationsSimulationCreate,
  simulationsSimulationDelete,
  simulationsSimulationResolveConflict,
  simulationsSimulationSave,
} from "../../generated/api/sdk.gen";
import { apiData, throwOnApiError } from "../../lib/generatedApi";
import { queryKeys } from "../../lib/queries";
import { scenarioToDraft, simulationPayload, type SimulationDraft } from "../../lib/simulations";
import type { SimulationScenario } from "../../types";
import type { GpaSimulationEditorState } from "./gpaSimulationState";

type ShowToast = (message: string, kind?: "success" | "error") => void;

export function useGpaConflictCommands({
  accountId,
  activeId,
  draft,
  editor,
  cacheScenario,
  fetchServerVersion,
  resetRevision,
  setActiveId,
  setDraft,
  setEditor,
  setSaveState,
  showToast,
}: {
  accountId: string;
  activeId: string | null;
  draft: SimulationDraft | null;
  editor: GpaSimulationEditorState;
  cacheScenario: (scenario: SimulationScenario) => void;
  fetchServerVersion: () => Promise<SimulationScenario | undefined>;
  resetRevision: () => void;
  setActiveId: Dispatch<SetStateAction<string | null>>;
  setDraft: Dispatch<SetStateAction<SimulationDraft | null>>;
  setEditor: Dispatch<SetStateAction<GpaSimulationEditorState>>;
  setSaveState: Dispatch<SetStateAction<SimulationSaveState>>;
  showToast: ShowToast;
}) {
  const queryClient = useQueryClient();

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
      const selectedId =
        editor?.mode === "edit" ? draft?.entries.find((item) => item.clientKey === editor.entryKey)?.id : null;
      const nextDraft = scenarioToDraft(next);
      cacheScenario(next);
      setDraft(nextDraft);
      if (selectedId) {
        const selected = nextDraft.entries.find((item) => item.id === selectedId);
        setEditor(selected ? { mode: "edit", entryKey: selected.clientKey } : null);
      }
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
      setEditor(null);
      setSaveState("saved");
      showToast("Modifications conservées dans une copie");
    },
    onError: (error) => showToast(error.message, "error"),
  });

  const reloadServerVersion = async () => {
    const refreshed = await fetchServerVersion();
    if (!refreshed) return;
    resetRevision();
    setDraft(scenarioToDraft(refreshed));
    setEditor(null);
    setSaveState("saved");
    showToast("Version serveur rechargée");
  };

  return { preserveConflictMutation, reloadServerVersion, resolveMutation };
}
