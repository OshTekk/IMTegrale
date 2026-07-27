// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SettingsResponse } from "../../generated/api/types.gen";
import { expectNoSeriousLearningViolations } from "../../test/learningTestA11y";
import { AutonomousSyncEnrollmentModal } from "./AutonomousSyncEnrollmentModal";
import { SyncModeSelector } from "./SyncModeSelector";

const sdkMocks = vi.hoisted(() => ({
  enroll: vi.fn(),
  updateMode: vi.fn(),
}));

vi.mock("../../generated/api/sdk.gen", () => ({
  settingsEnrollSyncCredential: sdkMocks.enroll,
  settingsUpdateSyncMode: sdkMocks.updateMode,
}));

function generatedResult(data: SettingsResponse) {
  return Promise.resolve({
    data,
    request: new Request("https://example.test/api/v1/settings"),
    response: new Response(),
  });
}

function syntheticSettings(mode: "manual" | "session_only" | "autonomous", configured: boolean): SettingsResponse {
  return {
    sync: {
      mode,
      enabled: mode !== "manual",
      interval_hours: 4,
      adaptive: true,
      current_interval_hours: 4,
      no_change_streak: 0,
      consented_at: "2026-07-27T10:00:00Z",
      paused_reason: null,
      paused_at: null,
      next_eligible_at: null,
      allowed_intervals: [2, 4, 6, 8, 12, 24],
      business_hours: {
        weekdays: "monday-friday",
        start: "08:00",
        end: "20:00",
        timezone: "Europe/Paris",
      },
      pass_access: null,
      service_session: null,
      available_modes: ["manual", "session_only", "autonomous"],
      autonomous: {
        available: true,
        enrollment_available: true,
        runtime_ready: true,
        unavailable_reason: null,
        configured,
        state: configured ? "active" : null,
        activation_pending: configured && mode !== "autonomous",
        consent_version: configured ? 1 : null,
        consented_at: configured ? "2026-07-27T10:00:00Z" : null,
        verified_at: configured ? "2026-07-27T10:00:00Z" : null,
        last_used_at: null,
        last_success_at: null,
        last_failure_at: null,
        needs_reenrollment: false,
      },
    },
  } as SettingsResponse;
}

function withQueryClient(child: React.ReactNode) {
  return (
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
        })
      }
    >
      {child}
    </QueryClientProvider>
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe("expérience des modes de synchronisation", () => {
  it("garde le mode manuel sélectionné et masque l'autonomie hors rollout", async () => {
    const onChange = vi.fn();
    const { container } = render(
      <SyncModeSelector
        value="manual"
        availableModes={["manual", "session_only"]}
        name="mode-fictif"
        onChange={onChange}
      />,
    );

    expect((screen.getByRole("radio", { name: /À la demande/ }) as HTMLInputElement).checked).toBe(true);
    expect(screen.getByRole("radio", { name: /Automatique avec session privée/ })).toBeTruthy();
    expect(screen.queryByRole("radio", { name: /Automatique autonome/ })).toBeNull();
    await expectNoSeriousLearningViolations(container);
  });

  it("présente trois choix accessibles pour un compte éligible", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <SyncModeSelector
        value="manual"
        availableModes={["manual", "session_only", "autonomous"]}
        includeAutonomous
        name="mode-fictif"
        onChange={onChange}
      />,
    );

    const autonomous = screen.getByRole("radio", { name: /Automatique autonome/ });
    await user.click(autonomous);
    expect(onChange).toHaveBeenCalledWith("autonomous");
  });

  it("enrôle puis active sans transmettre le mot de passe à la seconde étape", async () => {
    const user = userEvent.setup();
    const enrolled = syntheticSettings("manual", true);
    const activated = syntheticSettings("autonomous", true);
    sdkMocks.enroll.mockReturnValueOnce(generatedResult(enrolled));
    sdkMocks.updateMode.mockReturnValueOnce(generatedResult(activated));
    const onClose = vi.fn();
    const onSettings = vi.fn();
    const onActivated = vi.fn();
    const syntheticPassword = "FICTIF-G7A-Secret-47";
    render(
      withQueryClient(
        <AutonomousSyncEnrollmentModal
          open
          interval={4}
          adaptive
          onClose={onClose}
          onSettings={onSettings}
          onActivated={onActivated}
        />,
      ),
    );

    const submit = screen.getByRole("button", { name: "Vérifier et activer" });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    const password = screen.getByLabelText("Mot de passe IMT");
    await user.type(password, syntheticPassword);
    await user.click(screen.getByRole("button", { name: "Afficher le mot de passe" }));
    expect(password.getAttribute("type")).toBe("text");
    for (const checkbox of screen.getAllByRole("checkbox")) await user.click(checkbox);
    await user.click(submit);

    await waitFor(() => expect(onActivated).toHaveBeenCalledOnce());
    expect(sdkMocks.enroll).toHaveBeenCalledWith(
      expect.objectContaining({
        body: expect.objectContaining({ password: syntheticPassword }),
      }),
    );
    expect(sdkMocks.updateMode).toHaveBeenCalledWith(
      expect.objectContaining({
        body: {
          mode: "autonomous",
          interval_hours: 4,
          adaptive: true,
        },
      }),
    );
    expect(JSON.stringify(sdkMocks.updateMode.mock.calls)).not.toContain(syntheticPassword);
    expect((password as HTMLInputElement).value).toBe("");
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    expect(onSettings).toHaveBeenCalledTimes(2);
    expect(onClose).toHaveBeenCalledOnce();
    await expectNoSeriousLearningViolations(
      screen.getByRole("dialog", { name: "Activer la synchronisation autonome" }),
    );
  });

  it("conserve un état pending explicite lorsque l'activation échoue après l'enrôlement", async () => {
    const user = userEvent.setup();
    const enrolled = syntheticSettings("manual", true);
    sdkMocks.enroll.mockReturnValueOnce(generatedResult(enrolled));
    sdkMocks.updateMode.mockRejectedValueOnce(new Error("activation synthétique indisponible"));
    const onClose = vi.fn();
    const onSettings = vi.fn();
    const onActivated = vi.fn();
    const syntheticPassword = "FICTIF-G7A-Pending-12";
    render(
      withQueryClient(
        <AutonomousSyncEnrollmentModal
          open
          interval={6}
          adaptive={false}
          onClose={onClose}
          onSettings={onSettings}
          onActivated={onActivated}
        />,
      ),
    );

    const password = screen.getByLabelText("Mot de passe IMT");
    await user.type(password, syntheticPassword);
    for (const checkbox of screen.getAllByRole("checkbox")) await user.click(checkbox);
    await user.click(screen.getByRole("button", { name: "Vérifier et activer" }));

    expect(await screen.findByText(/Le mot de passe est protégé, mais l'activation n'est pas terminée/)).toBeTruthy();
    expect(onSettings).toHaveBeenCalledOnce();
    expect(onSettings).toHaveBeenCalledWith(enrolled);
    expect(onActivated).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect((password as HTMLInputElement).value).toBe("");
    expect(JSON.stringify(sdkMocks.updateMode.mock.calls)).not.toContain(syntheticPassword);
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });
});
