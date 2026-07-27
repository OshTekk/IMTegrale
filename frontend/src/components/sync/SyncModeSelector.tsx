import { CalendarClock, Check, Hand, RefreshCw } from "lucide-react";
import type { SyncMode } from "../../generated/api/types.gen";
import { syncModePresentations } from "./syncModeCopy";

const icons = {
  manual: Hand,
  session_only: CalendarClock,
  autonomous: RefreshCw,
} satisfies Record<SyncMode, typeof Hand>;

interface SyncModeSelectorProps {
  value: SyncMode;
  availableModes: readonly SyncMode[];
  includeAutonomous?: boolean;
  disabled?: boolean;
  name: string;
  onChange: (mode: SyncMode) => void;
}

export function SyncModeSelector({
  value,
  availableModes,
  includeAutonomous = false,
  disabled = false,
  name,
  onChange,
}: SyncModeSelectorProps) {
  const visibleModes = syncModePresentations.filter(
    ({ mode }) => mode !== "autonomous" || includeAutonomous || value === "autonomous",
  );

  return (
    <fieldset className="sync-mode-selector">
      <legend>Mode de synchronisation</legend>
      <div className="sync-mode-grid">
        {visibleModes.map((item) => {
          const Icon = icons[item.mode];
          const available = availableModes.includes(item.mode);
          const optionDisabled = disabled || (!available && item.mode !== value);
          return (
            <label
              key={item.mode}
              className={`sync-mode-card${value === item.mode ? " is-selected" : ""}${
                optionDisabled ? " is-disabled" : ""
              }`}
            >
              <input
                type="radio"
                name={name}
                value={item.mode}
                checked={value === item.mode}
                disabled={optionDisabled}
                onChange={() => onChange(item.mode)}
              />
              <span className="sync-mode-card-icon" aria-hidden="true">
                <Icon size={20} />
              </span>
              <span className="sync-mode-card-copy">
                <span className="sync-mode-card-title">
                  <strong>{item.title}</strong>
                  {item.badge && <small>{item.badge}</small>}
                </span>
                <span>{item.summary}</span>
                <span className="sync-mode-card-detail">
                  <b>Données conservées</b>
                  {item.stored}
                </span>
                <span className="sync-mode-card-detail">
                  <b>Avantage</b>
                  {item.benefit}
                </span>
                <span className="sync-mode-card-detail">
                  <b>Limite</b>
                  {item.limit}
                </span>
                {!available && item.mode === value && (
                  <span className="sync-mode-unavailable">Temporairement indisponible</span>
                )}
              </span>
              {value === item.mode && (
                <span className="sync-mode-card-check">
                  <Check size={16} aria-hidden="true" />
                  <span className="sr-only">Mode sélectionné</span>
                </span>
              )}
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
