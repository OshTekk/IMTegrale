import { TriangleAlert } from "lucide-react";

export type NoteSimulationResolution = "source" | "simulation";

export function NoteSimulationConflictPanel({
  scope,
  label,
  disabled,
  onResolve,
}: {
  scope: "UE" | "évaluation";
  label: string;
  disabled: boolean;
  onResolve: (resolution: NoteSimulationResolution) => void;
}) {
  return (
    <div className="note-workbench-conflict" role="alert">
      <TriangleAlert size={19} />
      <div>
        <strong>Conflit sur {scope === "UE" ? "l’UE" : "l’évaluation"}</strong>
        <p>{label}</p>
      </div>
      <div>
        <button className="secondary-button" type="button" onClick={() => onResolve("simulation")} disabled={disabled}>
          Garder la simulation
        </button>
        <button className="primary-button" type="button" onClick={() => onResolve("source")} disabled={disabled}>
          Prendre la source
        </button>
      </div>
    </div>
  );
}
