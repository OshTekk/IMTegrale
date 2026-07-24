import { ArrowLeftRight, Copy, EllipsisVertical, RotateCcw, Trash2 } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import type { SimulationConfirmation } from "../../components/simulations/SimulationConfirmationModal";
import {
  SimulationSaveIndicator,
  type SimulationSaveState,
} from "../../components/simulations/SimulationSaveIndicator";
import { formatDate, relativeDate } from "../../lib/format";
import type { SimulationScenarioSummary } from "../../types";

export function GpaSimulationHeader({
  scenario,
  name,
  saveState,
  valid,
  canCompare,
  actionPending,
  onNameChange,
  onCompare,
  onDuplicate,
  onConfirm,
}: {
  scenario: SimulationScenarioSummary;
  name: string;
  saveState: SimulationSaveState;
  valid: boolean;
  canCompare: boolean;
  actionPending: boolean;
  onNameChange: (name: string) => void;
  onCompare: () => void;
  onDuplicate: () => void;
  onConfirm: (confirmation: SimulationConfirmation) => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuId = useId();
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const actionsDisabled = saveState !== "saved" || actionPending;

  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setMenuOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [menuOpen]);

  const run = (action: () => void) => {
    setMenuOpen(false);
    action();
    window.requestAnimationFrame(() => triggerRef.current?.focus());
  };

  return (
    <header className="gpa-workbench-header">
      <div className="gpa-workbench-title">
        <label>
          <span className="sr-only">Nom de la simulation GPA</span>
          <input
            value={name}
            onChange={(event) => onNameChange(event.target.value)}
            maxLength={80}
            disabled={saveState === "conflict"}
          />
        </label>
        <SimulationSaveIndicator state={saveState} valid={valid} />
        <small>
          {scenario.source_captured_at
            ? `Base PASS + COMPETENCES du ${formatDate(scenario.source_captured_at, false)}`
            : "Scénario manuel"}{" "}
          · modifié {relativeDate(scenario.updated_at)}
        </small>
      </div>
      <div className="gpa-workbench-actions">
        <button
          className="secondary-button"
          type="button"
          onClick={onCompare}
          disabled={!canCompare || saveState !== "saved"}
        >
          <ArrowLeftRight size={17} />
          Comparer
        </button>
        <div className="gpa-action-menu" ref={menuRef}>
          <button
            ref={triggerRef}
            className="icon-button"
            type="button"
            aria-label="Actions sur la simulation"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            aria-controls={menuId}
            onClick={() => setMenuOpen((current) => !current)}
          >
            <EllipsisVertical size={19} />
          </button>
          {menuOpen && (
            <div id={menuId} role="menu">
              <button type="button" role="menuitem" onClick={() => run(onDuplicate)} disabled={actionsDisabled}>
                <Copy size={16} /> Dupliquer
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => run(() => onConfirm("reset"))}
                disabled={actionsDisabled}
              >
                <RotateCcw size={16} /> Réinitialiser
              </button>
              <button
                className="danger"
                type="button"
                role="menuitem"
                onClick={() => run(() => onConfirm("delete"))}
                disabled={actionsDisabled}
              >
                <Trash2 size={16} /> Supprimer
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
