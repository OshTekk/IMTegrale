import { describe, expect, it } from "vitest";
import type { Session } from "../types";
import { appNavItems, appPageHeading, isAppNavItemActive, visibleAppNavigation } from "./appNavigation";

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

describe("navigation Résultats", () => {
  const session = fictitiousSession("FIP 2028", "2A");

  it("exposes one unified academic entry", () => {
    const navigation = visibleAppNavigation(session, true);
    expect(navigation.filter((item) => item.to === "/results")).toHaveLength(1);
    expect(navigation.some((item) => item.to === "/notes" || item.to === "/ues")).toBe(false);
    expect(appNavItems.filter((item) => item.label === "Résultats")).toHaveLength(1);
  });

  it("keeps the Result entry active on UE deep links", () => {
    expect(isAppNavItemActive("/results", "/results/ue/UE-FICTIVE")).toBe(true);
    expect(appPageHeading("/results/ue/UE-FICTIVE", session)).toEqual([
      "Résultats",
      "UE, évaluations et nouveautés dans un espace unique",
    ]);
  });
});
