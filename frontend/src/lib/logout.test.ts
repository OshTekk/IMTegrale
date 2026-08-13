// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  isAllowedLogoutTransition,
  requestServerConfirmedLogout,
  verifyAuthoritativeSessionState,
  type LogoutPhase,
  type LogoutState,
} from "./logout";

const logoutStates = { requesting: 1, verifying: 2, confirmed: 3, failed: 4, indeterminate: 5 } as const;

const accountA = {
  id: "account-fictif-a",
  display_name: "[FICTIF] Compte A",
  imt_username: "compte.a.fictif",
};

const activeSessionA = {
  authenticated: true as const,
  role: "owner" as const,
  auth_method: "imt" as const,
  needs_security_setup: false,
  needs_sync_setup: false,
  account: accountA,
  learning: {
    available: false,
    audience_label: null,
    level_label: null,
    reverify_required: false,
    catalog_version: null,
  },
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "private, no-store" },
  });
}

function requestWith(fetchImpl: typeof fetch, setState: (state: LogoutState) => void = () => {}) {
  return requestServerConfirmedLogout(accountA.id, setState, fetchImpl);
}

beforeEach(() => {
  vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(true);
  document.cookie = "botnote_csrf=csrf-logout-fictif; path=/";
  document.cookie = "botnote_session=session-visible-fictif; path=/";
});

afterEach(() => {
  document.cookie = "botnote_csrf=; Max-Age=0; path=/";
  document.cookie = "botnote_session=; Max-Age=0; path=/";
  vi.restoreAllMocks();
});

describe("logout state machine", () => {
  it("allows exactly the declared transitions", () => {
    const allowed = new Set([
      "idle->requesting",
      "requesting->confirmed",
      "requesting->verifying",
      "verifying->confirmed",
      "verifying->failed",
      "verifying->indeterminate",
    ]);
    const phases: LogoutPhase[] = ["idle", "requesting", "verifying", "confirmed", "failed", "indeterminate"];

    for (const from of phases) {
      for (const to of phases) {
        expect(isAllowedLogoutTransition(from, to), `${from}->${to}`).toBe(allowed.has(`${from}->${to}`));
      }
    }
  });
});

describe("verifyAuthoritativeSessionState", () => {
  it("uses a direct no-store same-origin session read", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(activeSessionA));

    await expect(verifyAuthoritativeSessionState(fetchMock as typeof fetch)).resolves.toEqual(activeSessionA);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/auth/session",
      expect.objectContaining({
        credentials: "same-origin",
        cache: "no-store",
      }),
    );
  });

  it.each([
    ["HTTP 401", jsonResponse({ detail: "unauthorized" }, 401)],
    ["authenticated false", jsonResponse({ authenticated: false })],
  ])("accepts %s as authoritative anonymity", async (_label, response) => {
    const fetchMock = vi.fn().mockResolvedValue(response);
    await expect(verifyAuthoritativeSessionState(fetchMock as typeof fetch)).resolves.toEqual({ authenticated: false });
  });

  it.each([
    ["missing account", jsonResponse({ ...activeSessionA, account: undefined })],
    ["empty account id", jsonResponse({ ...activeSessionA, account: { ...accountA, id: "" } })],
    ["invalid role", jsonResponse({ ...activeSessionA, role: "administrator" })],
    ["missing setup flag", jsonResponse({ ...activeSessionA, needs_sync_setup: undefined })],
    ["missing learning contract", jsonResponse({ ...activeSessionA, learning: undefined })],
    ["malformed JSON", new Response("{", { status: 200, headers: { "Content-Type": "application/json" } })],
    ["HTTP 503", jsonResponse({ detail: "unavailable" }, 503)],
  ])("fails closed on %s", async (_label, response) => {
    const fetchMock = vi.fn().mockResolvedValue(response);
    await expect(verifyAuthoritativeSessionState(fetchMock as typeof fetch)).rejects.toThrow();
  });
});

describe("requestServerConfirmedLogout", () => {
  it("confirms only a successful HTTP response carrying ok true", async () => {
    const states: LogoutState[] = [];
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));

    await expect(requestWith(fetchMock as typeof fetch, (state) => states.push(state))).resolves.toEqual({
      authenticated: false,
    });
    expect(states).toEqual([logoutStates.requesting, logoutStates.confirmed]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/auth/logout",
      expect.objectContaining({
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: expect.objectContaining({
          "X-CSRF-Token": "csrf-logout-fictif",
        }),
      }),
    );
    expect(document.cookie).toContain("botnote_session=session-visible-fictif");
  });

  it.each([
    ["transport before commit", new TypeError("synthetic transport failure")],
    ["timeout transport", new DOMException("synthetic timeout", "AbortError")],
    ["HTTP 500", jsonResponse({ detail: "synthetic database failure" }, 500)],
    ["HTTP 401", jsonResponse({ detail: "synthetic expired session" }, 401)],
    ["HTTP 403", jsonResponse({ detail: "synthetic csrf rejection" }, 403)],
    ["HTTP 200 ok false", jsonResponse({ ok: false })],
    ["HTTP 200 unrelated payload", jsonResponse({ authenticated: false })],
    ["HTTP 200 malformed JSON", new Response("{", { status: 200 })],
    ["HTTP 204", new Response(null, { status: 204 })],
  ])("keeps the same principal authoritative after %s", async (_label, logoutResult) => {
    const states: LogoutState[] = [];
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() =>
        logoutResult instanceof Error || logoutResult instanceof DOMException
          ? Promise.reject(logoutResult)
          : Promise.resolve(logoutResult),
      )
      .mockResolvedValueOnce(jsonResponse(activeSessionA));

    await expect(requestWith(fetchMock as typeof fetch, (state) => states.push(state))).resolves.toBeUndefined();
    expect(states).toEqual([logoutStates.requesting, logoutStates.verifying, logoutStates.failed]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(document.cookie).toContain("botnote_session=session-visible-fictif");
  });

  it.each([
    ["verification 401", jsonResponse({ detail: "unauthorized" }, 401)],
    ["verification authenticated false", jsonResponse({ authenticated: false })],
  ])("recovers a success response lost after commit through %s", async (_label, sessionResponse) => {
    const states: LogoutState[] = [];
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("synthetic response lost after commit"))
      .mockResolvedValueOnce(sessionResponse);

    await expect(requestWith(fetchMock as typeof fetch, (state) => states.push(state))).resolves.toEqual({
      authenticated: false,
    });
    expect(states).toEqual([logoutStates.requesting, logoutStates.verifying, logoutStates.confirmed]);
  });

  it.each([
    ["verification transport error", new TypeError("synthetic verification unavailable")],
    ["verification malformed JSON", new Response("{", { status: 200 })],
    ["verification malformed session", jsonResponse({ authenticated: true })],
    ["verification HTTP 503", jsonResponse({ detail: "unavailable" }, 503)],
  ])("returns indeterminate on %s", async (_label, verificationResult) => {
    const states: LogoutState[] = [];
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("synthetic ambiguous logout"))
      .mockImplementationOnce(() =>
        verificationResult instanceof Error ? Promise.reject(verificationResult) : Promise.resolve(verificationResult),
      );

    await expect(requestWith(fetchMock as typeof fetch, (state) => states.push(state))).resolves.toBeUndefined();
    expect(states).toEqual([logoutStates.requesting, logoutStates.verifying, logoutStates.indeterminate]);
  });

  it("returns indeterminate without publishing success while offline", async () => {
    const states: LogoutState[] = [];
    const fetchMock = vi.fn();
    vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(false);

    await expect(
      requestServerConfirmedLogout(accountA.id, (state) => states.push(state), fetchMock as typeof fetch),
    ).resolves.toBeUndefined();
    expect(states).toEqual([logoutStates.requesting, logoutStates.verifying, logoutStates.indeterminate]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns a different principal without calling it anonymous or confirmed", async () => {
    const sessionB = {
      ...activeSessionA,
      account: { ...accountA, id: "account-fictif-b", display_name: "[FICTIF] Compte B" },
    };
    const states: LogoutState[] = [];
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("synthetic ambiguous logout"))
      .mockResolvedValueOnce(jsonResponse(sessionB));

    await expect(requestWith(fetchMock as typeof fetch, (state) => states.push(state))).resolves.toEqual(sessionB);
    expect(states).toEqual([logoutStates.requesting, logoutStates.verifying]);
  });
});
