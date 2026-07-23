import { BookOpenCheck, Clock3, ListChecks } from "lucide-react";
import { useRef, type KeyboardEvent } from "react";
import type { ResultsView } from "./resultsState";

const tabs = [
  { view: "ues" as const, label: "Par UE", icon: BookOpenCheck },
  { view: "evaluations" as const, label: "Évaluations", icon: ListChecks },
  { view: "recent" as const, label: "Nouveautés", icon: Clock3 },
];

export function ResultsTabs({ active, onChange }: { active: ResultsView; onChange: (view: ResultsView) => void }) {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);

  const moveFocus = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const nextIndex =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? tabs.length - 1
          : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    const next = tabs[nextIndex]!;
    refs.current[nextIndex]?.focus();
    onChange(next.view);
  };

  return (
    <div className="results-tabs" role="tablist" aria-label="Vues des résultats">
      {tabs.map((tab, index) => (
        <button
          key={tab.view}
          ref={(element) => {
            refs.current[index] = element;
          }}
          type="button"
          role="tab"
          id={`results-tab-${tab.view}`}
          aria-selected={active === tab.view}
          aria-controls={`results-panel-${tab.view}`}
          tabIndex={active === tab.view ? 0 : -1}
          className={active === tab.view ? "active" : undefined}
          onClick={() => onChange(tab.view)}
          onKeyDown={(event) => moveFocus(event, index)}
        >
          <tab.icon size={18} />
          <span>{tab.label}</span>
        </button>
      ))}
    </div>
  );
}
