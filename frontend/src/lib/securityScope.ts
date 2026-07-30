import { useLayoutEffect, useRef, useState } from "react";
import type { Session } from "../types";

export const PRIVATE_COMPARISON_DOCUMENT_TITLE = "Comparaison privée · IMTégrale";

export function primarySessionScope(session: Session | undefined): string {
  return [
    session?.authenticated === true ? "authenticated" : "anonymous",
    session?.session_scope ?? "unscoped",
    session?.account?.id ?? "no-account",
    session?.role ?? "no-role",
    session?.auth_method ?? "no-auth-method",
    session?.private_comparisons?.available === true ? "private-comparisons" : "no-private-comparisons",
    session?.account ? "account-present" : "account-missing",
  ].join("\u001f");
}

export function useSecurityDocumentTitle(title: string) {
  useLayoutEffect(() => {
    document.title = title;
    return () => {
      document.title = title;
    };
  }, [title]);
}

type ScopedValue<T> = { scope: string; value: T };
export type SessionBoundRequest = { controller: AbortController; scope: string };

export function useSessionBoundOneShot<T>(sessionScope: string, open: boolean, onPurge: () => void) {
  const [scopedValue, setScopedValue] = useState<ScopedValue<T> | null>(null);
  const valueRef = useRef<ScopedValue<T> | null>(null);
  const requestRef = useRef<SessionBoundRequest | null>(null);
  const mountedRef = useRef(false);
  const currentScopeRef = useRef(sessionScope);
  const previousScopeRef = useRef(sessionScope);
  const openRef = useRef(open);
  const onPurgeRef = useRef(onPurge);
  currentScopeRef.current = sessionScope;
  openRef.current = open;
  onPurgeRef.current = onPurge;

  const clear = () => {
    valueRef.current = null;
    setScopedValue(null);
  };
  const abort = () => {
    requestRef.current?.controller.abort();
    requestRef.current = null;
  };
  const purge = () => {
    abort();
    clear();
    onPurgeRef.current();
  };

  useLayoutEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abort();
      valueRef.current = null;
    };
  }, []);

  useLayoutEffect(() => {
    if (previousScopeRef.current === sessionScope) return;
    previousScopeRef.current = sessionScope;
    requestRef.current?.controller.abort();
    requestRef.current = null;
    valueRef.current = null;
    setScopedValue(null);
    onPurgeRef.current();
  }, [sessionScope]);

  useLayoutEffect(() => {
    const handlePageCache = (event: PageTransitionEvent) => {
      if (!event.persisted) return;
      requestRef.current?.controller.abort();
      requestRef.current = null;
      valueRef.current = null;
      setScopedValue(null);
      onPurgeRef.current();
    };
    window.addEventListener("pagehide", handlePageCache);
    window.addEventListener("pageshow", handlePageCache);
    return () => {
      window.removeEventListener("pagehide", handlePageCache);
      window.removeEventListener("pageshow", handlePageCache);
    };
  }, []);

  const begin = (): SessionBoundRequest => {
    abort();
    const request = { controller: new AbortController(), scope: currentScopeRef.current };
    requestRef.current = request;
    return request;
  };
  const usable = (request: SessionBoundRequest) =>
    !request.controller.signal.aborted &&
    mountedRef.current &&
    openRef.current &&
    currentScopeRef.current === request.scope;
  const finish = (request: SessionBoundRequest) => {
    if (requestRef.current === request) requestRef.current = null;
    return usable(request);
  };
  const set = (request: SessionBoundRequest, value: T) => {
    if (!usable(request)) return false;
    const scoped = { scope: request.scope, value };
    valueRef.current = scoped;
    setScopedValue(scoped);
    return true;
  };
  const current = () => {
    const scoped = valueRef.current;
    return scoped?.scope === currentScopeRef.current ? scoped.value : null;
  };

  return {
    value: scopedValue?.scope === sessionScope ? scopedValue.value : null,
    begin,
    clear,
    current,
    finish,
    purge,
    set,
    usable,
  };
}
