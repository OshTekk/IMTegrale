// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { expectNoSeriousLearningViolations } from "../test/learningTestA11y";
import { LearningSourceViewer } from "./LearningSourceViewer";

const pdfjsMocks = vi.hoisted(() => ({
  destroy: vi.fn(),
  getDocument: vi.fn(),
}));

vi.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  TextLayer: class {},
  getDocument: pdfjsMocks.getDocument,
}));

vi.mock("pdfjs-dist/build/pdf.worker.min.mjs?url", () => ({
  default: "/assets/pdf.worker.min-fic12345.mjs",
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("LearningSourceViewer PDF security", () => {
  it("passes the fail-closed scripting option to PDF.js and destroys the loading task", async () => {
    pdfjsMocks.destroy.mockResolvedValue(undefined);
    pdfjsMocks.getDocument.mockReturnValue({
      destroy: pdfjsMocks.destroy,
      onProgress: null,
      promise: new Promise(() => undefined),
    });

    const { unmount } = render(
      <LearningSourceViewer
        assetId="asset-pdf-fictif"
        assetUrl="/api/v1/learning/assets/asset-pdf-fictif"
        downloadUrl={null}
        mimeType="application/pdf"
        title="[FICTIF] PDF synthétique"
        page={1}
      />,
    );

    await waitFor(() =>
      expect(pdfjsMocks.getDocument).toHaveBeenCalledWith({
        url: "/api/v1/learning/assets/asset-pdf-fictif",
        withCredentials: true,
        rangeChunkSize: 64 * 1024,
        disableRange: false,
        disableStream: false,
        disableAutoFetch: false,
        stopAtErrors: true,
        enableScripting: false,
      }),
    );

    unmount();
    await waitFor(() => expect(pdfjsMocks.destroy).toHaveBeenCalledOnce());
  });
});

describe("LearningSourceViewer protected images", () => {
  it("loads an image directly from the protected same-origin asset endpoint", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(
      <main aria-label="[FICTIF] Source de démonstration">
        <LearningSourceViewer
          assetId="asset.fictif-image"
          assetUrl="/api/v1/learning/assets/asset.fictif-image"
          downloadUrl="/api/v1/learning/assets/asset.fictif-image/download"
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

  it("keeps an inline document visible without presenting a forbidden download", async () => {
    const { container } = render(
      <main aria-label="[FICTIF] Source consultable">
        <LearningSourceViewer
          assetId="asset.fictif-inline"
          assetUrl="/api/v1/learning/assets/asset.fictif-inline"
          downloadUrl={null}
          mimeType="image/png"
          title="[FICTIF] Illustration consultable"
          page={null}
        />
      </main>,
    );

    expect(await screen.findByRole("img", { name: "[FICTIF] Illustration consultable" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: /Télécharger/ })).toBeNull();
    await expectNoSeriousLearningViolations(container);
  });

  it("rejects a traversal asset ID before any request", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(
      <main aria-label="[FICTIF] Source invalide">
        <LearningSourceViewer
          assetId="../FICTIF-outside"
          assetUrl="/api/v1/learning/assets/../FICTIF-outside"
          downloadUrl={null}
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
