import { afterEach, describe, expect, it, vi } from "vitest";
import { authCompleteSecuritySetup, authSessionStatus } from "../generated/api/sdk.gen";
import { ApiError } from "./api";
import { apiData, throwOnApiError } from "./generatedApi";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("generated API client", () => {
  it("uses same-origin credentials and returns typed response data", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          authenticated: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const session = await apiData(authSessionStatus({ throwOnError: throwOnApiError }));

    expect(session).toEqual({ authenticated: false });
    const request = fetchMock.mock.calls[0]?.[0];
    expect(request).toBeInstanceOf(Request);
    expect((request as Request).credentials).toBe("same-origin");
  });

  it("normalizes stable server errors without leaking extra fields", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: {
              code: "PRIMARY_AUTH_REQUIRED",
              message: "Authentification primaire requise",
              private_debug: "secret",
            },
          }),
          { status: 403, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    const error = await apiData(authCompleteSecuritySetup({ throwOnError: throwOnApiError })).catch(
      (value: unknown) => value,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 403,
      code: "PRIMARY_AUTH_REQUIRED",
      message: "Authentification primaire requise",
    });
  });
});
