import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { join } from "node:path";
import { installFakeAppApi, installFakeEventSource, installFakeWebAuthn } from "./app-fixtures";

declare global {
  interface Window {
    __emitSyntheticUpdate: () => void;
  }
}

async function expectNoSeriousA11yViolations(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(
    results.violations
      .filter((violation) => violation.impact === "serious" || violation.impact === "critical")
      .map((violation) => ({ id: violation.id, targets: violation.nodes.map((node) => node.target) })),
  ).toEqual([]);
}

test("la connexion IMT fictive ouvre une session primaire sans appel externe", async ({ page }) => {
  const state = await installFakeAppApi(page, "anonymous");
  await installFakeEventSource(page);
  await page.goto("/");

  await page.getByLabel("Identifiant CAS / IMT Atlantique").fill("demo.fictif");
  await page.getByLabel("Mot de passe IMT").fill("mot-de-passe-entierement-fictif");
  await page.getByRole("button", { name: "Se connecter avec l'IMT" }).click();

  await expect(page.getByRole("heading", { name: "Vue d'ensemble" })).toBeVisible();
  expect(state.loginRequests).toEqual([{ username: "demo.fictif", password: "mot-de-passe-entierement-fictif" }]);
  expect(state.csrfHeaders).toContain("csrf-app-e2e-fictif");
  expect(state.externalRequests).toEqual([]);
  await expectNoSeriousA11yViolations(page);
});

test("un token owner délègue uniquement un accès viewer et ne gère pas les passkeys", async ({ page }) => {
  const state = await installFakeAppApi(page, "token");
  await installFakeEventSource(page);
  await page.goto("/settings");

  await expect(page.locator(".privacy-note strong").filter({ hasText: "Reconnexion requise." }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Ajouter" })).toHaveCount(0);
  await page.getByRole("link", { name: "Partage" }).first().click();
  await page.getByLabel("Nom de la personne ou de l'appareil").fill("Accès fictif");
  await page.getByRole("button", { name: "Générer le token" }).click();

  await expect(page.getByRole("heading", { name: "Token créé" })).toBeVisible();
  expect(state.tokenCreates).toEqual([{ name: "Accès fictif", role: "viewer", expires_in_days: 30 }]);
  expect(state.csrfHeaders).toContain("csrf-app-e2e-fictif");
  expect(state.externalRequests).toEqual([]);
});

test("un viewer reste en lecture seule même sur une route owner directe", async ({ page }) => {
  const state = await installFakeAppApi(page, "viewer");
  await installFakeEventSource(page);
  await page.goto("/sharing");

  await expect(page).toHaveURL("/");
  await expect(page.getByRole("link", { name: "Partage" })).toHaveCount(0);
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "Accès en lecture seule" })).toBeVisible();
  await expectNoSeriousA11yViolations(page);
  expect(state.externalRequests).toEqual([]);
});

test("une session passkey primaire peut créer puis supprimer une passkey", async ({ page }) => {
  const state = await installFakeAppApi(page, "passkey");
  await installFakeEventSource(page);
  await installFakeWebAuthn(page);
  await page.goto("/settings");

  await page.getByLabel("Nom de la passkey").fill("Nouvel appareil fictif");
  await page.getByRole("button", { name: "Ajouter" }).click();
  await expect.poll(() => state.passkeyCreates).toBe(1);
  await page.getByRole("button", { name: "Supprimer Appareil fictif" }).click();
  await expect.poll(() => state.passkeyDeletes).toEqual(["passkey-fictive"]);
  expect(state.csrfHeaders.every((value) => value === "csrf-app-e2e-fictif")).toBe(true);
  expect(state.externalRequests).toEqual([]);
});

test("la synchronisation et un événement SSE invalident les données du compte", async ({ page }) => {
  const state = await installFakeAppApi(page, "imt");
  await installFakeEventSource(page);
  await page.goto("/");
  await expect(page.getByText("Données en direct")).toBeVisible();
  await expect.poll(() => state.dashboardRequests).toBe(1);

  await page.getByRole("button", { name: /Synchronisation manuelle disponible/ }).click();
  await expect.poll(() => state.syncRequests).toBe(1);
  await page.evaluate(() => window.__emitSyntheticUpdate());
  await expect.poll(() => state.dashboardRequests).toBeGreaterThanOrEqual(2);
  expect(state.externalRequests).toEqual([]);
});

test("les erreurs API stables restent compréhensibles sans détail interne", async ({ page }) => {
  const state = await installFakeAppApi(page, "imt");
  state.dashboardError = true;
  await installFakeEventSource(page);
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Données indisponibles" })).toBeVisible();
  await expect(page.getByText("Service fictif indisponible.")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("traceback");
  await expectNoSeriousA11yViolations(page);
});

const visualCases = [
  { width: 320, height: 720, theme: "light" as const },
  { width: 375, height: 812, theme: "dark" as const },
  { width: 768, height: 900, theme: "light" as const },
  { width: 1024, height: 900, theme: "dark" as const },
  { width: 1440, height: 1000, theme: "light" as const },
];

for (const visual of visualCases) {
  test(`régression visuelle du shell ${visual.width}px ${visual.theme}`, async ({ page }) => {
    const state = await installFakeAppApi(page, "imt");
    await installFakeEventSource(page);
    await page.setViewportSize({ width: visual.width, height: visual.height });
    await page.emulateMedia({ colorScheme: visual.theme, reducedMotion: "reduce" });
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: "Paramètres" })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("data-theme", visual.theme);
    await expectNoSeriousA11yViolations(page);
    await expect(page).toHaveScreenshot(`shell-${visual.width}-${visual.theme}.png`, {
      animations: "disabled",
      fullPage: true,
      maxDiffPixelRatio: 0.03,
      stylePath: join(import.meta.dirname, "visual-snapshot.css"),
    });
    expect(state.externalRequests).toEqual([]);
  });
}
