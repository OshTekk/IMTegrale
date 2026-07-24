import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { Dispatch, SetStateAction } from "react";
import type { SimulationSaveState } from "../../components/simulations/SimulationSaveIndicator";
import {
  simulationsSimulationCreate,
  simulationsSimulationDelete,
  simulationsSimulationDuplicate,
  simulationsSimulationRebase,
  simulationsSimulationReset,
} from "../../generated/api/sdk.gen";
import { ApiError } from "../../lib/api";
import { apiData, throwOnApiError } from "../../lib/generatedApi";
import { queryKeys } from "../../lib/queries";
import { scenarioToDraft, type SimulationDraft } from "../../lib/simulations";
import type { SimulationScenario, SimulationScenarioSummary } from "../../types";
import type { GpaSimulationEditorState } from "./gpaSimulationState";

type ShowToast = (message: string, kind?: "success" | "error") => void;

export function useGpaScenarioCommands({
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
  closeCreation,
  closeConfirmation,
  showToast,
}: {
  accountId: string;
  activeId: string | null;
  draft: SimulationDraft | null;
  editor: GpaSimulationEditorState;
  scenarios: SimulationScenarioSummary[];
  cacheScenario: (scenario: SimulationScenario) => void;
  setActiveId: Dispatch<SetStateAction<string | null>>;
  setDraft: Dispatch<SetStateAction<SimulationDraft | null>>;
  setEditor: Dispatch<SetStateAction<GpaSimulationEditorState>>;
  setSaveState: Dispatch<SetStateAction<SimulationSaveState>>;
  closeCreation: () => void;
  closeConfirmation: () => void;
  showToast: ShowToast;
}) {
  const queryClient = useQueryClient();

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
      closeCreation();
      setActiveId(created.id);
      setDraft(scenarioToDraft(created));
      setEditor(null);
      setSaveState("saved");
      showToast(created.created_from === "academic" ? "UE actuelles importées dans la simulation" : "Simulation créée");
    },
    onError: (error) => showToast(error.message, "error"),
  });

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
      const next =
        action === "duplicate"
          ? await apiData(simulationsSimulationDuplicate(options))
          : action === "rebase"
            ? await apiData(simulationsSimulationRebase(options))
            : await apiData(simulationsSimulationReset(options));
      return { action, scenario: next };
    },
    onSuccess: ({ action, scenario: next }) => {
      closeConfirmation();
      if (action === "delete") {
        const replacement = scenarios.find((item) => item.id !== activeId)?.id ?? null;
        queryClient.removeQueries({ queryKey: queryKeys.simulation(accountId, activeId ?? "none") });
        setActiveId(replacement);
        setDraft(null);
        setEditor(null);
        void queryClient.invalidateQueries({ queryKey: queryKeys.simulations(accountId) });
        showToast("Simulation supprimée");
        return;
      }
      if (!next) return;
      const selected =
        editor?.mode === "edit" ? draft?.entries.find((item) => item.clientKey === editor.entryKey) : null;
      const nextDraft = scenarioToDraft(next);
      cacheScenario(next);
      void queryClient.invalidateQueries({ queryKey: queryKeys.simulations(accountId) });
      if (action === "duplicate") setActiveId(next.id);
      setDraft(nextDraft);
      setSaveState("saved");
      if (action === "rebase" && selected) {
        const replacement = nextDraft.entries.find(
          (item) =>
            item.id === selected.id ||
            (item.server?.lineage_key && item.server.lineage_key === selected.server?.lineage_key),
        );
        setEditor(replacement ? { mode: "edit", entryKey: replacement.clientKey } : null);
      } else {
        setEditor(null);
      }
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
        closeConfirmation();
        setSaveState("conflict");
        return;
      }
      showToast(error.message, "error");
    },
  });

  return { actionMutation, createMutation };
}
