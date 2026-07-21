import {
  ChevronLeft,
  ChevronRight,
  Download,
  FileWarning,
  Link2,
  Maximize2,
  Search,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import type { PDFDocumentProxy, PDFPageProxy } from "pdfjs-dist";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { learningAssetUrl, learningErrorCopy, learningErrorState, safeLearningId } from "../lib/learning";

interface LearningSourceViewerProps {
  assetId: string;
  mimeType: string | null | undefined;
  title: string;
  page: number | null;
  onPageChange?: (page: number) => void;
}

type PdfJsModule = typeof import("pdfjs-dist");

function clamp(value: number, minimum: number, maximum: number) {
  return Math.min(maximum, Math.max(minimum, value));
}

function isTextItem(value: unknown): value is { str: string } {
  return typeof value === "object" && value !== null && "str" in value && typeof value.str === "string";
}

export function LearningSourceViewer({ assetId, mimeType, title, page, onPageChange }: LearningSourceViewerProps) {
  const safeAssetId = safeLearningId(assetId);
  const assetUrl = safeAssetId ? learningAssetUrl(safeAssetId) : null;
  const downloadUrl = assetUrl ? `${assetUrl}/download` : null;
  const isPdf = mimeType === "application/pdf";
  const [pdfDocument, setPdfDocument] = useState<PDFDocumentProxy | null>(null);
  const [currentPage, setCurrentPage] = useState(page ?? 1);
  const [pageInput, setPageInput] = useState(String(page ?? 1));
  const [scale, setScale] = useState(1);
  const [fitWidth, setFitWidth] = useState(true);
  const [containerWidth, setContainerWidth] = useState(0);
  const [loadProgress, setLoadProgress] = useState(0);
  const [rendering, setRendering] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [searchInput, setSearchInput] = useState("");
  const [searchMatches, setSearchMatches] = useState<number[]>([]);
  const [searching, setSearching] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const viewportRef = useRef<HTMLDivElement>(null);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textLayerRef = useRef<HTMLDivElement>(null);
  const pdfModuleRef = useRef<PdfJsModule | null>(null);
  const pageTextCache = useRef(new Map<number, string>());

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport || typeof ResizeObserver === "undefined") return;
    const update = () => setContainerWidth(Math.max(0, viewport.clientWidth - 32));
    update();
    const observer = new ResizeObserver(update);
    observer.observe(viewport);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!assetUrl || !isPdf) return;
    let cancelled = false;
    let loadingTask: ReturnType<PdfJsModule["getDocument"]> | null = null;
    setPdfDocument(null);
    setError(null);
    setLoadProgress(0);
    pageTextCache.current.clear();
    void import("pdfjs-dist")
      .then(async (pdfjs) => {
        if (cancelled) return;
        pdfModuleRef.current = pdfjs;
        pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
        loadingTask = pdfjs.getDocument({
          url: assetUrl,
          withCredentials: true,
          rangeChunkSize: 64 * 1024,
          disableRange: false,
          disableStream: false,
          disableAutoFetch: false,
          stopAtErrors: true,
        });
        loadingTask.onProgress = ({ loaded, total }: { loaded: number; total: number }) => {
          if (!cancelled && total > 0) setLoadProgress(clamp(Math.round((loaded / total) * 100), 0, 100));
        };
        const loadedDocument = await loadingTask.promise;
        if (cancelled) {
          await loadingTask.destroy();
          return;
        }
        setPdfDocument(loadedDocument);
        setCurrentPage((current) => clamp(current, 1, loadedDocument.numPages));
        setLoadProgress(100);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason);
      });
    return () => {
      cancelled = true;
      void loadingTask?.destroy();
      setPdfDocument(null);
      pdfModuleRef.current = null;
    };
  }, [assetUrl, isPdf]);

  useEffect(() => {
    if (!pdfDocument || !page) return;
    setCurrentPage(clamp(page, 1, pdfDocument.numPages));
  }, [page, pdfDocument]);

  useEffect(() => {
    if (!pdfDocument) return;
    onPageChange?.(currentPage);
    setPageInput(String(currentPage));
  }, [currentPage, onPageChange, pdfDocument]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const textLayerContainer = textLayerRef.current;
    const surface = surfaceRef.current;
    const pdfjs = pdfModuleRef.current;
    if (!pdfDocument || !pdfjs || !canvas || !textLayerContainer || !surface) return;
    let cancelled = false;
    let renderTask: ReturnType<PDFPageProxy["render"]> | null = null;
    let textLayer: InstanceType<PdfJsModule["TextLayer"]> | null = null;
    setRendering(true);
    setError(null);
    void pdfDocument
      .getPage(currentPage)
      .then(async (pdfPage) => {
        if (cancelled) return;
        const baseViewport = pdfPage.getViewport({ scale: 1 });
        const fittedScale = containerWidth > 0 ? clamp(containerWidth / baseViewport.width, 0.4, 3) : 1;
        const visualScale = fitWidth ? fittedScale : scale;
        const viewport = pdfPage.getViewport({ scale: visualScale });
        const pixelRatio = clamp(window.devicePixelRatio || 1, 1, 2);
        const renderViewport = pdfPage.getViewport({ scale: visualScale * pixelRatio });
        surface.style.width = `${Math.ceil(viewport.width)}px`;
        surface.style.height = `${Math.ceil(viewport.height)}px`;
        surface.style.setProperty("--scale-factor", String(visualScale));
        surface.style.setProperty("--total-scale-factor", String(visualScale));
        canvas.width = Math.ceil(renderViewport.width);
        canvas.height = Math.ceil(renderViewport.height);
        canvas.style.width = `${Math.ceil(viewport.width)}px`;
        canvas.style.height = `${Math.ceil(viewport.height)}px`;
        textLayerContainer.replaceChildren();
        renderTask = pdfPage.render({ canvas, viewport: renderViewport });
        const textContent = await pdfPage.getTextContent({ includeMarkedContent: true });
        if (cancelled) return;
        textLayer = new pdfjs.TextLayer({
          textContentSource: textContent,
          container: textLayerContainer,
          viewport,
        });
        await Promise.all([renderTask.promise, textLayer.render()]);
        if (!cancelled) setRendering(false);
      })
      .catch((reason: unknown) => {
        if (!cancelled && !(reason instanceof Error && reason.name === "RenderingCancelledException")) {
          setRendering(false);
          setError(reason);
        }
      });
    return () => {
      cancelled = true;
      renderTask?.cancel();
      textLayer?.cancel();
    };
  }, [containerWidth, currentPage, fitWidth, pdfDocument, scale]);

  const goToPage = useCallback(
    (nextPage: number) => {
      if (!pdfDocument) return;
      setCurrentPage(clamp(Math.round(nextPage), 1, pdfDocument.numPages));
    },
    [pdfDocument],
  );

  const changeZoom = useCallback((delta: number) => {
    setFitWidth(false);
    setScale((current) => clamp(Math.round((current + delta) * 100) / 100, 0.5, 3));
  }, []);

  const handleKeyboard = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.target instanceof HTMLInputElement || event.target instanceof HTMLButtonElement) return;
    if (event.key === "ArrowRight" || event.key === "PageDown") goToPage(currentPage + 1);
    else if (event.key === "ArrowLeft" || event.key === "PageUp") goToPage(currentPage - 1);
    else if (event.key === "+" || event.key === "=") changeZoom(0.15);
    else if (event.key === "-") changeZoom(-0.15);
    else if (event.key === "0") setFitWidth(true);
    else return;
    event.preventDefault();
  };

  const searchDocument = async (event: FormEvent) => {
    event.preventDefault();
    const query = searchInput.trim().toLocaleLowerCase("fr");
    if (!pdfDocument || query.length < 2) return;
    setSearching(true);
    setSearchMatches([]);
    try {
      const matches: number[] = [];
      for (let pageNumber = 1; pageNumber <= pdfDocument.numPages; pageNumber += 1) {
        let text = pageTextCache.current.get(pageNumber);
        if (text === undefined) {
          const pdfPage = await pdfDocument.getPage(pageNumber);
          const content = await pdfPage.getTextContent();
          text = content.items
            .flatMap((item) => (isTextItem(item) ? [item.str] : []))
            .join(" ")
            .toLocaleLowerCase("fr");
          pageTextCache.current.set(pageNumber, text);
        }
        if (text.includes(query)) matches.push(pageNumber);
      }
      setSearchMatches(matches);
      if (matches.length) goToPage(matches.find((match) => match >= currentPage) ?? matches[0]!);
      setAnnouncement(
        matches.length
          ? `${matches.length} page${matches.length === 1 ? "" : "s"} trouvée${matches.length === 1 ? "" : "s"}.`
          : "Aucun résultat dans ce document.",
      );
    } catch (reason) {
      setError(reason);
    } finally {
      setSearching(false);
    }
  };

  const copyExactLink = async () => {
    const exact = new URL(window.location.href);
    exact.searchParams.set("page", String(currentPage));
    try {
      await navigator.clipboard.writeText(exact.toString());
      setAnnouncement(`Lien copié vers la page ${currentPage}.`);
    } catch {
      setAnnouncement("Le lien n'a pas pu être copié.");
    }
  };

  if (!safeAssetId || !assetUrl || !downloadUrl) {
    return (
      <div className="learning-state learning-state-error">
        <FileWarning aria-hidden="true" />
        <h2>Document indisponible</h2>
        <p>La référence du document est invalide.</p>
      </div>
    );
  }

  if (mimeType?.startsWith("image/")) {
    return (
      <div className="learning-source-viewer">
        <div className="learning-source-actions">
          <span>Illustration</span>
          <a className="secondary-button" href={downloadUrl}>
            <Download size={16} /> Télécharger
          </a>
        </div>
        <img className="learning-source-image" src={assetUrl} alt={title} />
      </div>
    );
  }

  if (!isPdf) {
    return (
      <div className="learning-state learning-state-empty">
        <p>Ce format doit être téléchargé pour être consulté.</p>
        <a className="secondary-button" href={downloadUrl}>
          <Download size={16} /> Télécharger
        </a>
      </div>
    );
  }

  if (error) {
    const copy = learningErrorCopy[learningErrorState(error)];
    return (
      <div className="learning-state learning-state-error" role="alert">
        <FileWarning aria-hidden="true" />
        <h2>{copy.title}</h2>
        <p>{copy.message}</p>
        <a className="secondary-button" href={downloadUrl}>
          <Download size={16} /> Télécharger le document
        </a>
      </div>
    );
  }

  if (!pdfDocument) {
    return (
      <div className="learning-source-loading" role="status" aria-live="polite" aria-busy="true">
        <span className="spinner" />
        <span>Ouverture du document{loadProgress > 0 ? ` · ${loadProgress} %` : ""}</span>
        <progress max="100" value={loadProgress || undefined} aria-label="Chargement du document" />
      </div>
    );
  }

  return (
    <section
      className="learning-source-viewer learning-pdf-viewer"
      aria-label={`Lecteur PDF : ${title}`}
      tabIndex={0}
      onKeyDown={handleKeyboard}
    >
      <p className="sr-only" role="status" aria-live="polite">
        {announcement}
      </p>
      <div className="learning-pdf-toolbar" role="toolbar" aria-label="Commandes du document">
        <div className="learning-pdf-toolbar-group">
          <button
            type="button"
            onClick={() => goToPage(currentPage - 1)}
            disabled={currentPage <= 1}
            aria-label="Page précédente"
          >
            <ChevronLeft aria-hidden="true" />
          </button>
          <form
            className="learning-pdf-page-control"
            onSubmit={(event) => {
              event.preventDefault();
              const nextPage = Number(pageInput);
              if (Number.isFinite(nextPage)) goToPage(nextPage);
              else setPageInput(String(currentPage));
            }}
          >
            <label htmlFor="learning-pdf-page">Page</label>
            <input
              id="learning-pdf-page"
              name="page"
              type="number"
              min="1"
              max={pdfDocument.numPages}
              value={pageInput}
              onChange={(event) => setPageInput(event.target.value)}
              onBlur={() => setPageInput(String(currentPage))}
            />
            <span>sur {pdfDocument.numPages}</span>
          </form>
          <button
            type="button"
            onClick={() => goToPage(currentPage + 1)}
            disabled={currentPage >= pdfDocument.numPages}
            aria-label="Page suivante"
          >
            <ChevronRight aria-hidden="true" />
          </button>
        </div>
        <div className="learning-pdf-toolbar-group">
          <button type="button" onClick={() => changeZoom(-0.15)} aria-label="Réduire le zoom">
            <ZoomOut aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={() => setFitWidth(true)}
            aria-label="Adapter à la largeur"
            aria-pressed={fitWidth}
          >
            <Maximize2 aria-hidden="true" />
          </button>
          <button type="button" onClick={() => changeZoom(0.15)} aria-label="Augmenter le zoom">
            <ZoomIn aria-hidden="true" />
          </button>
          <button type="button" onClick={() => void copyExactLink()} aria-label="Copier le lien de cette page">
            <Link2 aria-hidden="true" />
          </button>
          <a href={downloadUrl} aria-label="Télécharger le document" title="Télécharger">
            <Download aria-hidden="true" />
          </a>
        </div>
      </div>
      <form className="learning-pdf-search" role="search" onSubmit={(event) => void searchDocument(event)}>
        <Search aria-hidden="true" />
        <label className="sr-only" htmlFor="learning-pdf-search">
          Rechercher dans le document
        </label>
        <input
          id="learning-pdf-search"
          type="search"
          value={searchInput}
          minLength={2}
          onChange={(event) => setSearchInput(event.target.value)}
          placeholder="Rechercher dans le document"
        />
        <button type="submit" disabled={searchInput.trim().length < 2 || searching}>
          {searching ? "Recherche…" : "Rechercher"}
        </button>
        {searchMatches.length > 0 && (
          <span>
            {searchMatches.length} page{searchMatches.length === 1 ? "" : "s"}
          </span>
        )}
      </form>
      <div className="learning-pdf-viewport" ref={viewportRef} aria-busy={rendering}>
        <div className="learning-pdf-surface" ref={surfaceRef}>
          <canvas ref={canvasRef} aria-label={`${title}, page ${currentPage}`} />
          <div className="textLayer" ref={textLayerRef} />
          {rendering && <span className="learning-pdf-rendering spinner" aria-label="Rendu de la page" />}
        </div>
      </div>
    </section>
  );
}

export default LearningSourceViewer;
