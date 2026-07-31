import { useQueryClient } from "@tanstack/react-query";
import { createContext, type ReactNode, useCallback, useContext, useLayoutEffect, useRef } from "react";
import type { Session } from "../types";
import { queryKeys } from "./queries";
import {
  fetchSecuritySession,
  SessionAuthority,
  SessionAuthorityRoot,
  type SessionSecurityInvalidationReason,
  type SessionSecurityStatus,
  sessionDeadlineFromResponse,
  useOptionalSessionAuthority,
  useSessionAuthoritySnapshot,
} from "./sessionAuthority";

export type { SessionSecurityInvalidationReason, SessionSecurityStatus } from "./sessionAuthority";
export { fetchSecuritySession } from "./sessionAuthority";

type PurgeCallback = (reason: SessionSecurityInvalidationReason) => void;

export interface SessionSecurityContextValue {
  status: SessionSecurityStatus;
  scope: string | null;
  authEpoch: number;
  isScopeVerified: (scope?: string | null) => boolean;
  revalidateScope: (scope: string) => Promise<boolean>;
  registerPurge: (callback: PurgeCallback) => () => void;
  invalidate: (
    reason: SessionSecurityInvalidationReason,
    options?: { clearAllAccountState?: boolean; status?: SessionSecurityStatus },
  ) => void;
}

const VERIFICATION_ERROR = new Error("Session verification failed");

const blockedContext: SessionSecurityContextValue = {
  status: "verifying",
  scope: null,
  authEpoch: 0,
  isScopeVerified: () => false,
  revalidateScope: async () => false,
  registerPurge: () => () => {},
  invalidate: () => {},
};

const SessionSecurityContext = createContext<SessionSecurityContextValue>(blockedContext);

export function useSessionSecurity(): SessionSecurityContextValue {
  return useContext(SessionSecurityContext);
}

export function verifiedSessionSecurityMetadata(session: Session | undefined) {
  const now = performance.now();
  const metadata = sessionDeadlineFromResponse(session, now, now);
  return metadata
    ? {
        scope: metadata.scope,
        monotonicDeadline: metadata.monotonicDeadline,
        wallDeadline: metadata.wallDeadline,
      }
    : null;
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
      if (!(await revalidateScope(expectedScope)) || !isScopeVerified(expectedScope)) {
        throw VERIFICATION_ERROR;
      }
      const controller = new AbortController();
      controllersRef.current.add(controller);
      try {
        const value = await operation(controller.signal);
        if (controller.signal.aborted || !isScopeVerified(expectedScope)) throw VERIFICATION_ERROR;
        return value;
      } finally {
        controllersRef.current.delete(controller);
      }
    },
    [isScopeVerified, revalidateScope],
  );
}

function registerLegacyQueryClient(authority: SessionAuthority, queryClient: ReturnType<typeof useQueryClient>) {
  return authority.registerEpochResource({
    authEpoch: authority.getSnapshot().authEpoch,
    purge: () => {
      void queryClient.cancelQueries();
      queryClient.clear();
    },
    projectSession: (session) => {
      if (session) queryClient.setQueryData(queryKeys.session, session);
      else queryClient.removeQueries({ queryKey: queryKeys.session, exact: true });
    },
  });
}

export function SessionSecurityBoundary({
  children,
  session,
  sessionPending: _sessionPending,
  refetchSession,
}: {
  children: ReactNode;
  session?: Session;
  sessionPending?: boolean;
  refetchSession?: () => Promise<Session | undefined>;
}) {
  const inheritedAuthority = useOptionalSessionAuthority();
  const refetchRef = useRef(refetchSession);
  refetchRef.current = refetchSession;
  const localAuthorityRef = useRef<SessionAuthority | null>(null);
  if (!inheritedAuthority && !localAuthorityRef.current) {
    localAuthorityRef.current = new SessionAuthority({
      initialSession: session,
      transport: () => refetchRef.current?.() ?? fetchSecuritySession(),
    });
  }
  const authority = inheritedAuthority ?? localAuthorityRef.current!;
  const snapshot = useSessionAuthoritySnapshot(authority);
  const queryClient = useQueryClient();

  useLayoutEffect(() => {
    if (inheritedAuthority) return;
    return registerLegacyQueryClient(authority, queryClient);
  }, [authority, inheritedAuthority, queryClient]);

  useLayoutEffect(() => {
    if (inheritedAuthority) return;
    authority.setTransport(() => refetchRef.current?.() ?? fetchSecuritySession());
    authority.start({ refresh: false });
  }, [authority, inheritedAuthority]);

  useLayoutEffect(() => {
    if (inheritedAuthority || !session) return;
    authority.applyExternalSessionForTests(session);
  }, [authority, inheritedAuthority, session]);

  const context: SessionSecurityContextValue = {
    status: snapshot.securityState,
    scope: snapshot.sessionScope,
    authEpoch: snapshot.authEpoch,
    isScopeVerified: (scope) => authority.isScopeVerified(scope),
    revalidateScope: (scope) => authority.revalidateScope(scope),
    registerPurge: (callback) => authority.registerPurge(callback),
    invalidate: (reason, options) => authority.invalidate(reason, options?.status),
  };
  const renderSensitive =
    snapshot.securityState === "verified" &&
    snapshot.session !== undefined &&
    snapshot.sessionScope !== null &&
    (!session || session.session_scope === snapshot.sessionScope);

  const content = (
    <SessionSecurityContext.Provider value={context}>
      <div
        data-private-sensitive
        data-auth-epoch={snapshot.authEpoch}
        hidden={!renderSensitive}
        inert={!renderSensitive}
      >
        {renderSensitive ? children : null}
      </div>
      {!renderSensitive && snapshot.securityState !== "anonymous" ? (
        <div className="app-loading" data-session-security-fallback role="status">
          <span className="loading-line" />
          <span>
            {snapshot.securityState === "expired"
              ? "Session expirée. Reconnecte-toi pour continuer."
              : "Vérification de la session…"}
          </span>
        </div>
      ) : null}
    </SessionSecurityContext.Provider>
  );

  return inheritedAuthority ? (
    content
  ) : (
    <SessionAuthorityRoot authority={authority} autoStart={false}>
      {content}
    </SessionAuthorityRoot>
  );
}
