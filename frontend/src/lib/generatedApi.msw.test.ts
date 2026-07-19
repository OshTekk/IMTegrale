import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { authSessionStatus } from "../generated/api/sdk.gen";
import { apiMockServer } from "../test/server";
import { ApiError } from "./api";
import { apiData, throwOnApiError } from "./generatedApi";

describe("generated API client with MSW", () => {
  it("decodes a typed synthetic response through the real fetch boundary", async () => {
    apiMockServer.use(
      http.get("http://localhost/api/v1/auth/session", () => HttpResponse.json({ authenticated: false })),
    );

    await expect(apiData(authSessionStatus({ throwOnError: throwOnApiError }))).resolves.toEqual({
      authenticated: false,
    });
  });

  it("keeps the stable error contract across the mocked HTTP boundary", async () => {
    apiMockServer.use(
      http.get("http://localhost/api/v1/auth/session", () =>
        HttpResponse.json(
          { detail: { code: "AUTHENTICATION_REQUIRED", message: "Session fictive absente." } },
          { status: 401 },
        ),
      ),
    );

    const error = await apiData(authSessionStatus({ throwOnError: throwOnApiError })).catch(
      (caught: unknown) => caught,
    );
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      code: "AUTHENTICATION_REQUIRED",
      message: "Session fictive absente.",
      status: 401,
    });
  });
});
