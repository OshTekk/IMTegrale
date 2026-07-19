import { Download, FileWarning } from "lucide-react";
import { useEffect, useState } from "react";
import { fetchLearningAsset } from "../lib/api";
import { learningErrorCopy, learningErrorState, safeLearningId } from "../lib/learning";

interface LearningSourceViewerProps {
  assetId: string;
  mimeType: string | null | undefined;
  title: string;
  page: number | null;
}

export function LearningSourceViewer({ assetId, mimeType, title, page }: LearningSourceViewerProps) {
  const [file, setFile] = useState<{ url: string; filename: string; type: string } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const safeAssetId = safeLearningId(assetId);

  useEffect(() => {
    if (!safeAssetId) return;
    const controller = new AbortController();
    let objectUrl: string | null = null;
    setFile(null);
    setError(null);
    void fetchLearningAsset(safeAssetId, "application/pdf,image/*", controller.signal)
      .then(({ blob, filename }) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setFile({ url: objectUrl, filename, type: blob.type || mimeType || "application/octet-stream" });
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason);
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [mimeType, safeAssetId]);

  if (!safeAssetId) {
    return (
      <div className="learning-state learning-state-error">
        <FileWarning aria-hidden="true" />
        <h2>Document indisponible</h2>
        <p>La référence du document est invalide.</p>
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
      </div>
    );
  }
  if (!file) {
    return (
      <div
        className="learning-source-loading"
        role="status"
        aria-live="polite"
        aria-busy="true"
        aria-label="Chargement sécurisé du document"
      >
        <span className="spinner" />
      </div>
    );
  }

  const normalizedPage = page && Number.isInteger(page) && page > 0 ? page : null;
  const documentUrl =
    file.type === "application/pdf" && normalizedPage ? `${file.url}#page=${normalizedPage}` : file.url;
  return (
    <div className="learning-source-viewer">
      <div className="learning-source-actions">
        {normalizedPage && <span>Page demandée : {normalizedPage}</span>}
        <a className="secondary-button" href={file.url} download={file.filename}>
          <Download size={16} /> Télécharger
        </a>
      </div>
      {file.type === "application/pdf" ? (
        <object
          className="learning-pdf-object"
          data={documentUrl}
          type="application/pdf"
          aria-label={`${title}${normalizedPage ? `, page ${normalizedPage}` : ""}`}
        >
          <p>
            Le lecteur PDF intégré n'est pas disponible.{" "}
            <a href={file.url} download={file.filename}>
              Télécharger le document
            </a>
            .
          </p>
        </object>
      ) : file.type.startsWith("image/") ? (
        <img className="learning-source-image" src={file.url} alt={title} />
      ) : (
        <div className="learning-state learning-state-empty">
          <p>Ce format doit être téléchargé pour être consulté.</p>
        </div>
      )}
    </div>
  );
}

export default LearningSourceViewer;
