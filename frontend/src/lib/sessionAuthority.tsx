import { createContext, type ReactNode, useContext, useLayoutEffect, useSyncExternalStore } from "react";
import { flushSync } from "react-dom";
import { authSessionStatus } from "../generated/api/sdk.gen";
import type { Session } from "../types";
import { isPrimaryOwnerSession } from "./auth";
import { apiData, throwOnApiError } from "./generatedApi";
import { broadcastSessionChange, subscribeToSessionChanges, type SessionTransitionMessage } from "./sessionSync";

export type SessionSecurityStatus = "verifying" | "verified" | "invalidating" | "expired" | "anonymous";

export type SessionSecurityInvalidationReason =
  | "session-change"
  | "pagehide"
  | "bfcache"
  | "expired"
  | "invalid-session"
  | "offline-revalidation"
  | "revalidation-failed"
  | "unauthorized"
  | "login"
  | "logout";

export interface SessionAuthoritySnapshot {
  securityState: SessionSecurityStatus;
  authEpoch: number;
  session: Session | undefined;
  sessionScope: string | null;
  sessionExpiresAt: string | null;
  monotonicDeadline: number | null;
  wallDeadline: number | null;
  currentRequestSequence: number;
  latestCommittedSequence: number;
  transitionReason: SessionSecurityInvalidationReason | null;
}

export interface VerifiedSessionDeadline {
  scope: string;
  expiresAt: string;
  serverTime: string;
  serverTimeMs: number;
  monotonicDeadline: number;
  wallDeadline: number;
}

export type SessionTransport = (signal: AbortSignal) => Promise<Session | undefined>;
type PurgeCallback = (reason: SessionSecurityInvalidationReason) => void;
type EpochResource = {
  authEpoch: number;
  purge: () => void;
  projectSession: (session: Session | undefined) => void;
};

const SESSION_SCOPE_PATTERN = /^bss1_[0-9a-f]{64}$/;
const MAX_SESSION_WINDOW_MS = 366 * 24 * 60 * 60 * 1_000;
const MAX_TIMEOUT_MS = 2_147_000_000;
const SAFE_DOCUMENT_TITLE = "Session · IMTégrale";

function monotonicNow(): number {
  return performance.now();
}

function wallNow(): number {
  return Date.now();
}

function sameAuthoritativePrincipal(previous: Session | undefined, next: Session): boolean {
  return (
    previous?.authenticated === true &&
    previous.account?.id === next.account?.id &&
    previous.role === next.role &&
    previous.auth_method === next.auth_method &&
    previous.private_comparisons?.available === next.private_comparisons?.available
  );
}

function blockedSnapshot(
  previous: SessionAuthoritySnapshot,
  securityState: SessionSecurityStatus,
  reason: SessionSecurityInvalidationReason | null,
  incrementEpoch: boolean,
): SessionAuthoritySnapshot {
  return {
    securityState,
    authEpoch: previous.authEpoch + (incrementEpoch ? 1 : 0),
    session: undefined,
    sessionScope: null,
    sessionExpiresAt: null,
    monotonicDeadline: null,
    wallDeadline: null,
    currentRequestSequence: previous.currentRequestSequence,
    latestCommittedSequence: previous.latestCommittedSequence,
    transitionReason: reason,
  };
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

function publishSynchronously(listeners: Set<() => void>): void {
  if (listeners.size === 0) return;
  if (typeof document === "undefined") {
    for (const listener of [...listeners]) listener();
    return;
  }
  flushSync(() => {
    for (const listener of [...listeners]) listener();
  });
}

export function sessionDeadlineFromResponse(
  session: Session | undefined,
  requestStarted: number,
  requestFinished: number,
  receivedWallTime = wallNow(),
): VerifiedSessionDeadline | null {
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
  const roundTrip = requestFinished - requestStarted;
  const remaining = expiresAt - serverTime - roundTrip;
  if (
    !Number.isFinite(expiresAt) ||
    !Number.isFinite(serverTime) ||
    !Number.isFinite(roundTrip) ||
    roundTrip < 0 ||
    !Number.isFinite(remaining) ||
    remaining <= 0 ||
    remaining > MAX_SESSION_WINDOW_MS
  ) {
    return null;
  }
  const monotonicDeadline = requestFinished + remaining;
  const wallDeadline = receivedWallTime + remaining;
  if (!Number.isFinite(monotonicDeadline) || !Number.isFinite(wallDeadline)) return null;
  return {
    scope: session.session_scope!,
    expiresAt: session.session_expires_at,
    serverTime: session.server_time,
    serverTimeMs: serverTime,
    monotonicDeadline,
    wallDeadline,
  };
}

export function fetchSecuritySession(signal?: AbortSignal): Promise<Session> {
  return apiData(
    authSessionStatus({
      headers: {
        "Cache-Control": "no-store",
        Pragma: "no-cache",
      },
      signal,
      throwOnError: throwOnApiError,
    }),
  ) as Promise<Session>;
}

export class SessionAuthority {
  private snapshot: SessionAuthoritySnapshot = {
    securityState: "verifying",
    authEpoch: 0,
    session: undefined,
    sessionScope: null,
    sessionExpiresAt: null,
    monotonicDeadline: null,
    wallDeadline: null,
    currentRequestSequence: 0,
    latestCommittedSequence: 0,
    transitionReason: null,
  };

  private readonly listeners = new Set<() => void>();
  private readonly purgeCallbacks = new Set<PurgeCallback>();
  private transport: SessionTransport;
  private currentAbortController: AbortController | null = null;
  private epochResource: EpochResource | null = null;
  private lastServerTimeMs: number | null = null;
  private deadlineTimer: number | undefined;
  private started = false;
  private unsubscribeSessionChanges: (() => void) | null = null;
  private readonly eventDisposers: Array<() => void> = [];

  constructor(options?: { transport?: SessionTransport; initialSession?: Session }) {
    this.transport = options?.transport ?? ((signal) => fetchSecuritySession(signal));
    if (options?.initialSession) this.seedInitialSession(options.initialSession);
  }

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  readonly getSnapshot = (): SessionAuthoritySnapshot => this.snapshot;

  readonly getServerSnapshot = (): SessionAuthoritySnapshot => this.snapshot;

  setTransport(transport: SessionTransport): void {
    this.transport = transport;
  }

  start(options?: { refresh?: boolean }): void {
    if (this.started || typeof window === "undefined") return;
    this.started = true;
    applyDocumentSecurityState(this.snapshot.securityState);
    this.unsubscribeSessionChanges = subscribeToSessionChanges((message) => {
      this.handleExternalTransition(message);
    });
    const listenWindow = <K extends keyof WindowEventMap>(type: K, listener: (event: WindowEventMap[K]) => void) => {
      window.addEventListener(type, listener as EventListener);
      this.eventDisposers.push(() => window.removeEventListener(type, listener as EventListener));
    };
    const listenDocument = (type: string, listener: EventListener) => {
      document.addEventListener(type, listener);
      this.eventDisposers.push(() => document.removeEventListener(type, listener));
    };
    listenWindow("pagehide", () => this.invalidate("pagehide", "verifying"));
    listenWindow("pageshow", (event) => {
      if (event.persisted) {
        this.invalidate("bfcache", "verifying");
        void this.refreshAuthoritativeSession();
      } else {
        this.expireIfNeeded();
      }
    });
    listenWindow("offline", () => this.invalidate("offline-revalidation", "invalidating"));
    listenWindow("online", () => {
      if (this.snapshot.securityState !== "verified") void this.refreshAuthoritativeSession();
    });
    listenWindow("focus", () => this.revalidateLiveness());
    listenDocument("visibilitychange", () => {
      if (document.visibilityState === "visible") this.revalidateLiveness();
    });
    listenDocument("resume", () => this.revalidateLiveness());
    if (options?.refresh !== false && !this.snapshot.session) void this.refreshAuthoritativeSession();
  }

  stopForTests(): void {
    this.unsubscribeSessionChanges?.();
    this.unsubscribeSessionChanges = null;
    for (const dispose of this.eventDisposers.splice(0)) dispose();
    this.abortCurrentRequest();
    this.clearDeadlineTimer();
    this.started = false;
  }

  registerPurge(callback: PurgeCallback): () => void {
    this.purgeCallbacks.add(callback);
    return () => this.purgeCallbacks.delete(callback);
  }

  registerEpochResource(resource: EpochResource): () => void {
    if (resource.authEpoch !== this.snapshot.authEpoch) {
      resource.purge();
      return () => {};
    }
    this.epochResource = resource;
    resource.projectSession(this.snapshot.session);
    return () => {
      if (this.epochResource === resource) this.epochResource = null;
    };
  }

  isScopeVerified(scope?: string | null): boolean {
    if (this.expireIfNeeded()) return false;
    return (
      this.snapshot.securityState === "verified" &&
      this.snapshot.sessionScope === scope &&
      this.snapshot.session?.private_comparisons?.available === true &&
      this.snapshot.session !== undefined &&
      isPrimaryOwnerSession(this.snapshot.session) &&
      (typeof navigator === "undefined" || navigator.onLine !== false)
    );
  }

  async revalidateScope(expectedScope: string): Promise<boolean> {
    if (!this.isScopeVerified(expectedScope)) return false;
    if (typeof navigator !== "undefined" && navigator.onLine === false) {
      this.invalidate("offline-revalidation", "invalidating");
      return false;
    }
    const next = await this.refreshAuthoritativeSession();
    return next?.session_scope === expectedScope && this.isScopeVerified(expectedScope);
  }

  invalidate(
    reason: SessionSecurityInvalidationReason,
    status: SessionSecurityStatus = reason === "expired" ? "expired" : "invalidating",
  ): void {
    this.abortCurrentRequest();
    this.clearDeadlineTimer();
    this.snapshot = blockedSnapshot(this.snapshot, status, reason, true);
    this.lastServerTimeMs = null;
    applyDocumentSecurityState(status);
    for (const callback of [...this.purgeCallbacks]) {
      try {
        callback(reason);
      } catch {
        // One consumer cannot prevent the document-wide purge.
      }
    }
    this.epochResource?.purge();
    this.epochResource = null;
    publishSynchronously(this.listeners);
  }

  signalLocalTransition(reason: "login" | "logout" | "unauthorized" | "session-change"): void {
    this.invalidate(reason, reason === "logout" || reason === "unauthorized" ? "anonymous" : "invalidating");
    broadcastSessionChange();
    if (reason === "logout" || reason === "unauthorized") {
      this.commitAnonymous(this.snapshot.currentRequestSequence);
    }
    if (reason !== "logout") void this.refreshAuthoritativeSession();
  }

  async refreshAuthoritativeSession(): Promise<Session | undefined> {
    if (typeof navigator !== "undefined" && navigator.onLine === false) {
      this.invalidate("offline-revalidation", "invalidating");
      return undefined;
    }
    this.abortCurrentRequest();
    const controller = new AbortController();
    this.currentAbortController = controller;
    const requestEpoch = this.snapshot.authEpoch;
    const requestSequence = this.snapshot.currentRequestSequence + 1;
    const requestStarted = monotonicNow();
    this.snapshot = {
      ...this.snapshot,
      currentRequestSequence: requestSequence,
    };
    this.emit();

    let next: Session | undefined;
    try {
      next = await this.transport(controller.signal);
    } catch {
      if (this.requestCanPublish(requestEpoch, requestSequence, controller)) {
        this.invalidate("revalidation-failed", "invalidating");
      }
      return undefined;
    } finally {
      if (this.currentAbortController === controller) this.currentAbortController = null;
    }
    const requestFinished = monotonicNow();
    if (!this.requestCanPublish(requestEpoch, requestSequence, controller)) return undefined;
    if (!next || next.authenticated !== true) {
      const changed = this.snapshot.securityState === "verified" || this.snapshot.session?.authenticated === true;
      if (changed) this.invalidate("session-change", "anonymous");
      this.commitAnonymous(requestSequence);
      return next;
    }
    const metadata = sessionDeadlineFromResponse(next, requestStarted, requestFinished);
    if (!metadata) {
      this.invalidate("invalid-session", "expired");
      return undefined;
    }
    if (this.lastServerTimeMs !== null && this.snapshot.sessionScope === metadata.scope) {
      if (!sameAuthoritativePrincipal(this.snapshot.session, next) || metadata.serverTimeMs < this.lastServerTimeMs) {
        this.invalidate("invalid-session", "expired");
        return undefined;
      }
      metadata.monotonicDeadline = Math.min(metadata.monotonicDeadline, this.snapshot.monotonicDeadline ?? Infinity);
      metadata.wallDeadline = Math.min(metadata.wallDeadline, this.snapshot.wallDeadline ?? Infinity);
    }
    if (
      this.snapshot.securityState === "verified" &&
      this.snapshot.sessionScope !== null &&
      this.snapshot.sessionScope !== metadata.scope
    ) {
      this.invalidate("session-change", "invalidating");
      return this.refreshAuthoritativeSession();
    }
    if (
      monotonicNow() >= metadata.monotonicDeadline ||
      wallNow() >= metadata.wallDeadline ||
      metadata.monotonicDeadline <= requestFinished
    ) {
      this.invalidate("expired", "expired");
      return undefined;
    }
    this.lastServerTimeMs = metadata.serverTimeMs;
    this.snapshot = {
      securityState: "verified",
      authEpoch: this.snapshot.authEpoch,
      session: next,
      sessionScope: metadata.scope,
      sessionExpiresAt: metadata.expiresAt,
      monotonicDeadline: metadata.monotonicDeadline,
      wallDeadline: metadata.wallDeadline,
      currentRequestSequence: requestSequence,
      latestCommittedSequence: requestSequence,
      transitionReason: null,
    };
    applyDocumentSecurityState("verified");
    this.epochResource?.projectSession(next);
    this.armDeadline();
    this.emit();
    return next;
  }

  /**
   * Compatibility hook for isolated component tests. Production has exactly one
   * writer: refreshAuthoritativeSession().
   */
  applyExternalSessionForTests(session: Session | undefined): void {
    const started = monotonicNow();
    const metadata = sessionDeadlineFromResponse(session, started, started);
    if (!session?.authenticated) {
      this.commitAnonymous(this.snapshot.currentRequestSequence);
      return;
    }
    if (!metadata) {
      this.invalidate("invalid-session", "expired");
      return;
    }
    if (this.snapshot.sessionScope === metadata.scope) {
      metadata.monotonicDeadline = Math.min(metadata.monotonicDeadline, this.snapshot.monotonicDeadline ?? Infinity);
      metadata.wallDeadline = Math.min(metadata.wallDeadline, this.snapshot.wallDeadline ?? Infinity);
    }
    this.lastServerTimeMs = Math.max(this.lastServerTimeMs ?? metadata.serverTimeMs, metadata.serverTimeMs);
    this.snapshot = {
      ...this.snapshot,
      securityState: "verified",
      session,
      sessionScope: metadata.scope,
      sessionExpiresAt: metadata.expiresAt,
      monotonicDeadline: metadata.monotonicDeadline,
      wallDeadline: metadata.wallDeadline,
      transitionReason: null,
    };
    applyDocumentSecurityState("verified");
    this.epochResource?.projectSession(session);
    this.armDeadline();
    this.emit();
  }

  private seedInitialSession(session: Session): void {
    const now = monotonicNow();
    const metadata = sessionDeadlineFromResponse(session, now, now);
    if (session.authenticated !== true) {
      this.snapshot = blockedSnapshot(this.snapshot, "anonymous", null, false);
      this.snapshot = { ...this.snapshot, session };
      return;
    }
    if (!metadata) {
      this.snapshot = blockedSnapshot(this.snapshot, "expired", "invalid-session", false);
      return;
    }
    this.lastServerTimeMs = metadata.serverTimeMs;
    this.snapshot = {
      ...this.snapshot,
      securityState: "verified",
      session,
      sessionScope: metadata.scope,
      sessionExpiresAt: metadata.expiresAt,
      monotonicDeadline: metadata.monotonicDeadline,
      wallDeadline: metadata.wallDeadline,
    };
    this.armDeadline();
  }

  private commitAnonymous(sequence: number): void {
    this.clearDeadlineTimer();
    this.lastServerTimeMs = null;
    this.snapshot = {
      ...blockedSnapshot(this.snapshot, "anonymous", null, false),
      session: { authenticated: false },
      currentRequestSequence: sequence,
      latestCommittedSequence: sequence,
    };
    applyDocumentSecurityState("anonymous");
    this.epochResource?.projectSession(this.snapshot.session);
    this.emit();
  }

  private handleExternalTransition(_message: SessionTransitionMessage): void {
    this.invalidate("session-change", "invalidating");
    void this.refreshAuthoritativeSession();
  }

  private revalidateLiveness(): void {
    if (this.expireIfNeeded()) return;
    if (this.snapshot.securityState !== "verified") void this.refreshAuthoritativeSession();
  }

  private requestCanPublish(epoch: number, sequence: number, controller: AbortController): boolean {
    return (
      !controller.signal.aborted &&
      this.snapshot.authEpoch === epoch &&
      this.snapshot.currentRequestSequence === sequence
    );
  }

  private abortCurrentRequest(): void {
    this.currentAbortController?.abort();
    this.currentAbortController = null;
  }

  private expireIfNeeded(): boolean {
    if (this.snapshot.securityState !== "verified") return false;
    if (
      this.snapshot.monotonicDeadline === null ||
      this.snapshot.wallDeadline === null ||
      monotonicNow() >= this.snapshot.monotonicDeadline ||
      wallNow() >= this.snapshot.wallDeadline
    ) {
      this.invalidate("expired", "expired");
      broadcastSessionChange();
      return true;
    }
    return false;
  }

  private armDeadline(): void {
    this.clearDeadlineTimer();
    if (this.snapshot.securityState !== "verified") return;
    const arm = () => {
      if (this.expireIfNeeded()) return;
      const remaining = Math.min(
        this.snapshot.monotonicDeadline! - monotonicNow(),
        this.snapshot.wallDeadline! - wallNow(),
      );
      this.deadlineTimer = window.setTimeout(arm, Math.min(Math.max(remaining, 0), MAX_TIMEOUT_MS));
    };
    if (typeof window !== "undefined") arm();
  }

  private clearDeadlineTimer(): void {
    if (this.deadlineTimer !== undefined && typeof window !== "undefined") {
      window.clearTimeout(this.deadlineTimer);
    }
    this.deadlineTimer = undefined;
  }

  private emit(): void {
    for (const listener of [...this.listeners]) listener();
  }
}

const SessionAuthorityContext = createContext<SessionAuthority | null>(null);

export function SessionAuthorityRoot({
  authority,
  children,
  autoStart = true,
}: {
  authority: SessionAuthority;
  children: ReactNode;
  autoStart?: boolean;
}) {
  useLayoutEffect(() => {
    if (autoStart) authority.start();
  }, [authority, autoStart]);
  return <SessionAuthorityContext.Provider value={authority}>{children}</SessionAuthorityContext.Provider>;
}

export function useOptionalSessionAuthority(): SessionAuthority | null {
  return useContext(SessionAuthorityContext);
}

export function useSessionAuthority(): SessionAuthority {
  const authority = useOptionalSessionAuthority();
  if (!authority) throw new Error("SessionAuthorityRoot is required");
  return authority;
}

export function useSessionAuthoritySnapshot(explicitAuthority?: SessionAuthority): SessionAuthoritySnapshot {
  const contextualAuthority = useOptionalSessionAuthority();
  const authority = explicitAuthority ?? contextualAuthority;
  if (!authority) throw new Error("SessionAuthorityRoot is required");
  return useSyncExternalStore(authority.subscribe, authority.getSnapshot, authority.getServerSnapshot);
}
