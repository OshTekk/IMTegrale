import { useQueryClient } from "@tanstack/react-query";
import { createContext, type ReactNode, useCallback, useContext, useLayoutEffect, useRef, useState } from "react";
import { authSessionStatus } from "../generated/api/sdk.gen";
import type { Session } from "../types";
import { apiData, throwOnApiError } from "./generatedApi";
import { clearAccountState, clearPrivateComparisonState, queryKeys } from "./queries";
import { broadcastSessionChange, subscribeToSessionChanges } from "./sessionSync";

export type SessionSecurityStatus = "verifying" | "verified" | "invalidating" | "expired" | "anonymous";

export type SessionSecurityInvalidationReason =
  | "session-change"
  | "pagehide"
  | "bfcache"
  | "expired"
  | "invalid-session"
  | "offline-revalidation"
  | "revalidation-failed";

type PurgeCallback = (reason: SessionSecurityInvalidationReason) => void;

interface VerifiedSecurityMetadata {
  scope: string;
  monotonicDeadline: number;
  wallDeadline: number;
}

interface InternalSecurityState {
  status: SessionSecurityStatus;
  scope: string | null;
  monotonicDeadline: number | null;
  wallDeadline: number | null;
}

export interface SessionSecurityContextValue {
  status: SessionSecurityStatus;
  scope: string | null;
  isScopeVerified: (scope?: string | null) => boolean;
  revalidateScope: (scope: string) => Promise<boolean>;
  registerPurge: (callback: PurgeCallback) => () => void;
  invalidate: (
    reason: SessionSecurityInvalidationReason,
    options?: { clearAllAccountState?: boolean; status?: SessionSecurityStatus },
  ) => void;
}

const SESSION_SCOPE_PATTERN = /^bss1_[0-9a-f]{64}$/;
const MAX_TIMEOUT_MS = 2_147_000_000;
const SAFE_DOCUMENT_TITLE = "Session · IMTégrale";
const VERIFICATION_ERROR = new Error();

function monotonicNow(): number {
  return performance.now();
}

function sessionMarker(session: Session | undefined): string {
  return `${session?.session_scope}:${session?.server_time}`;
}

export function verifiedSessionSecurityMetadata(session: Session | undefined): VerifiedSecurityMetadata | null {
  if (
    session?.authenticated !== true ||
    !SESSION_SCOPE_PATTERN.test(session.session_scope ?? "") ||
    typeof session.session_expires_at !== "string" ||
    typeof session.server_time !== "string"
  ) {
    return null;
  }
  const expiresAt = Date.parse(session.session_expires_at);
  const serverTime = Date.parse(session.server_time);
  const remaining = expiresAt - serverTime;
  if (!Number.isFinite(remaining) || remaining <= 0) return null;
  return {
    scope: session.session_scope!,
    monotonicDeadline: monotonicNow() + remaining,
    wallDeadline: Date.now() + remaining,
  };
}

function blockedSecurityState(status: SessionSecurityStatus): InternalSecurityState {
  return { status, scope: null, monotonicDeadline: null, wallDeadline: null };
}

function initialSecurityState(session: Session | undefined, sessionPending: boolean): InternalSecurityState {
  if (sessionPending) return blockedSecurityState("verifying");
  if (session?.authenticated !== true) {
    return blockedSecurityState("anonymous");
  }
  const metadata = verifiedSessionSecurityMetadata(session);
  if (!metadata) return blockedSecurityState("invalidating");
  return { status: "verified", ...metadata };
}

function isDeadlineExpired(state: InternalSecurityState): boolean {
  return (
    state.monotonicDeadline === null ||
    state.wallDeadline === null ||
    monotonicNow() >= state.monotonicDeadline ||
    Date.now() >= state.wallDeadline
  );
}

function applyDocumentSecurityState(status: SessionSecurityStatus): void {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.sessionSecurity = status;
  const blocked = status !== "verified";
  for (const element of document.querySelectorAll<HTMLElement>("[data-private-sensitive]")) {
    element.hidden = blocked;
    element.inert = blocked;
  }
  if (blocked && status !== "anonymous") document.title = SAFE_DOCUMENT_TITLE;
}

const blockedContext: SessionSecurityContextValue = {
  status: "verifying",
  scope: null,
  isScopeVerified: () => false,
  revalidateScope: async () => false,
  registerPurge: () => () => {},
  invalidate: () => {},
};

const SessionSecurityContext = createContext<SessionSecurityContextValue>(blockedContext);

export function useSessionSecurity(): SessionSecurityContextValue {
  return useContext(SessionSecurityContext);
}

export function fetchSecuritySession(): Promise<Session> {
  return apiData(
    authSessionStatus({
      headers: {
        "Cache-Control": "no-store",
        Pragma: "no-cache",
      },
      throwOnError: throwOnApiError,
    }),
  ) as Promise<Session>;
}

export function useVerifiedSessionRequest() {
  const { isScopeVerified, registerPurge, revalidateScope } = useSessionSecurity();
  const controllersRef = useRef(new Set<AbortController>());

  useLayoutEffect(() => {
    const abortAll = () => {
      for (const controller of controllersRef.current) controller.abort();
      controllersRef.current.clear();
    };
    const unregister = registerPurge(abortAll);
    return () => {
      unregister();
      abortAll();
    };
  }, [registerPurge]);

  return useCallback(
    async <T,>(expectedScope: string, operation: (signal: AbortSignal) => Promise<T>): Promise<T> => {
      if (!(await revalidateScope(expectedScope))) {
        throw VERIFICATION_ERROR;
      }
      if (!isScopeVerified(expectedScope)) throw VERIFICATION_ERROR;
      const controller = new AbortController();
      controllersRef.current.add(controller);
      try {
        const value = await operation(controller.signal);
        if (controller.signal.aborted || !isScopeVerified(expectedScope)) {
          throw VERIFICATION_ERROR;
        }
        return value;
      } finally {
        controllersRef.current.delete(controller);
      }
    },
    [isScopeVerified, revalidateScope],
  );
}

export function SessionSecurityBoundary({
  children,
  session,
  sessionPending,
  refetchSession,
}: {
  children: ReactNode;
  session: Session | undefined;
  sessionPending: boolean;
  refetchSession: () => Promise<Session | undefined>;
}) {
  const queryClient = useQueryClient();
  const initial = initialSecurityState(session, sessionPending);
  const [state, setState] = useState<InternalSecurityState>(initial);
  const stateRef = useRef(initial);
  const sessionRef = useRef(session);
  const refetchRef = useRef(refetchSession);
  const purgeCallbacksRef = useRef(new Set<PurgeCallback>());
  const invalidationEpochRef = useRef(0);
  const blockedMarkerRef = useRef<string | null>(initial.status === "verified" ? null : sessionMarker(session));
  sessionRef.current = session;
  refetchRef.current = refetchSession;

  const commitState = useCallback((next: InternalSecurityState) => {
    stateRef.current = next;
    applyDocumentSecurityState(next.status);
    setState(next);
  }, []);

  const runPurgeCallbacks = useCallback((reason: SessionSecurityInvalidationReason) => {
    for (const callback of [...purgeCallbacksRef.current]) {
      try {
        callback(reason);
      } catch {
        // A failing consumer must never prevent the remaining security purge.
      }
    }
  }, []);

  const invalidate = useCallback<SessionSecurityContextValue["invalidate"]>(
    (reason, options) => {
      invalidationEpochRef.current += 1;
      const nextStatus = options?.status ?? (reason === "expired" ? "expired" : "invalidating");
      blockedMarkerRef.current = sessionMarker(sessionRef.current);
      commitState(blockedSecurityState(nextStatus));
      runPurgeCallbacks(reason);
      (options?.clearAllAccountState ? clearAccountState : clearPrivateComparisonState)(queryClient);
    },
    [commitState, queryClient, runPurgeCallbacks],
  );

  const expireIfNeeded = useCallback((): boolean => {
    const current = stateRef.current;
    if (current.status !== "verified" || !isDeadlineExpired(current)) return false;
    invalidate("expired", { clearAllAccountState: true, status: "expired" });
    broadcastSessionChange();
    return true;
  }, [invalidate]);

  const applyVerifiedSession = useCallback(
    (nextSession: Session): boolean => {
      const metadata = verifiedSessionSecurityMetadata(nextSession);
      if (!metadata) return false;
      blockedMarkerRef.current = null;
      commitState({ status: "verified", ...metadata });
      return true;
    },
    [commitState],
  );

  const refreshSession = useCallback(async () => {
    const requestEpoch = invalidationEpochRef.current;
    try {
      const next = await refetchRef.current();
      if (requestEpoch !== invalidationEpochRef.current) return;
      if (!next) {
        invalidate("revalidation-failed");
        return;
      }
      queryClient.setQueryData(queryKeys.session, next);
      if (next.authenticated !== true) {
        blockedMarkerRef.current = null;
        commitState(blockedSecurityState("anonymous"));
        return;
      }
      if (!applyVerifiedSession(next)) invalidate("invalid-session");
    } catch {
      invalidate("revalidation-failed");
    }
  }, [applyVerifiedSession, commitState, invalidate, queryClient]);

  const isScopeVerified = useCallback(
    (scope?: string | null): boolean => {
      if (expireIfNeeded()) return false;
      const current = stateRef.current;
      return current.status === "verified" && current.scope === scope && !isDeadlineExpired(current);
    },
    [expireIfNeeded],
  );

  const revalidateScope = useCallback(
    async (expectedScope: string): Promise<boolean> => {
      if (!isScopeVerified(expectedScope)) return false;
      if (navigator.onLine === false) {
        invalidate("offline-revalidation");
        return false;
      }
      const requestEpoch = invalidationEpochRef.current;
      try {
        const next = await fetchSecuritySession();
        if (requestEpoch !== invalidationEpochRef.current || !isScopeVerified(expectedScope)) {
          return false;
        }
        queryClient.setQueryData(queryKeys.session, next);
        const metadata = verifiedSessionSecurityMetadata(next);
        if (!metadata || metadata.scope !== expectedScope) {
          invalidate("session-change", { clearAllAccountState: true });
          return false;
        }
        blockedMarkerRef.current = null;
        commitState({ status: "verified", ...metadata });
        return isScopeVerified(expectedScope);
      } catch {
        invalidate("revalidation-failed");
        return false;
      }
    },
    [commitState, invalidate, isScopeVerified, queryClient],
  );

  const registerPurge = useCallback((callback: PurgeCallback) => {
    purgeCallbacksRef.current.add(callback);
    return () => purgeCallbacksRef.current.delete(callback);
  }, []);

  useLayoutEffect(() => {
    if (sessionPending) return;
    if (session?.authenticated !== true) {
      blockedMarkerRef.current = null;
      commitState(blockedSecurityState("anonymous"));
      return;
    }
    const metadata = verifiedSessionSecurityMetadata(session);
    if (!metadata) {
      if (stateRef.current.status !== "invalidating") {
        invalidate("invalid-session");
      } else {
        applyDocumentSecurityState("invalidating");
      }
      return;
    }
    if (blockedMarkerRef.current === sessionMarker(session) && stateRef.current.status !== "verified") {
      applyDocumentSecurityState(stateRef.current.status);
      return;
    }
    applyVerifiedSession(session);
  }, [applyVerifiedSession, commitState, invalidate, session, sessionPending]);

  useLayoutEffect(() => {
    if (state.status !== "verified") return;
    let timer: number | undefined;
    const arm = () => {
      const current = stateRef.current;
      if (current.status !== "verified") return;
      const monotonicRemaining = current.monotonicDeadline! - monotonicNow();
      const wallRemaining = current.wallDeadline! - Date.now();
      const remaining = Math.min(monotonicRemaining, wallRemaining);
      if (remaining <= 0) {
        expireIfNeeded();
        return;
      }
      timer = window.setTimeout(arm, Math.min(remaining, MAX_TIMEOUT_MS));
    };
    arm();
    return () => window.clearTimeout(timer);
  }, [expireIfNeeded, state.monotonicDeadline, state.status, state.wallDeadline]);

  useLayoutEffect(() => {
    const blockAndRefresh = (
      reason: SessionSecurityInvalidationReason,
      status: SessionSecurityStatus,
      clearAllAccountState = false,
    ) => {
      invalidate(reason, { clearAllAccountState, status });
      void refreshSession();
    };
    const unsubscribe = subscribeToSessionChanges(() => blockAndRefresh("session-change", "invalidating", true));
    const handlePageHide = () => {
      invalidate("pagehide", { status: "verifying" });
    };
    const handlePageShow = (event: PageTransitionEvent) => {
      if (!event.persisted) {
        expireIfNeeded();
        return;
      }
      blockAndRefresh("bfcache", "verifying", true);
    };
    const handleLiveness = () => {
      if (expireIfNeeded()) return;
      if (stateRef.current.status === "invalidating" || stateRef.current.status === "verifying") {
        void refreshSession();
      }
    };
    const handleVisibility = () => {
      if (document.visibilityState === "visible") handleLiveness();
    };
    window.addEventListener("pagehide", handlePageHide);
    window.addEventListener("pageshow", handlePageShow);
    const livenessEvents = ["focus", "online", "offline"] as const;
    for (const event of livenessEvents) window.addEventListener(event, handleLiveness);
    document.addEventListener("visibilitychange", handleVisibility);
    document.addEventListener("resume", handleLiveness);
    return () => {
      unsubscribe();
      window.removeEventListener("pagehide", handlePageHide);
      window.removeEventListener("pageshow", handlePageShow);
      for (const event of livenessEvents) window.removeEventListener(event, handleLiveness);
      document.removeEventListener("visibilitychange", handleVisibility);
      document.removeEventListener("resume", handleLiveness);
    };
  }, [expireIfNeeded, invalidate, refreshSession]);

  const context: SessionSecurityContextValue = {
    status: state.status,
    scope: state.scope,
    isScopeVerified,
    revalidateScope,
    registerPurge,
    invalidate,
  };
  const desiredMetadata = verifiedSessionSecurityMetadata(session);
  const renderSensitive =
    state.status === "verified" && desiredMetadata !== null && desiredMetadata.scope === state.scope;

  return (
    <SessionSecurityContext.Provider value={context}>
      <div data-private-sensitive hidden={!renderSensitive} inert={!renderSensitive}>
        {renderSensitive ? children : null}
      </div>
      {!renderSensitive && state.status !== "anonymous" ? (
        <div className="app-loading" data-session-security-fallback role="status">
          <span className="loading-line" />
          <span>
            {state.status === "expired"
              ? "Session expirée. Reconnecte-toi pour continuer."
              : "Vérification de la session…"}
          </span>
        </div>
      ) : null}
    </SessionSecurityContext.Provider>
  );
}
