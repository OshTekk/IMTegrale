import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, downloadFile, readCsrfCookie } from "./api";

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
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("%PDF-sample", {
      status: 200,
      headers: {
        "Content-Type": "application/pdf",
        "Content-Disposition": 'attachment; filename="releve-academique.pdf"'
      }
    })));

    const file = await downloadFile("/api/v1/academic-reports/personal.pdf");

    expect(file.filename).toBe("releve-academique.pdf");
    expect(await file.blob.text()).toBe("%PDF-sample");
  });

  it("preserves the API error instead of downloading an invalid document", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Aucune UE" }), {
      status: 409,
      headers: { "Content-Type": "application/json" }
    })));

    const error = await downloadFile("/api/v1/academic-reports/personal.pdf").catch((value: unknown) => value);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, message: "Aucune UE" });
  });
});
