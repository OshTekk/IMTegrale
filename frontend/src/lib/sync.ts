import { useEffect, useState } from "react";
import type { ManualSyncStatus } from "../types";

export function formatSyncDuration(value: number): string {
  const total = Math.max(0, Math.ceil(value));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours && minutes) return `${hours} h ${minutes} min`;
  if (hours) return `${hours} h`;
  if (minutes && seconds) return `${minutes} min ${seconds} s`;
  if (minutes) return `${minutes} min`;
  return `${seconds} s`;
}

export function useServerCountdown(status: ManualSyncStatus | null | undefined): number {
  const [remaining, setRemaining] = useState(status?.retry_after_seconds ?? 0);

  useEffect(() => {
    const initial = status?.retry_after_seconds ?? 0;
    const startedAt = Date.now();
    setRemaining(initial);
    if (initial <= 0) return;
    const timer = window.setInterval(() => {
      const elapsed = Math.floor((Date.now() - startedAt) / 1000);
      setRemaining(Math.max(0, initial - elapsed));
    }, 250);
    return () => window.clearInterval(timer);
  }, [status?.retry_after_seconds, status?.server_time, status?.state]);

  return remaining;
}

export function manualSyncMessage(
  status: ManualSyncStatus | null | undefined,
  remaining: number
): string {
  if (!status) return "Vérification de la disponibilité";
  if (status.state === "in_progress") return "Synchronisation en cours";
  if (status.state === "cooldown") {
    return remaining > 0
      ? `Synchronisation récente. Réessaie dans ${formatSyncDuration(remaining)}.`
      : "Vérification du prochain créneau";
  }
  if (status.state === "pass_unavailable") {
    return remaining > 0
      ? `PASS disponible dans ${formatSyncDuration(remaining)}.`
      : "Vérification du prochain créneau PASS";
  }
  return "Synchronisation manuelle disponible";
}
