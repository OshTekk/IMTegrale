import { CircleDashed, CloudCheck, CloudOff, LoaderCircle } from "lucide-react";

export type SimulationSaveState = "saved" | "dirty" | "saving" | "error" | "conflict";

export function SimulationSaveIndicator({ state, valid }: { state: SimulationSaveState; valid: boolean }) {
  if (!valid && state === "dirty") {
    return (
      <span className="simulation-save-state is-warning">
        <CircleDashed size={15} /> À compléter
      </span>
    );
  }
  if (state === "saving") {
    return (
      <span className="simulation-save-state">
        <LoaderCircle className="spin" size={15} /> Enregistrement…
      </span>
    );
  }
  if (state === "dirty") {
    return (
      <span className="simulation-save-state">
        <CircleDashed size={15} /> Modifications en attente
      </span>
    );
  }
  if (state === "error" || state === "conflict") {
    return (
      <span className="simulation-save-state is-error">
        <CloudOff size={15} /> Non enregistré
      </span>
    );
  }
  return (
    <span className="simulation-save-state is-saved">
      <CloudCheck size={15} /> Enregistré
    </span>
  );
}
