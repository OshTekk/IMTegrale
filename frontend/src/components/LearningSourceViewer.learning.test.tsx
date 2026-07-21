// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { expectNoSeriousLearningViolations } from "../test/learningTestA11y";
import { LearningSourceViewer } from "./LearningSourceViewer";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("LearningSourceViewer protected images", () => {
  it("loads an image directly from the protected same-origin asset endpoint", async () => {
    const fetchMock = vi.fn();
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
    expect(image.getAttribute("src")).toBe("/api/v1/learning/assets/asset.fictif-image");
    expect(fetchMock).not.toHaveBeenCalled();
    const download = screen.getByRole("link", { name: /Télécharger/ });
    expect(download.getAttribute("href")).toBe("/api/v1/learning/assets/asset.fictif-image/download");
    expect(download.getAttribute("download")).toBeNull();
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
