import { describe, expect, it } from "vitest";
import type { ManualSyncStatus } from "../types";
import { formatSyncDuration, manualSyncMessage } from "./sync";

const status = (state: ManualSyncStatus["state"]): ManualSyncStatus => ({
  state,
  can_start: state === "available",
  cooldown_seconds: 600,
  retry_after_seconds: state === "available" ? 0 : 452,
  cooldown_until: state === "available" ? null : "2026-07-16T10:10:00Z",
  active_until: state === "in_progress" ? "2026-07-16T10:15:00Z" : null,
  server_time: "2026-07-16T10:02:28Z",
  last_request: null,
  pass_access: {
    state: "available",
    available: true,
    available_at: "2026-07-16T10:02:28Z",
    retry_after_seconds: 0,
    circuit: { state: "closed", reason: null, next_probe_at: null },
    quota: {
      hour: { used: 0, limit: 3, remaining: 3 },
      day: { used: 0, limit: 8, remaining: 8 },
      available_at: "2026-07-16T10:02:28Z",
      retry_after_seconds: 0,
    },
  },
});

describe("manual synchronization countdown", () => {
  it("formats exact server seconds without hiding the remaining seconds", () => {
    expect(formatSyncDuration(452)).toBe("7 min 32 s");
    expect(formatSyncDuration(120)).toBe("2 min");
    expect(formatSyncDuration(3665)).toBe("1 h 1 min");
    expect(formatSyncDuration(3600)).toBe("1 h");
    expect(formatSyncDuration(-1)).toBe("0 s");
  });

  it("uses neutral and accessible cooldown wording", () => {
    expect(manualSyncMessage(status("cooldown"), 452)).toBe(
      "Synchronisation récente. Réessaie dans 7 min 32 s."
    );
    expect(manualSyncMessage(status("cooldown"), 0)).toBe(
      "Vérification du prochain créneau"
    );
  });

  it("distinguishes an active request from an available action", () => {
    expect(manualSyncMessage(status("in_progress"), 452)).toBe(
      "Synchronisation en cours"
    );
    expect(manualSyncMessage(status("available"), 0)).toBe(
      "Synchronisation manuelle disponible"
    );
    expect(manualSyncMessage(null, 0)).toBe("Vérification de la disponibilité");
  });

  it("keeps an upstream protection message neutral", () => {
    expect(manualSyncMessage(status("pass_unavailable"), 120)).toBe(
      "PASS disponible dans 2 min."
    );
  });
});
