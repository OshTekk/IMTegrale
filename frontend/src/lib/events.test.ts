import { describe, expect, it } from "vitest";
import { eventReconnectDelay } from "./events";

describe("event stream reconnect delay", () => {
  it("backs off from three seconds and caps the base delay", () => {
    expect(eventReconnectDelay(0, 0)).toBe(3_000);
    expect(eventReconnectDelay(1, 0)).toBe(6_000);
    expect(eventReconnectDelay(4, 0)).toBe(30_000);
    expect(eventReconnectDelay(12, 0)).toBe(30_000);
  });

  it("adds bounded jitter to avoid synchronized reconnects", () => {
    expect(eventReconnectDelay(0, 0.999)).toBe(3_499);
    expect(eventReconnectDelay(-4, 2)).toBe(3_499);
  });
});
