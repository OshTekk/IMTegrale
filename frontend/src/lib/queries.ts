import { type QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import {
  authSessionStatus,
  calendarCalendarConnect,
  calendarCalendarDisconnect,
  calendarCalendarEvents,
  calendarCalendarStatus,
  calendarFipTrainingCalendar,
  dashboardGetDashboard,
  leaderboardGetLeaderboard,
  learningCreateLearningAttempt,
  learningLearningAccess,
  learningLearningCatalog,
  learningLearningContent,
  learningLearningSource,
  learningLearningSourceReference,
  learningListLearningAttempts,
  learningListLearningProgress,
  learningResetLearningProgress,
  learningSearchLearning,
  learningUpdateLearningProgress,
  noteSimulationsScenarioGet,
  noteSimulationsScenarioList,
  settingsGetSettings,
  simulationsSimulationGet,
  simulationsSimulationList,
  syncStartSync,
  tokensListTokens,
} from "../generated/api/sdk.gen";
import { ApiError } from "./api";
import { apiData, throwOnApiError } from "./generatedApi";
import type { LeaderboardMetric, LearningAttemptCreate, LearningProgressUpdate, Session } from "../types";

export const queryKeys = {
  session: ["session"] as const,
  account: ["account"] as const,
  dashboard: (accountId: string) => ["account", accountId, "dashboard"] as const,
  settings: (accountId: string) => ["account", accountId, "settings"] as const,
  tokens: (accountId: string) => ["account", accountId, "tokens"] as const,
  simulations: (accountId: string) => ["account", accountId, "simulations"] as const,
  simulation: (accountId: string, scenarioId: string) => ["account", accountId, "simulations", scenarioId] as const,
  noteSimulations: (accountId: string) => ["account", accountId, "note-simulations"] as const,
  noteSimulation: (accountId: string, scenarioId: string) =>
    ["account", accountId, "note-simulations", scenarioId] as const,
  calendarStatus: (accountId: string) => ["account", accountId, "calendar", "status"] as const,
  calendarEvents: (accountId: string, start: string, end: string) =>
    ["account", accountId, "calendar", "events", start, end] as const,
  trainingCalendar: (accountId: string) => ["account", accountId, "calendar", "training"] as const,
  learningRoot: (accountId: string, catalogVersion: string) =>
    ["account", accountId, "learning", catalogVersion] as const,
  learningAccess: (accountId: string, catalogVersion: string) =>
    ["account", accountId, "learning", catalogVersion, "access"] as const,
  learningCatalog: (accountId: string, catalogVersion: string) =>
    ["account", accountId, "learning", catalogVersion, "catalog"] as const,
  learningContent: (accountId: string, catalogVersion: string, contentId: string) =>
    ["account", accountId, "learning", catalogVersion, "content", contentId] as const,
  learningSource: (accountId: string, catalogVersion: string, sourceId: string) =>
    ["account", accountId, "learning", catalogVersion, "source", sourceId] as const,
  learningReference: (accountId: string, catalogVersion: string, contentId: string, referenceId: string) =>
    ["account", accountId, "learning", catalogVersion, "reference", contentId, referenceId] as const,
  learningSearch: (accountId: string, catalogVersion: string, search: string) =>
    ["account", accountId, "learning", catalogVersion, "search", search] as const,
  learningProgress: (accountId: string, catalogVersion: string) =>
    ["account", accountId, "learning", catalogVersion, "progress"] as const,
  learningAttempts: (accountId: string, catalogVersion: string) =>
    ["account", accountId, "learning", catalogVersion, "attempts"] as const,
  leaderboardRoot: (accountId: string) => ["account", accountId, "leaderboard"] as const,
  leaderboard: (accountId: string, metric: string, campus: string, cohort: string) =>
    ["account", accountId, "leaderboard", metric, campus, cohort] as const,
};

function sessionAccountId(session: Session | undefined): string | null {
  return session?.authenticated && session.account ? session.account.id : null;
}

function sessionCapabilityScope(session: Session | undefined): string {
  return [
    session?.authenticated === true ? "authenticated" : "anonymous",
    sessionAccountId(session) ?? "none",
    session?.role ?? "none",
    session?.auth_method ?? "none",
    session?.learning?.available === true ? "learning" : "no-learning",
    session?.learning?.reverify_required === true ? "reverify" : "fresh",
    session?.learning?.catalog_version ?? "no-catalog",
  ].join("\u001f");
}

export function clearAccountStateOnCapabilityChange(
  queryClient: QueryClient,
  previous: Session | undefined,
  next: Session,
): void {
  if (sessionCapabilityScope(previous) !== sessionCapabilityScope(next)) {
    clearAccountState(queryClient);
  }
}

function currentAccountId(queryClient: QueryClient): string {
  return sessionAccountId(queryClient.getQueryData<Session>(queryKeys.session)) ?? "anonymous";
}

function currentLearningScope(queryClient: QueryClient): { accountId: string; catalogVersion: string } {
  const session = queryClient.getQueryData<Session>(queryKeys.session);
  return {
    accountId: sessionAccountId(session) ?? "anonymous",
    catalogVersion: session?.learning?.catalog_version ?? "unavailable",
  };
}

export function clearAccountState(queryClient: QueryClient): void {
  queryClient.cancelQueries({ queryKey: queryKeys.account });
  queryClient.removeQueries({ queryKey: queryKeys.account });
  queryClient.getMutationCache().clear();
}

export function replaceSessionState(queryClient: QueryClient, session: Session): void {
  clearAccountState(queryClient);
  queryClient.setQueryData(queryKeys.session, session);
}

export function useSession() {
  return useQuery({
    queryKey: queryKeys.session,
    queryFn: async ({ client }): Promise<Session> => {
      const previous = client.getQueryData<Session>(queryKeys.session);
      const next = await apiData(authSessionStatus({ throwOnError: throwOnApiError }));
      clearAccountStateOnCapabilityChange(client, previous, next);
      return next;
    },
    staleTime: 30_000,
    refetchOnWindowFocus: "always",
    retry: false,
  });
}

export function useDashboard() {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  return useQuery({
    queryKey: queryKeys.dashboard(accountId),
    queryFn: () => apiData(dashboardGetDashboard({ throwOnError: throwOnApiError })),
    staleTime: 20_000,
  });
}

export function useLearningAccess(enabled = true) {
  const client = useQueryClient();
  const { accountId, catalogVersion } = currentLearningScope(client);
  return useQuery({
    queryKey: queryKeys.learningAccess(accountId, catalogVersion),
    queryFn: () => apiData(learningLearningAccess({ throwOnError: throwOnApiError })),
    enabled,
    staleTime: 0,
    retry: false,
  });
}

export function useLearningCatalog(enabled = true) {
  const client = useQueryClient();
  const { accountId, catalogVersion } = currentLearningScope(client);
  return useQuery({
    queryKey: queryKeys.learningCatalog(accountId, catalogVersion),
    queryFn: () => apiData(learningLearningCatalog({ throwOnError: throwOnApiError })),
    enabled,
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  });
}

export function useLearningContent(contentId: string | null, enabled = true) {
  const client = useQueryClient();
  const { accountId, catalogVersion } = currentLearningScope(client);
  return useQuery({
    queryKey: queryKeys.learningContent(accountId, catalogVersion, contentId ?? "none"),
    queryFn: () =>
      apiData(
        learningLearningContent({
          path: { content_id: contentId! },
          throwOnError: throwOnApiError,
        }),
      ),
    enabled: enabled && Boolean(contentId),
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  });
}

export function useLearningSource(sourceId: string | null, enabled = true) {
  const client = useQueryClient();
  const { accountId, catalogVersion } = currentLearningScope(client);
  return useQuery({
    queryKey: queryKeys.learningSource(accountId, catalogVersion, sourceId ?? "none"),
    queryFn: () =>
      apiData(
        learningLearningSource({
          path: { source_id: sourceId! },
          throwOnError: throwOnApiError,
        }),
      ),
    enabled: enabled && Boolean(sourceId),
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  });
}

export function useLearningReference(contentId: string | null, referenceId: string | null, enabled = true) {
  const client = useQueryClient();
  const { accountId, catalogVersion } = currentLearningScope(client);
  return useQuery({
    queryKey: queryKeys.learningReference(accountId, catalogVersion, contentId ?? "none", referenceId ?? "none"),
    queryFn: () =>
      apiData(
        learningLearningSourceReference({
          path: { content_id: contentId!, reference_id: referenceId! },
          throwOnError: throwOnApiError,
        }),
      ),
    enabled: enabled && Boolean(contentId && referenceId),
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  });
}

export function useLearningSearch(search: string, enabled = true) {
  const client = useQueryClient();
  const { accountId, catalogVersion } = currentLearningScope(client);
  const normalizedSearch = search.trim();
  return useQuery({
    queryKey: queryKeys.learningSearch(accountId, catalogVersion, normalizedSearch),
    queryFn: () =>
      apiData(
        learningSearchLearning({
          body: {
            query: normalizedSearch,
            filters: { entity_types: ["concept", "lesson", "exercise", "pc_td", "past_exam", "source"] },
            limit: 20,
          },
          throwOnError: throwOnApiError,
        }),
      ),
    enabled: enabled && normalizedSearch.length >= 2,
    staleTime: 30_000,
    retry: false,
  });
}

export function useLearningProgress(enabled = true) {
  const client = useQueryClient();
  const { accountId, catalogVersion } = currentLearningScope(client);
  return useQuery({
    queryKey: queryKeys.learningProgress(accountId, catalogVersion),
    queryFn: () => apiData(learningListLearningProgress({ throwOnError: throwOnApiError })),
    enabled,
    staleTime: 10_000,
    retry: false,
  });
}

export function useUpdateLearningProgress() {
  const client = useQueryClient();
  const { accountId, catalogVersion } = currentLearningScope(client);
  return useMutation({
    mutationFn: ({ contentId, update }: { contentId: string; update: LearningProgressUpdate }) =>
      apiData(
        learningUpdateLearningProgress({
          path: { content_id: contentId },
          body: update,
          throwOnError: throwOnApiError,
        }),
      ),
    onSuccess: () =>
      client.invalidateQueries({
        queryKey: queryKeys.learningProgress(accountId, catalogVersion),
      }),
  });
}

export function useDeleteLearningProgress() {
  const client = useQueryClient();
  const { accountId, catalogVersion } = currentLearningScope(client);
  return useMutation({
    mutationFn: () => apiData(learningResetLearningProgress({ throwOnError: throwOnApiError })),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.learningProgress(accountId, catalogVersion) });
      client.invalidateQueries({ queryKey: queryKeys.learningAttempts(accountId, catalogVersion) });
    },
  });
}

export function useLearningAttempts(enabled = true) {
  const client = useQueryClient();
  const { accountId, catalogVersion } = currentLearningScope(client);
  return useQuery({
    queryKey: queryKeys.learningAttempts(accountId, catalogVersion),
    queryFn: () => apiData(learningListLearningAttempts({ throwOnError: throwOnApiError })),
    enabled,
    staleTime: 10_000,
    retry: false,
  });
}

export function useCreateLearningAttempt() {
  const client = useQueryClient();
  const { accountId, catalogVersion } = currentLearningScope(client);
  return useMutation({
    mutationFn: (attempt: LearningAttemptCreate) =>
      apiData(
        learningCreateLearningAttempt({
          body: attempt,
          throwOnError: throwOnApiError,
        }),
      ),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.learningAttempts(accountId, catalogVersion) });
      client.invalidateQueries({ queryKey: queryKeys.learningProgress(accountId, catalogVersion) });
    },
  });
}

export function useSettings() {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  return useQuery({
    queryKey: queryKeys.settings(accountId),
    queryFn: () => apiData(settingsGetSettings({ throwOnError: throwOnApiError })),
    staleTime: 30_000,
  });
}

export function useTokens(enabled = true) {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  return useQuery({
    queryKey: queryKeys.tokens(accountId),
    queryFn: () => apiData(tokensListTokens({ throwOnError: throwOnApiError })),
    enabled,
  });
}

export function useSimulations(enabled = true) {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  return useQuery({
    queryKey: queryKeys.simulations(accountId),
    queryFn: () => apiData(simulationsSimulationList({ throwOnError: throwOnApiError })),
    enabled,
    staleTime: 20_000,
  });
}

export function useSimulation(scenarioId: string | null) {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  return useQuery({
    queryKey: queryKeys.simulation(accountId, scenarioId ?? "none"),
    queryFn: () =>
      apiData(
        simulationsSimulationGet({
          path: { scenario_id: scenarioId! },
          throwOnError: throwOnApiError,
        }),
      ),
    enabled: Boolean(scenarioId),
    staleTime: 0,
  });
}

export function useNoteSimulations(enabled = true) {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  return useQuery({
    queryKey: queryKeys.noteSimulations(accountId),
    queryFn: () => apiData(noteSimulationsScenarioList({ throwOnError: throwOnApiError })),
    enabled,
    staleTime: 20_000,
  });
}

export function useNoteSimulation(scenarioId: string | null) {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  return useQuery({
    queryKey: queryKeys.noteSimulation(accountId, scenarioId ?? "none"),
    queryFn: () =>
      apiData(
        noteSimulationsScenarioGet({
          path: { scenario_id: scenarioId! },
          throwOnError: throwOnApiError,
        }),
      ),
    enabled: Boolean(scenarioId),
    staleTime: 0,
  });
}

export function useCalendarStatus() {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  return useQuery({
    queryKey: queryKeys.calendarStatus(accountId),
    queryFn: () => apiData(calendarCalendarStatus({ throwOnError: throwOnApiError })),
    staleTime: 60_000,
    refetchInterval: 5 * 60_000,
  });
}

export function useCalendarEvents(start: string | null, end: string | null, enabled = true) {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  return useQuery({
    queryKey: queryKeys.calendarEvents(accountId, start ?? "none", end ?? "none"),
    queryFn: () =>
      apiData(
        calendarCalendarEvents({
          query: { start: start!, end: end! },
          throwOnError: throwOnApiError,
        }),
      ),
    enabled: enabled && Boolean(start && end),
    staleTime: 5 * 60_000,
  });
}

export function useFipTrainingCalendar(enabled = true) {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  return useQuery({
    queryKey: queryKeys.trainingCalendar(accountId),
    queryFn: () => apiData(calendarFipTrainingCalendar({ throwOnError: throwOnApiError })),
    enabled,
    staleTime: Number.POSITIVE_INFINITY,
  });
}

export function useConnectCalendar() {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  return useMutation({
    mutationFn: (url: string) =>
      apiData(
        calendarCalendarConnect({
          body: { url },
          throwOnError: throwOnApiError,
        }),
      ),
    onSuccess: (status) => {
      client.setQueryData(queryKeys.calendarStatus(accountId), status);
      client.removeQueries({ queryKey: ["account", accountId, "calendar", "events"] });
    },
  });
}

export function useDisconnectCalendar() {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  return useMutation({
    mutationFn: () => apiData(calendarCalendarDisconnect({ throwOnError: throwOnApiError })),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.calendarStatus(accountId) });
      client.removeQueries({ queryKey: ["account", accountId, "calendar", "events"] });
    },
  });
}

export function useLeaderboard(metric: LeaderboardMetric, campus: string, cohort: string) {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  return useQuery({
    queryKey: queryKeys.leaderboard(accountId, metric, campus, cohort || "default"),
    queryFn: () =>
      apiData(
        leaderboardGetLeaderboard({
          query: { metric, campus, cohort: cohort || undefined },
          throwOnError: throwOnApiError,
        }),
      ),
    staleTime: 0,
    gcTime: 0,
    refetchInterval: 10_000,
    refetchOnWindowFocus: "always",
  });
}

export function useRefreshDashboard() {
  const client = useQueryClient();
  const idempotencyKey = useRef<string | null>(null);
  return useMutation({
    mutationFn: () => {
      idempotencyKey.current ??= crypto.randomUUID();
      return apiData(
        syncStartSync({
          headers: { "Idempotency-Key": idempotencyKey.current },
          throwOnError: throwOnApiError,
        }),
      );
    },
    onSuccess: () => {
      idempotencyKey.current = null;
      window.setTimeout(() => client.invalidateQueries({ queryKey: queryKeys.account }), 1500);
    },
    onError: (error) => {
      if (error instanceof ApiError) idempotencyKey.current = null;
    },
    onSettled: () => {
      client.invalidateQueries({ queryKey: queryKeys.account });
    },
  });
}
