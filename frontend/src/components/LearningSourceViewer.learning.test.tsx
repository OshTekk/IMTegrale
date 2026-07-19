// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { expectNoSeriousLearningViolations } from "../test/learningTestA11y";
import { LearningSourceViewer } from "./LearningSourceViewer";

const createObjectURL = vi.fn(() => "blob:https://imt.test/FICTIF-image");
const revokeObjectURL = vi.fn();

beforeEach(() => {
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: createObjectURL,
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    value: revokeObjectURL,
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

function successfulImageResponse(): Response {
  return {
    ok: true,
    status: 200,
    headers: {
      get: (name: string) =>
        name.toLowerCase() === "content-disposition" ? 'attachment; filename="FICTIF-illustration.png"' : null,
    },
    blob: async () => new Blob(["[FICTIF] image"], { type: "image/png" }),
  } as Response;
}

describe("LearningSourceViewer protected images", () => {
  it("loads an image only through the protected asset endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(successfulImageResponse());
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(
      <main aria-label="[FICTIF] Source de démonstration">
        <LearningSourceViewer
          assetId="asset.fictif-image"
          mimeType="image/png"
          title="[FICTIF] Illustration technique"
          page={null}
        />
      </main>,
    );

    const image = await screen.findByRole("img", { name: "[FICTIF] Illustration technique" });
    expect(image.getAttribute("src")).toBe("blob:https://imt.test/FICTIF-image");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/learning/assets/asset.fictif-image",
      expect.objectContaining({
        method: "GET",
        credentials: "same-origin",
      }),
    );
    const download = screen.getByRole("link", { name: /Télécharger/ });
    expect(download.getAttribute("href")).toBe("blob:https://imt.test/FICTIF-image");
    expect(download.getAttribute("download")).toBe("FICTIF-illustration.png");
    await expectNoSeriousLearningViolations(container);
  });

  it("rejects a traversal asset ID before any request", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(
      <main aria-label="[FICTIF] Source invalide">
        <LearningSourceViewer
          assetId="../FICTIF-outside"
          mimeType="image/png"
          title="[FICTIF] Illustration refusée"
          page={null}
        />
      </main>,
    );

    expect(screen.getByRole("heading", { name: "Document indisponible" })).toBeTruthy();
    await waitFor(() => expect(fetchMock).not.toHaveBeenCalled());
    await expectNoSeriousLearningViolations(container);
  });
});
