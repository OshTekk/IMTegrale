// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { isCurrentLogoutAttempt, requestServerConfirmedLogout, verifyAuthoritativeSessionState } from "./logout";

const accountA = {
  id: "account-fictif-a",
  display_name: "[FICTIF] Compte A",
  imt_username: "compte.a.fictif",
};

const activeSessionA = {
  authenticated: true,
  role: "owner",
  auth_method: "imt",
  account: accountA,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "private, no-store" },
  });
}

beforeEach(() => {
  document.cookie = "botnote_csrf=csrf-logout-fictif; path=/";
});

afterEach(() => {
  document.cookie = "botnote_csrf=; Max-Age=0; path=/";
  vi.restoreAllMocks();
});

describe("verifyAuthoritativeSessionState", () => {
  it("bypasses browser and TanStack caches for a real session read", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(activeSessionA));

    await expect(verifyAuthoritativeSessionState(fetchMock as typeof fetch)).resolves.toEqual({
      kind: "authenticated",
      session: activeSessionA,
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/auth/session",
      expect.objectContaining({
        credentials: "same-origin",
        cache: "no-store",
      }),
    );
  });

  it("treats the session endpoint 401 as authoritative absence", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ detail: "unauthorized" }, 401));

    await expect(verifyAuthoritativeSessionState(fetchMock as typeof fetch)).resolves.toEqual({ kind: "anonymous" });
  });

  it("fails closed on malformed, cached-looking, or unavailable verification responses", async () => {
    const invalidPayload = vi.fn().mockResolvedValue(jsonResponse({ authenticated: true }));
    const unavailable = vi.fn().mockResolvedValue(jsonResponse({ detail: "unavailable" }, 503));

    await expect(verifyAuthoritativeSessionState(invalidPayload as typeof fetch)).rejects.toThrow();
    await expect(verifyAuthoritativeSessionState(unavailable as typeof fetch)).rejects.toThrow();
  });
});

describe("requestServerConfirmedLogout", () => {
  it("confirms only a valid successful logout response", async () => {
    const phases: string[] = [];
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));

    await expect(
      requestServerConfirmedLogout({
        expectedAccountId: accountA.id,
        fetchImpl: fetchMock as typeof fetch,
        onPhase: (phase) => phases.push(phase),
      }),
    ).resolves.toEqual({ kind: "confirmed" });
    expect(phases).toEqual(["requesting", "confirmed"]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/auth/logout",
      expect.objectContaining({
        method: "POST",
        credentials: "same-origin",
        headers: expect.objectContaining({ "X-CSRF-Token": "csrf-logout-fictif" }),
      }),
    );
  });

  it.each([
    ["transport rejection", new TypeError("synthetic failure before server")],
    ["HTTP 500", jsonResponse({ detail: "synthetic database failure" }, 500)],
    ["HTTP 401", jsonResponse({ detail: "synthetic expired session" }, 401)],
    ["HTTP 403", jsonResponse({ detail: "synthetic csrf rejection" }, 403)],
    ["malformed HTTP 200", jsonResponse({ ok: false }, 200)],
  ])("keeps the same principal active after %s", async (_label, logoutResult) => {
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() =>
        logoutResult instanceof Error ? Promise.reject(logoutResult) : Promise.resolve(logoutResult),
      )
      .mockResolvedValueOnce(jsonResponse(activeSessionA));

    await expect(
      requestServerConfirmedLogout({ expectedAccountId: accountA.id, fetchImpl: fetchMock as typeof fetch }),
    ).resolves.toMatchObject({ kind: "failed", session: activeSessionA });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([
    ["HTTP 401", jsonResponse({ detail: "unauthorized" }, 401)],
    ["anonymous JSON", jsonResponse({ authenticated: false })],
  ])("recognizes a response lost after commit through %s verification", async (_label, sessionResponse) => {
    const phases: string[] = [];
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("synthetic response lost after commit"))
      .mockResolvedValueOnce(sessionResponse);

    await expect(
      requestServerConfirmedLogout({
        expectedAccountId: accountA.id,
        fetchImpl: fetchMock as typeof fetch,
        onPhase: (phase) => phases.push(phase),
      }),
    ).resolves.toEqual({ kind: "confirmed" });
    expect(phases).toEqual(["requesting", "verifying", "confirmed"]);
  });

  it("returns indeterminate when both logout and verification are offline", async () => {
    const phases: string[] = [];
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("synthetic offline logout"))
      .mockRejectedValueOnce(new TypeError("synthetic offline verification"));

    await expect(
      requestServerConfirmedLogout({
        expectedAccountId: accountA.id,
        fetchImpl: fetchMock as typeof fetch,
        onPhase: (phase) => phases.push(phase),
      }),
    ).resolves.toEqual({ kind: "indeterminate" });
    expect(phases).toEqual(["requesting", "verifying", "indeterminate"]);
  });

  it("distinguishes a concurrent principal change from an anonymous logout", async () => {
    const sessionB = {
      ...activeSessionA,
      account: { ...accountA, id: "account-fictif-b", display_name: "[FICTIF] Compte B" },
    };
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("synthetic ambiguous response"))
      .mockResolvedValueOnce(jsonResponse(sessionB));

    await expect(
      requestServerConfirmedLogout({ expectedAccountId: accountA.id, fetchImpl: fetchMock as typeof fetch }),
    ).resolves.toEqual({ kind: "principal-changed", session: sessionB });
  });
});

describe("logout attempt ordering", () => {
  it("ignores a late transition from an older attempt", () => {
    expect(isCurrentLogoutAttempt(1, 2)).toBe(false);
    expect(isCurrentLogoutAttempt(2, 2)).toBe(true);
  });
});
