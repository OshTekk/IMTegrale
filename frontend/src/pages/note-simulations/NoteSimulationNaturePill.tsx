import { BadgeCheck, FlaskConical, Sparkles } from "lucide-react";
import { natureLabel, type NoteSimulationNature } from "./noteSimulationPresentation";

export function NoteSimulationNaturePill({ nature }: { nature: NoteSimulationNature }) {
  return (
    <span className={`simulation-origin-pill ${nature}`}>
      {nature === "imported" ? (
        <BadgeCheck size={13} />
      ) : nature === "modified" ? (
        <Sparkles size={13} />
      ) : (
        <FlaskConical size={13} />
      )}
      {natureLabel(nature)}
    </span>
  );
}
