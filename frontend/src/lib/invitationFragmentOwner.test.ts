// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";
import type { Session } from "../types";
import { initializeInvitationFragmentOwner, resetInvitationFragmentOwnerForTests } from "./invitationFragmentOwner";
import type { SessionAuthoritySnapshot } from "./sessionAuthority";

const TOKEN = `pcinv1_${"A".repeat(43)}`;
const SCOPE = `bss1_${"a".repeat(64)}`;

function snapshot(session: Session | undefined, securityState: SessionAuthoritySnapshot["securityState"] = "verified") {
  return {
    securityState,
    authEpoch: 7,
    session,
    sessionScope: session?.session_scope ?? null,
    sessionExpiresAt: session?.session_expires_at ?? null,
    monotonicDeadline: 100_000,
    wallDeadline: 100_000,
    currentRequestSequence: 1,
    latestCommittedSequence: 1,
    transitionReason: null,
  } satisfies SessionAuthoritySnapshot;
}

function principal(options?: {
  role?: "owner" | "editor" | "viewer";
  authMethod?: "imt" | "passkey" | "token";
  available?: boolean;
  authenticated?: boolean;
}): Session {
  return {
    authenticated: options?.authenticated ?? true,
    role: options?.role ?? "owner",
    auth_method: options?.authMethod ?? "imt",
    session_scope: SCOPE,
    session_expires_at: "2099-07-30T12:05:00Z",
    server_time: "2099-07-30T12:00:00Z",
    account: { id: "synthetic", display_name: "Synthétique", imt_username: null },
    private_comparisons: { available: options?.available ?? true },
  };
}

afterEach(() => {
  resetInvitationFragmentOwnerForTests();
  window.history.replaceState(null, "", "/");
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe("InvitationFragmentOwner", () => {
  it("scrubbe au bootstrap puis livre une seule fois au scope vérifié", () => {
    window.history.replaceState({ harmless: true }, "", `/comparisons/accept#invite=${TOKEN}`);

    const owner = initializeInvitationFragmentOwner();

    expect(window.location.hash).toBe("");
    expect(window.history.state).toEqual({ harmless: true });
    expect(document.documentElement.outerHTML).not.toContain(TOKEN);
    owner.observe(snapshot(principal()));
    expect(owner.consume(7, SCOPE)).toBe(TOKEN);
    expect(owner.consume(7, SCOPE)).toBeNull();
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });

  it.each([
    ["anonyme", undefined, "anonymous" as const],
    ["viewer", principal({ role: "viewer", available: false }), "verified" as const],
    ["editor", principal({ role: "editor", available: false }), "verified" as const],
    ["owner token", principal({ authMethod: "token", available: false }), "verified" as const],
    ["feature false", principal({ available: false }), "verified" as const],
    ["session expirée", undefined, "expired" as const],
  ])("détruit définitivement le bearer pour %s", (_label, candidate, state) => {
    window.history.replaceState(null, "", `/comparisons/accept#invite=${TOKEN}`);
    const owner = initializeInvitationFragmentOwner();
    expect(window.location.hash).toBe("");

    owner.observe(snapshot(candidate, state));
    owner.observe(snapshot(principal()));

    expect(owner.consume(7, SCOPE)).toBeNull();
    expect(document.body.textContent).not.toContain(TOKEN);
  });

  it("rejette et scrubbe tout fragment injecté après le bootstrap", () => {
    window.history.replaceState(null, "", "/comparisons/accept");
    const owner = initializeInvitationFragmentOwner();
    owner.observe(snapshot(principal()));

    window.history.pushState(null, "", `/comparisons/accept#invite=${TOKEN}`);

    expect(window.location.hash).toBe("");
    expect(owner.consume(7, SCOPE)).toBeNull();
    expect(JSON.stringify(window.history.state)).not.toContain(TOKEN);
  });
});
