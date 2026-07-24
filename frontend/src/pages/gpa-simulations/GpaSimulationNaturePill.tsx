import { BadgeCheck, FlaskConical, Sparkles } from "lucide-react";
import { natureLabel, type GpaEntryNature } from "./gpaSimulationPresentation";

export function GpaSimulationNaturePill({ nature }: { nature: GpaEntryNature }) {
  return (
    <span className={`gpa-nature-pill is-${nature}`}>
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
