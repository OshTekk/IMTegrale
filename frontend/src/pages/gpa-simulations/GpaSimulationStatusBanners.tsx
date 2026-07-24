import { CloudOff, Copy, History, LoaderCircle, RefreshCw } from "lucide-react";
import type { SimulationSaveState } from "../../components/simulations/SimulationSaveIndicator";

export function GpaSimulationStatusBanners({
  rebaseAvailable,
  saveState,
  actionPending,
  preservePending,
  onRebase,
  onReload,
  onPreserve,
  onRetry,
}: {
  rebaseAvailable: boolean;
  saveState: SimulationSaveState;
  actionPending: boolean;
  preservePending: boolean;
  onRebase: () => void;
  onReload: () => void;
  onPreserve: () => void;
  onRetry: () => void;
}) {
  return (
    <>
      {rebaseAvailable && (
        <section className="simulation-rebase-banner">
          <History size={20} />
          <div>
            <strong>Tes données académiques ont évolué</strong>
            <p>Actualise la base. Tes hypothèses sont conservées et les divergences restent explicites.</p>
          </div>
          <button
            className="secondary-button"
            type="button"
            onClick={onRebase}
            disabled={saveState !== "saved" || actionPending}
          >
            {actionPending ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}
            Actualiser la base
          </button>
        </section>
      )}
      {saveState === "conflict" && (
        <section className="simulation-version-banner">
          <CloudOff size={20} />
          <div>
            <strong>Une version plus récente existe</strong>
            <p>Tes changements locaux restent affichés sans écraser l’autre onglet.</p>
          </div>
          <div>
            <button className="secondary-button" type="button" onClick={onReload}>
              Recharger
            </button>
            <button className="primary-button" type="button" onClick={onPreserve} disabled={preservePending}>
              {preservePending ? <LoaderCircle className="spin" size={16} /> : <Copy size={16} />}
              Conserver en copie
            </button>
          </div>
        </section>
      )}
      {saveState === "error" && (
        <section className="simulation-save-error-banner">
          <CloudOff size={20} />
          <div>
            <strong>L’enregistrement n’a pas abouti</strong>
            <p>Tes modifications sont locales et restent présentes dans cette page.</p>
          </div>
          <button className="secondary-button" type="button" onClick={onRetry}>
            <RefreshCw size={16} /> Réessayer
          </button>
        </section>
      )}
    </>
  );
}
