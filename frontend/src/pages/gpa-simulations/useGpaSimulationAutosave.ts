import { useMutation } from "@tanstack/react-query";
import { type Dispatch, type SetStateAction, useCallback, useEffect, useRef, useState } from "react";
import type { SimulationSaveState } from "../../components/simulations/SimulationSaveIndicator";
import { simulationsSimulationSave } from "../../generated/api/sdk.gen";
import { ApiError } from "../../lib/api";
import { apiData, throwOnApiError } from "../../lib/generatedApi";
import { draftIsValid, mergeSavedIds, simulationPayload, type SimulationDraft } from "../../lib/simulations";
import type { SimulationScenario } from "../../types";

type ShowToast = (message: string, kind?: "success" | "error") => void;

export function useGpaSimulationAutosave({
  draft,
  setDraft,
  cacheScenario,
  showToast,
}: {
  draft: SimulationDraft | null;
  setDraft: Dispatch<SetStateAction<SimulationDraft | null>>;
  cacheScenario: (scenario: SimulationScenario) => void;
  showToast: ShowToast;
}) {
  const [saveState, setSaveState] = useState<SimulationSaveState>("saved");
  const revision = useRef(0);
  const draftRef = useRef<SimulationDraft | null>(null);
  const saveStateRef = useRef<SimulationSaveState>("saved");
  const savePendingRef = useRef(false);

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
      setDraft((current) =>
        current && current.id === saved.id ? mergeSavedIds(current, saved, variables.sentKeys) : current,
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

  const updateDraft = useCallback(
    (updater: (current: SimulationDraft) => SimulationDraft) => {
      revision.current += 1;
      setDraft((current) => (current ? updater(current) : current));
      setSaveState("dirty");
    },
    [setDraft],
  );

  const resetRevision = useCallback(() => {
    revision.current = 0;
  }, []);

  return {
    resetRevision,
    saveState,
    setSaveState,
    updateDraft,
    validDraft,
  };
}
