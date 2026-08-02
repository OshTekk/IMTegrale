import { readCsrfCookie } from "./api";
import type { Session } from "../types";

export type LogoutPhase = "idle" | "requesting" | "verifying" | "confirmed" | "failed" | "indeterminate";

export function isCurrentLogoutAttempt(attempt: number, currentAttempt: number): boolean {
  return attempt === currentAttempt;
}

export type AuthoritativeSessionState =
  { kind: "anonymous" } | { kind: "authenticated"; session: Session & { account: NonNullable<Session["account"]> } };

export type LogoutResult =
  | { kind: "confirmed" }
  | { kind: "failed"; session: Session & { account: NonNullable<Session["account"]> } }
  | { kind: "principal-changed"; session: Session & { account: NonNullable<Session["account"]> } }
  | { kind: "indeterminate" };

interface LogoutRequestOptions {
  expectedAccountId: string;
  fetchImpl?: typeof fetch;
  onPhase?: (phase: Exclude<LogoutPhase, "idle">) => void;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isConfirmedLogoutPayload(value: unknown): boolean {
  return isRecord(value) && value.ok === true;
}

function parseAuthenticatedSession(value: unknown): AuthoritativeSessionState | null {
  if (!isRecord(value) || typeof value.authenticated !== "boolean") return null;
  if (!value.authenticated) return { kind: "anonymous" };
  if (!isRecord(value.account) || typeof value.account.id !== "string" || value.account.id.length === 0) return null;
  return {
    kind: "authenticated",
    session: value as unknown as Session & { account: NonNullable<Session["account"]> },
  };
}

export async function verifyAuthoritativeSessionState(
  fetchImpl: typeof fetch = fetch,
): Promise<AuthoritativeSessionState> {
  const response = await fetchImpl("/api/v1/auth/session", {
    credentials: "same-origin",
    cache: "no-store",
  });

  if (response.status === 401) return { kind: "anonymous" };
  if (!response.ok) throw new Error();
  const parsed = parseAuthenticatedSession(await response.json());
  if (!parsed) throw new Error();
  return parsed;
}

export async function requestServerConfirmedLogout({
  expectedAccountId,
  fetchImpl = fetch,
  onPhase,
}: LogoutRequestOptions): Promise<LogoutResult> {
  onPhase?.("requesting");
  try {
    const csrfToken = readCsrfCookie();
    const response = await fetchImpl("/api/v1/auth/logout", {
      method: "POST",
      credentials: "same-origin",
      headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {},
    });
    if (response.ok && isConfirmedLogoutPayload(await response.json().catch(() => null))) {
      onPhase?.("confirmed");
      return { kind: "confirmed" };
    }
  } catch {
    // A transport error can happen either before revocation or after the server commit.
  }

  onPhase?.("verifying");
  try {
    const authority = await verifyAuthoritativeSessionState(fetchImpl);
    if (authority.kind === "anonymous") {
      onPhase?.("confirmed");
      return { kind: "confirmed" };
    }
    onPhase?.("failed");
    return authority.session.account.id === expectedAccountId
      ? { kind: "failed", session: authority.session }
      : { kind: "principal-changed", session: authority.session };
  } catch {
    onPhase?.("indeterminate");
    return { kind: "indeterminate" };
  }
}
