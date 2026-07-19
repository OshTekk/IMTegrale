import { describe, expect, it } from "vitest";
import { isPrimaryOwnerSession } from "./auth";

describe("isPrimaryOwnerSession", () => {
  it.each(["imt", "passkey"] as const)("accepts an owner authenticated with %s", (authMethod) => {
    expect(isPrimaryOwnerSession({ role: "owner", auth_method: authMethod })).toBe(true);
  });

  it("rejects delegated, viewer, and incomplete sessions", () => {
    expect(isPrimaryOwnerSession({ role: "owner", auth_method: "token" })).toBe(false);
    expect(isPrimaryOwnerSession({ role: "viewer", auth_method: "passkey" })).toBe(false);
    expect(isPrimaryOwnerSession({ role: "owner" })).toBe(false);
  });
});
