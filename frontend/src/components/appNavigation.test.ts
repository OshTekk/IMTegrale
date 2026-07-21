import { describe, expect, it } from "vitest";
import type { Session } from "../types";
import { appPageHeading } from "./appNavigation";

function fictitiousSession(audienceLabel: string, levelLabel: string): Session {
  return {
    authenticated: true,
    role: "owner",
    auth_method: "imt",
    account: {
      id: "account-synthetic",
      display_name: "Compte fictif",
      imt_username: "synthetic.user",
    },
    learning: {
      available: true,
      audience_label: audienceLabel,
      level_label: levelLabel,
      reverify_required: false,
      catalog_version: "synthetic-catalog-v2",
    },
    needs_security_setup: false,
    needs_sync_setup: false,
  };
}

describe("appPageHeading Parcours", () => {
  it("never exposes an internal audience label in the application header", () => {
    expect(
      appPageHeading(
        "/parcours/lecons/lesson-synthetic",
        fictitiousSession("synthetic-private-preview-audience", "Niveau fictif"),
      ),
    ).toEqual(["Parcours", "Niveau fictif"]);
  });

  it("uses a neutral fallback when every label is internal", () => {
    expect(
      appPageHeading("/parcours", fictitiousSession("personal:synthetic-owner", "release_id:synthetic-r1")),
    ).toEqual(["Parcours", "Espace pédagogique personnel"]);
  });
});
