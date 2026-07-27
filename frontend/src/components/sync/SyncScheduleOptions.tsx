import { Clock3, Zap } from "lucide-react";
import { useId } from "react";

export type SyncInterval = 2 | 4 | 6 | 8 | 12 | 24;

interface SyncScheduleOptionsProps {
  interval: SyncInterval;
  adaptive: boolean;
  allowedIntervals: readonly SyncInterval[];
  disabled?: boolean;
  onIntervalChange: (interval: SyncInterval) => void;
  onAdaptiveChange: (adaptive: boolean) => void;
}

export function SyncScheduleOptions({
  interval,
  adaptive,
  allowedIntervals,
  disabled = false,
  onIntervalChange,
  onAdaptiveChange,
}: SyncScheduleOptionsProps) {
  const intervalId = useId();
  return (
    <section className="sync-schedule-options" aria-labelledby={`${intervalId}-title`}>
      <div>
        <label id={`${intervalId}-title`} htmlFor={intervalId}>
          Fréquence de base
        </label>
        <select
          id={intervalId}
          value={interval}
          disabled={disabled}
          onChange={(event) => onIntervalChange(Number(event.target.value) as SyncInterval)}
        >
          {allowedIntervals.map((hours) => (
            <option key={hours} value={hours}>
              {hours === 24 ? "Une fois par jour" : `Toutes les ${hours} heures`}
            </option>
          ))}
        </select>
      </div>
      <label className="adaptive-control">
        <span>
          <Zap size={17} />
          <span>
            <strong>Cadence adaptative</strong>
            <small>Ralentit après plusieurs passages sans changement.</small>
          </span>
        </span>
        <input
          type="checkbox"
          checked={adaptive}
          disabled={disabled}
          onChange={(event) => onAdaptiveChange(event.target.checked)}
        />
      </label>
      <p className="sync-schedule-window">
        <Clock3 size={16} />
        Du lundi au vendredi, entre 8 h et 20 h. Deux heures est la fréquence maximale.
      </p>
    </section>
  );
}
