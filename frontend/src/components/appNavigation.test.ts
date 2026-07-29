import { describe, expect, it } from "vitest";
import type { Session } from "../types";
import {
  appNavItems,
  appPageHeading,
  isAppNavItemActive,
  mobileAppNavigation,
  visibleAppNavigation,
} from "./appNavigation";

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
    private_comparisons: { available: true },
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
    expect(isAppNavItemActive("/results", "/ues/releve")).toBe(true);
    expect(appPageHeading("/results/ue/UE-FICTIVE", session)).toEqual([
      "Résultats",
      "UE, évaluations et nouveautés dans un espace unique",
    ]);
  });
});

describe("navigation mobile selon le niveau d'accès", () => {
  it("keeps four primary destinations and a populated Plus menu for a primary owner", () => {
    const session = fictitiousSession("FIP 2028", "2A");
    const navigation = mobileAppNavigation(session, true);
    expect(navigation.primary.map((item) => item.to)).toEqual(["/", "/results", "/calendar", "/simulations/gpa"]);
    expect(navigation.secondary.map((item) => item.to)).toEqual([
      "/parcours",
      "/leaderboard",
      "/comparisons",
      "/sharing",
      "/settings",
    ]);
    expect(navigation.primary).toHaveLength(4);
  });

  it("never reserves forbidden owner destinations for a viewer", () => {
    const session: Session = {
      ...fictitiousSession("FIP 2028", "2A"),
      role: "viewer",
      auth_method: "token",
      learning: { ...fictitiousSession("FIP 2028", "2A").learning!, available: false },
    };
    const navigation = mobileAppNavigation(session, false);
    expect(navigation.primary.map((item) => item.to)).toEqual(["/", "/results", "/settings"]);
    expect(navigation.secondary).toEqual([]);
  });

  it("keeps delegated owner pages behind Plus without exposing primary-only routes", () => {
    const session: Session = {
      ...fictitiousSession("FIP 2028", "2A"),
      auth_method: "token",
      learning: { ...fictitiousSession("FIP 2028", "2A").learning!, available: false },
    };
    const navigation = mobileAppNavigation(session, false);
    expect(navigation.primary.map((item) => item.to)).toEqual(["/", "/results", "/settings"]);
    expect(navigation.secondary.map((item) => item.to)).toEqual(["/leaderboard", "/sharing"]);
    expect(navigation.primary.some((item) => item.to === "/calendar" || item.to.startsWith("/simulations"))).toBe(
      false,
    );
  });

  it("keeps Comparaisons in Plus and active on every private sub-route", () => {
    const session = fictitiousSession("FIP 2028", "2A");
    const navigation = mobileAppNavigation(session, true);
    expect(navigation.primary.map((item) => item.to)).not.toContain("/comparisons");
    expect(navigation.secondary.map((item) => item.to)).toContain("/comparisons");
    expect(isAppNavItemActive("/comparisons", "/comparisons")).toBe(true);
    expect(isAppNavItemActive("/comparisons", "/comparisons/accept")).toBe(true);
    expect(isAppNavItemActive("/comparisons", "/comparisons/pc_synthetic-public-id")).toBe(true);
  });

  it("hides Comparaisons as soon as the session capability closes", () => {
    const session = fictitiousSession("FIP 2028", "2A");
    session.private_comparisons = { available: false };
    expect(visibleAppNavigation(session, true).some((item) => item.to === "/comparisons")).toBe(false);
  });
});
