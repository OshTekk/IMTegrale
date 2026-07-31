import type { QueryClient } from "@tanstack/react-query";
import { useSyncExternalStore } from "react";
import { clearPrivateComparisonState } from "./queries";

const PUBLIC_ID_PATTERN = /^pc_[A-Za-z0-9_-]{24}$/;
const MAX_TIMEOUT_MS = 2_147_000_000;
const terminalPublicIds = new Set<string>();
const listeners = new Set<() => void>();
const purgeCallbacks = new Set<() => void>();

export interface PrivateComparisonLease {
  authEpoch: number;
  sessionScope: string;
  publicId: string;
  expiresAt: string;
  status: "active";
  lastValidatedAt: number;
}

export function validPrivateComparisonLease(
  lease: PrivateComparisonLease | null,
  expected: { authEpoch: number; sessionScope: string },
  now = Date.now(),
): lease is PrivateComparisonLease {
  return Boolean(
    lease &&
    Number.isSafeInteger(lease.authEpoch) &&
    lease.authEpoch === expected.authEpoch &&
    PUBLIC_ID_PATTERN.test(lease.publicId) &&
    /^bss1_[0-9a-f]{64}$/.test(lease.sessionScope) &&
    lease.sessionScope === expected.sessionScope &&
    lease.status === "active" &&
    Number.isFinite(lease.lastValidatedAt) &&
    lease.lastValidatedAt > 0 &&
    Number.isFinite(Date.parse(lease.expiresAt)) &&
    Date.parse(lease.expiresAt) > now &&
    !terminalPublicIds.has(lease.publicId),
  );
}

export function blockTerminalPrivateComparison(publicId: string): boolean {
  if (!PUBLIC_ID_PATTERN.test(publicId) || terminalPublicIds.has(publicId)) return false;
  terminalPublicIds.add(publicId);
  for (const listener of [...listeners]) listener();
  return true;
}

export function purgeTerminalPrivateComparison(queryClient: QueryClient, publicId: string): void {
  if (!PUBLIC_ID_PATTERN.test(publicId)) return;
  blockTerminalPrivateComparison(publicId);
  for (const callback of [...purgeCallbacks]) {
    try {
      callback();
    } catch {
      // One consumer cannot prevent the terminal purge.
    }
  }
  clearPrivateComparisonState(queryClient);
}

export function registerPrivateComparisonPurge(callback: () => void): () => void {
  purgeCallbacks.add(callback);
  return () => purgeCallbacks.delete(callback);
}

export function usePrivateComparisonLeaseOpen(publicId: string | null): boolean {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => Boolean(publicId && !terminalPublicIds.has(publicId)),
    () => false,
  );
}

export function resetPrivateComparisonLeasesForTests(): void {
  terminalPublicIds.clear();
  purgeCallbacks.clear();
  for (const listener of [...listeners]) listener();
}

export function armPrivateDeadline(expiresAt: string, callback: () => void): () => void {
  let timer: number | undefined;
  let cancelled = false;
  const arm = () => {
    if (cancelled) return;
    const remaining = Date.parse(expiresAt) - Date.now();
    if (!Number.isFinite(remaining) || remaining <= 0) {
      callback();
      return;
    }
    timer = window.setTimeout(arm, Math.min(remaining, MAX_TIMEOUT_MS));
  };
  arm();
  return () => {
    cancelled = true;
    if (timer !== undefined) window.clearTimeout(timer);
  };
}
