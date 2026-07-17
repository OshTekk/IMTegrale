import { type QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { api, ApiError } from "./api";
import type { Dashboard, LeaderboardMetric, LeaderboardView, Session, SettingsView, ShareToken, SyncStartResponse } from "../types";

export const queryKeys = {
  session: ["session"] as const,
  account: ["account"] as const,
  dashboard: (accountId: string) => ["account", accountId, "dashboard"] as const,
  settings: (accountId: string) => ["account", accountId, "settings"] as const,
  tokens: (accountId: string) => ["account", accountId, "tokens"] as const,
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
