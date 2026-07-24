import { CircleDashed, CloudCheck, CloudOff, LoaderCircle } from "lucide-react";

export type SimulationSaveState = "saved" | "dirty" | "saving" | "error" | "conflict";

export function SimulationSaveIndicator({ state, valid }: { state: SimulationSaveState; valid: boolean }) {
  if (!valid && state === "dirty") {
    return (
      <span className="simulation-save-state is-warning" role="status" aria-live="polite">
        <CircleDashed size={15} /> À compléter
      </span>
    );
  }
  if (state === "saving") {
    return (
      <span className="simulation-save-state" role="status" aria-live="polite">
        <LoaderCircle className="spin" size={15} /> Enregistrement…
      </span>
    );
  }
  if (state === "dirty") {
    return (
      <span className="simulation-save-state" role="status" aria-live="polite">
        <CircleDashed size={15} /> Modifications locales
      </span>
    );
  }
  if (state === "conflict") {
    return (
      <span className="simulation-save-state is-error" role="status" aria-live="assertive">
        <CloudOff size={15} /> Action requise
      </span>
    );
  }
  if (state === "error") {
    return (
      <span className="simulation-save-state is-error" role="status" aria-live="assertive">
        <CloudOff size={15} /> Échec, réessayer
      </span>
    );
  }
  return (
    <span className="simulation-save-state is-saved" role="status" aria-live="polite">
      <CloudCheck size={15} /> Enregistré
    </span>
  );
}
