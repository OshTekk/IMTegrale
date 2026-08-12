// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider, type ToastKind, useToast } from "./Toast";

const kinds: ToastKind[] = ["success", "error", "warning", "info"];

function ToastHarness() {
  const { showToast } = useToast();
  return (
    <>
      {kinds.map((kind) => (
        <button key={kind} type="button" onClick={() => showToast(`[FICTIF] Toast ${kind}`, kind)}>
          Afficher {kind}
        </button>
      ))}
    </>
  );
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("notifications toast", () => {
  it("conserve les rôles existants et retire chaque action avec son toast", () => {
    vi.useFakeTimers();
    render(
      <ToastProvider>
        <ToastHarness />
      </ToastProvider>,
    );

    for (const kind of kinds) fireEvent.click(screen.getByRole("button", { name: `Afficher ${kind}` }));

    const region = screen.getByRole("region", { name: "Notifications" });
    expect(within(region).getByRole("alert").textContent).toContain("[FICTIF] Toast error");
    expect(within(region).getAllByRole("status")).toHaveLength(3);
    expect(within(region).getAllByRole("button", { name: "Masquer" })).toHaveLength(4);

    fireEvent.click(within(region).getAllByRole("button", { name: "Masquer" })[0]!);
    expect(within(region).getAllByRole("button", { name: "Masquer" })).toHaveLength(3);

    act(() => vi.advanceTimersByTime(4200));
    expect(within(region).queryAllByRole("button", { name: "Masquer" })).toHaveLength(0);
    expect(within(region).queryAllByRole("status")).toHaveLength(0);
    expect(within(region).queryAllByRole("alert")).toHaveLength(0);
  });
});
