import type { Session } from "../types";

export type LogoutPhase = "idle" | "requesting" | "verifying" | "confirmed" | "failed" | "indeterminate";
export type LogoutState = 0 | 1 | 2 | 3 | 4 | 5;
const phases: LogoutPhase[] = ["idle", "requesting", "verifying", "confirmed", "failed", "indeterminate"];

type ActiveSession = Session & {
  authenticated: true;
  account: NonNullable<Session["account"]>;
};

export type AuthoritativeSessionState = { authenticated: false } | ActiveSession;
const anonymous = { authenticated: false } as const;
const authorityFetchOptions = { credentials: "same-origin", cache: "no-store" } as const;
type SetLogoutState = (state: Exclude<LogoutState, 0>) => void;

export function isAllowedLogoutTransition(from: LogoutPhase, to: LogoutPhase): boolean {
  return Boolean([2, 12, 56][phases.indexOf(from)]! & (1 << phases.indexOf(to)));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return (value as { constructor?: unknown } | null)?.constructor === Object;
}

function parseAuthoritativeSession(value: unknown): AuthoritativeSessionState | null {
  if (!isRecord(value)) return null;
  if (value.authenticated === false) return anonymous;
  const account = value.account;
  const learning = value.learning;
  if (value.authenticated !== true || !isRecord(account) || !isRecord(learning)) return null;
  if (
    typeof account.id !== "string" ||
    !account.id ||
    !["owner", "editor", "viewer"].includes(value.role as string) ||
    !["imt", "token", "passkey"].includes(value.auth_method as string) ||
    [value.needs_security_setup, value.needs_sync_setup, learning.available, learning.reverify_required].some(
      (item) => Boolean(item) !== item,
    )
  ) {
    return null;
  }
  return value as unknown as ActiveSession;
}

export async function verifyAuthoritativeSessionState(fetchImpl: typeof fetch): Promise<AuthoritativeSessionState> {
  const response = await fetchImpl("/api/v1/auth/session", authorityFetchOptions);
  const session =
    response.status === 401
      ? anonymous
      : response.status === 200
        ? parseAuthoritativeSession(await response.json())
        : null;
  if (!session) throw new Error();
  return session;
}

export async function requestServerConfirmedLogout(
  expectedAccountId: string,
  setState: SetLogoutState,
  fetchImpl: typeof fetch = fetch,
): Promise<AuthoritativeSessionState | undefined> {
  let state: LogoutState = 0;
  const transition = (nextState: Exclude<LogoutState, 0>) => {
    if (!([2, 12, 56][state]! & (1 << nextState))) throw new Error();
    state = nextState;
    setState(nextState);
  };
  transition(1);
  if (!navigator.onLine) {
    transition(2);
    transition(5);
    return;
  }

  try {
    const csrfToken = decodeURIComponent(document.cookie.match(/(?:^|; ?)(?:__Host-)?botnote_csrf=([^;]*)/)?.[1] ?? "");
    const response = await fetchImpl("/api/v1/auth/logout", {
      ...authorityFetchOptions,
      method: "POST",
      headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {},
    });
    const payload = response.ok && (await response.json());
    if (isRecord(payload) && payload.ok === true) {
      transition(3);
      return anonymous;
    }
  } catch {
    // A transport error is ambiguous: it can happen before or after the server commit.
  }

  transition(2);

  try {
    const authority = await verifyAuthoritativeSessionState(fetchImpl);
    if (!authority.authenticated) {
      transition(3);
      return authority;
    }
    if (authority.account.id === expectedAccountId) {
      transition(4);
      return;
    }
    // A different principal is an external authority change, not a confirmed anonymous logout.
    return authority;
  } catch {
    transition(5);
    return;
  }
}
