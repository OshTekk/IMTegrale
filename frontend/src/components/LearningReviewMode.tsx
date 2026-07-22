import { EllipsisVertical, Eye, EyeOff } from "lucide-react";
import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { isPrimaryOwnerSession } from "../lib/auth";
import type { Session } from "../types";

interface LearningReviewContextValue {
  canReview: boolean;
  enabled: boolean;
  setEnabled: (enabled: boolean) => void;
}

const LearningReviewContext = createContext<LearningReviewContextValue>({
  canReview: false,
  enabled: false,
  setEnabled: () => undefined,
});

export function LearningReviewProvider({ session, children }: { session: Session; children: ReactNode }) {
  const canReview = session.authenticated === true && isPrimaryOwnerSession(session);
  const [requested, setRequested] = useState(false);
  const value = useMemo(
    () => ({ canReview, enabled: canReview && requested, setEnabled: setRequested }),
    [canReview, requested],
  );
  return <LearningReviewContext.Provider value={value}>{children}</LearningReviewContext.Provider>;
}

export function useLearningReviewMode() {
  return useContext(LearningReviewContext);
}

export function LearningReviewMenu() {
  const { canReview, enabled, setEnabled } = useLearningReviewMode();
  if (!canReview) return null;
  return (
    <details className="learning-review-menu">
      <summary aria-label="Options Parcours" title="Options Parcours">
        <EllipsisVertical size={18} aria-hidden="true" />
      </summary>
      <div>
        <button type="button" aria-pressed={enabled} onClick={() => setEnabled(!enabled)}>
          {enabled ? <EyeOff size={17} aria-hidden="true" /> : <Eye size={17} aria-hidden="true" />}
          {enabled ? "Masquer les informations de vérification" : "Informations de vérification"}
        </button>
      </div>
    </details>
  );
}

export function LearningReviewPanel({
  title = "Métadonnées de revue",
  rows,
}: {
  title?: string;
  rows: Array<{ label: string; value: string | number | null | undefined }>;
}) {
  const { enabled } = useLearningReviewMode();
  if (!enabled) return null;
  const visibleRows = rows.filter((row) => row.value !== null && row.value !== undefined && row.value !== "");
  if (!visibleRows.length) return null;
  return (
    <aside className="learning-review-panel" aria-label={title}>
      <strong>{title}</strong>
      <dl>
        {visibleRows.map((row) => (
          <div key={row.label}>
            <dt>{row.label}</dt>
            <dd>{row.value}</dd>
          </div>
        ))}
      </dl>
    </aside>
  );
}
