import { type QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { api, ApiError } from "./api";
import type { CalendarEventItem, CalendarStatus, Dashboard, FipTrainingCalendar, LeaderboardMetric, LeaderboardView, NoteSimulationList, NoteSimulationScenario, Session, SettingsView, ShareToken, SimulationList, SimulationScenario, SyncStartResponse } from "../types";

export const queryKeys = {
  session: ["session"] as const,
  account: ["account"] as const,
  dashboard: (accountId: string) => ["account", accountId, "dashboard"] as const,
  settings: (accountId: string) => ["account", accountId, "settings"] as const,
  tokens: (accountId: string) => ["account", accountId, "tokens"] as const,
  simulations: (accountId: string) => ["account", accountId, "simulations"] as const,
  simulation: (accountId: string, scenarioId: string) =>
    ["account", accountId, "simulations", scenarioId] as const,
  noteSimulations: (accountId: string) => ["account", accountId, "note-simulations"] as const,
  noteSimulation: (accountId: string, scenarioId: string) =>
    ["account", accountId, "note-simulations", scenarioId] as const,
  calendarStatus: (accountId: string) => ["account", accountId, "calendar", "status"] as const,
  calendarEvents: (accountId: string, start: string, end: string) =>
    ["account", accountId, "calendar", "events", start, end] as const,
  trainingCalendar: (accountId: string) => ["account", accountId, "calendar", "training"] as const,
  leaderboardRoot: (accountId: string) => ["account", accountId, "leaderboard"] as const,
  leaderboard: (accountId: string, metric: string, campus: string, cohort: string) =>
    ["account", accountId, "leaderboard", metric, campus, cohort] as const
};

function sessionAccountId(session: Session | undefined): string | null {
  return session?.authenticated && session.account ? session.account.id : null;
}

function currentAccountId(queryClient: QueryClient): string {
  return sessionAccountId(queryClient.getQueryData<Session>(queryKeys.session)) ?? "anonymous";
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
    queryFn: async ({ client }) => {
      const previousId = sessionAccountId(client.getQueryData<Session>(queryKeys.session));
      const next = await api<Session>("/api/v1/auth/session");
      if (previousId !== sessionAccountId(next)) clearAccountState(client);
      return next;
    },
    staleTime: 30_000,
    refetchOnWindowFocus: "always",
    retry: false
  });
}

export function useDashboard() {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  return useQuery({
    queryKey: queryKeys.dashboard(accountId),
    queryFn: () => api<Dashboard>("/api/v1/dashboard"),
    staleTime: 20_000
  });
}

export function useSettings() {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  return useQuery({
    queryKey: queryKeys.settings(accountId),
    queryFn: () => api<SettingsView>("/api/v1/settings"),
    staleTime: 30_000
  });
}

export function useTokens(enabled = true) {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  return useQuery({
    queryKey: queryKeys.tokens(accountId),
    queryFn: () => api<ShareToken[]>("/api/v1/tokens"),
    enabled
  });
}

export function useSimulations(enabled = true) {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  return useQuery({
    queryKey: queryKeys.simulations(accountId),
    queryFn: () => api<SimulationList>("/api/v1/simulations"),
    enabled,
    staleTime: 20_000
  });
}

export function useSimulation(scenarioId: string | null) {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  return useQuery({
    queryKey: queryKeys.simulation(accountId, scenarioId ?? "none"),
    queryFn: () => api<SimulationScenario>(`/api/v1/simulations/${scenarioId}`),
    enabled: Boolean(scenarioId),
    staleTime: 0
  });
}

export function useNoteSimulations(enabled = true) {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  return useQuery({
    queryKey: queryKeys.noteSimulations(accountId),
    queryFn: () => api<NoteSimulationList>("/api/v1/note-simulations"),
    enabled,
    staleTime: 20_000
  });
}

export function useNoteSimulation(scenarioId: string | null) {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  return useQuery({
    queryKey: queryKeys.noteSimulation(accountId, scenarioId ?? "none"),
    queryFn: () => api<NoteSimulationScenario>(`/api/v1/note-simulations/${scenarioId}`),
    enabled: Boolean(scenarioId),
    staleTime: 0
  });
}

export function useCalendarStatus() {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  return useQuery({
    queryKey: queryKeys.calendarStatus(accountId),
    queryFn: () => api<CalendarStatus>("/api/v1/calendar/status"),
    staleTime: 60_000,
    refetchInterval: 5 * 60_000
  });
}

export function useCalendarEvents(start: string | null, end: string | null, enabled = true) {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  return useQuery({
    queryKey: queryKeys.calendarEvents(accountId, start ?? "none", end ?? "none"),
    queryFn: () => {
      const params = new URLSearchParams({ start: start!, end: end! });
      return api<CalendarEventItem[]>(`/api/v1/calendar/events?${params.toString()}`);
    },
    enabled: enabled && Boolean(start && end),
    staleTime: 5 * 60_000
  });
}

export function useFipTrainingCalendar(enabled = true) {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  return useQuery({
    queryKey: queryKeys.trainingCalendar(accountId),
    queryFn: () => api<FipTrainingCalendar>("/api/v1/calendar/training"),
    enabled,
    staleTime: Number.POSITIVE_INFINITY
  });
}

export function useConnectCalendar() {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  return useMutation({
    mutationFn: (url: string) => api<CalendarStatus>("/api/v1/calendar/subscription", {
      method: "PUT",
      body: JSON.stringify({ url })
    }),
    onSuccess: (status) => {
      client.setQueryData(queryKeys.calendarStatus(accountId), status);
      client.removeQueries({ queryKey: ["account", accountId, "calendar", "events"] });
    }
  });
}

export function useDisconnectCalendar() {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  return useMutation({
    mutationFn: () => api<void>("/api/v1/calendar/subscription", { method: "DELETE" }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.calendarStatus(accountId) });
      client.removeQueries({ queryKey: ["account", accountId, "calendar", "events"] });
    }
  });
}

export function useLeaderboard(
  metric: LeaderboardMetric,
  campus: string,
  cohort: string
) {
  const client = useQueryClient();
  const accountId = currentAccountId(client);
  const query = new URLSearchParams({ metric, campus });
  if (cohort) query.set("cohort", cohort);
  return useQuery({
    queryKey: queryKeys.leaderboard(accountId, metric, campus, cohort || "default"),
    queryFn: () => api<LeaderboardView>(`/api/v1/leaderboard?${query.toString()}`),
    staleTime: 0,
    gcTime: 0,
    refetchInterval: 10_000,
    refetchOnWindowFocus: "always"
  });
}

export function useRefreshDashboard() {
  const client = useQueryClient();
  const idempotencyKey = useRef<string | null>(null);
  return useMutation({
    mutationFn: () => {
      idempotencyKey.current ??= crypto.randomUUID();
      return api<SyncStartResponse>("/api/v1/sync", {
        method: "POST",
        body: "{}",
        headers: { "Idempotency-Key": idempotencyKey.current }
      });
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
    }
  });
}
