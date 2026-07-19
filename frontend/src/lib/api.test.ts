import { afterEach, describe, expect, it, vi } from "vitest";
import { learningLearningCatalog } from "../generated/api/sdk.gen";
import { ApiError, apiErrorDetails, downloadFile, fetchLearningAsset, readCsrfCookie } from "./api";
import { apiData, throwOnApiError } from "./generatedApi";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("readCsrfCookie", () => {
  it("matches only the exact host cookie and ignores suffix collisions", () => {
    const cookies = "evilbotnote_csrf=attacker; __Host-botnote_csrf=valid%2Dtoken";

    expect(readCsrfCookie(cookies)).toBe("valid-token");
  });

  it("fails closed instead of throwing on malformed encoding", () => {
    expect(readCsrfCookie("__Host-botnote_csrf=%")).toBeNull();
  });
});

describe("downloadFile", () => {
  it("returns the PDF blob and its server-provided filename", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("%PDF-sample", {
          status: 200,
          headers: {
            "Content-Type": "application/pdf",
            "Content-Disposition": 'attachment; filename="releve-academique.pdf"',
          },
        }),
      ),
    );

    const file = await downloadFile("/api/v1/academic-reports/personal.pdf");

    expect(file.filename).toBe("releve-academique.pdf");
    expect(await file.blob.text()).toBe("%PDF-sample");
  });

  it("preserves the API error instead of downloading an invalid document", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Aucune UE" }), {
          status: 409,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    const error = await downloadFile("/api/v1/academic-reports/personal.pdf").catch((value: unknown) => value);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, message: "Aucune UE" });
  });
});

describe("structured learning errors", () => {
  it("preserves stable API codes while allowing the UI to ignore private detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: { code: "LEARNING_CATALOG_UNAVAILABLE", message: "Catalogue indisponible" },
          }),
          { status: 503, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    const error = await apiData(learningLearningCatalog({ throwOnError: throwOnApiError })).catch(
      (value: unknown) => value,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 503, code: "LEARNING_CATALOG_UNAVAILABLE" });
  });

  it("normalizes reverification details without accepting arbitrary fields", () => {
    expect(
      apiErrorDetails(
        {
          detail: {
            code: "STUDENT_REVERIFICATION_REQUIRED",
            message: "Vérification requise",
            system_path: "/srv/private/catalog.pdf",
          },
        },
        403,
      ),
    ).toEqual({
      code: "STUDENT_REVERIFICATION_REQUIRED",
      message: "Vérification requise",
      retryAfterSeconds: undefined,
      availableAt: undefined,
    });
  });
});

describe("fetchLearningAsset", () => {
  it("builds the protected route from an encoded ID and trusts only the server filename", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("%PDF", {
        status: 200,
        headers: {
          "Content-Type": "application/pdf",
          "Content-Disposition": 'inline; filename="document-fictif.pdf"',
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const file = await fetchLearningAsset("source:asset");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/learning/assets/source%3Aasset",
      expect.objectContaining({ credentials: "same-origin" }),
    );
    expect(file.filename).toBe("document-fictif.pdf");
  });

  it("keeps structured denial codes instead of returning a private blob", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: { code: "STUDENT_REVERIFICATION_REQUIRED", message: "Vérification requise" },
          }),
          { status: 403, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    const error = await fetchLearningAsset("asset.one").catch((value: unknown) => value);

    expect(error).toMatchObject({ status: 403, code: "STUDENT_REVERIFICATION_REQUIRED" });
  });
});
