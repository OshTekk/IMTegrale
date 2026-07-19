import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { installFakeLearningApi } from "./learning-fixtures";

async function expectNoSeriousA11yViolations(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const violations = results.violations
    .filter((violation) => violation.impact === "serious" || violation.impact === "critical")
    .map((violation) => ({
      id: violation.id,
      impact: violation.impact,
      targets: violation.nodes.map((node) => node.target),
    }));
  expect(violations).toEqual([]);
}

test("la route directe refuse un token owner sans sonder Parcours", async ({ page }) => {
  const state = await installFakeLearningApi(page, "token");
  await page.goto("/parcours");

  await expect(page).toHaveURL("/");
  await expect(page.getByRole("link", { name: "Parcours" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Réussir ma 2A" })).toHaveCount(0);
  await expectNoSeriousA11yViolations(page);
  expect(state.learningRequests).toEqual([]);
  expect(state.externalRequests).toEqual([]);
});

test("un propriétaire primaire non éligible ne voit ni navigation ni CTA", async ({ page }) => {
  const state = await installFakeLearningApi(page, "noneligible");
  await page.goto("/parcours");

  await expect(page).toHaveURL("/");
  await expect(page.getByRole("link", { name: "Parcours" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Réussir ma 2A" })).toHaveCount(0);
  await expectNoSeriousA11yViolations(page);
  expect(state.learningRequests).toEqual([]);
  expect(state.externalRequests).toEqual([]);
});

test("une passkey expirée affiche la revérification et piège le focus dans la modale", async ({ page }) => {
  const state = await installFakeLearningApi(page, "reverify");
  await page.goto("/parcours");

  await expect(page.getByRole("heading", { name: "Confirme ton statut étudiant" })).toBeVisible();
  const trigger = page.getByRole("button", { name: "Vérifier avec mon compte IMT" });
  await trigger.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Vérifier mon statut étudiant" });
  await expect(dialog).toBeFocused();
  await expectNoSeriousA11yViolations(page);
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Fermer" })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(page.getByRole("button", { name: "Annuler" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await expectNoSeriousA11yViolations(page);
  expect(state.learningRequests).toEqual([]);
  expect(state.externalRequests).toEqual([]);
});

test("le catalogue indisponible reste explicite sans détail de fichier", async ({ page }) => {
  const state = await installFakeLearningApi(page, "unavailable");
  await page.goto("/parcours");

  await expect(page.getByRole("heading", { name: "Parcours temporairement indisponible" })).toBeVisible();
  await expect(page.getByText("Le catalogue pédagogique ne peut pas être chargé en toute sécurité.")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("manifest.json");
  await expect(page.locator("body")).not.toContainText("/opt/");
  await expectNoSeriousA11yViolations(page);
  expect(state.externalRequests).toEqual([]);
});

test("les états de chargement et d'erreur générique sont rendus sans fuite", async ({ page }) => {
  const loadingState = await installFakeLearningApi(page);
  loadingState.accessDelayMs = 2_500;
  await page.goto("/parcours");
  await expect(page.getByLabel("Vérification de l'accès au parcours")).toBeVisible();
  await expectNoSeriousA11yViolations(page);
  await expect(page.getByRole("heading", { name: "Comprendre, pratiquer, progresser" })).toBeVisible();
  expect(loadingState.externalRequests).toEqual([]);

  const errorPage = await page.context().newPage();
  const errorState = await installFakeLearningApi(errorPage, "error");
  await errorPage.goto("/parcours");
  await expect(errorPage.getByRole("heading", { name: "Chargement impossible" })).toBeVisible();
  await expect(errorPage.locator("body")).not.toContainText("Erreur fictive générique");
  await expectNoSeriousA11yViolations(errorPage);
  expect(errorState.externalRequests).toEqual([]);
  await errorPage.close();
});

test("le parcours heureux est navigable au clavier et conserve les headers privés", async ({ page }) => {
  const state = await installFakeLearningApi(page);
  const accessResponse = page.waitForResponse((response) => response.url().endsWith("/api/v1/learning/access"));
  await page.goto("/parcours");

  await expect(page.getByRole("heading", { name: "Comprendre, pratiquer, progresser" })).toBeVisible();
  const response = await accessResponse;
  expect(response.headers()["cache-control"]).toBe("private, no-store");
  expect(response.headers()["x-robots-tag"]).toBe("noindex, nofollow, noarchive");
  expect(response.headers()["x-content-type-options"]).toBe("nosniff");

  await page.getByRole("link", { name: /DÉMO FICTIVE — UE Zorbion/ }).focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/parcours\/ues\/ue-fictive$/);
  await expect(page.locator("#main-content")).toBeFocused();
  await page.getByRole("link", { name: /DÉMO FICTIVE — module Alpha/ }).click();
  await page.getByRole("link", { name: /DÉMO FICTIVE — leçon Alpha/ }).click();
  await expect(page.getByRole("heading", { name: "DÉMO FICTIVE — leçon Alpha", level: 1 })).toBeVisible();
  await expect(page.getByLabel("Formule mathématique : z = alpha + 1")).toBeVisible();
  await expectNoSeriousA11yViolations(page);
  expect(state.externalRequests).toEqual([]);
});

test("la recherche serveur ouvre le résultat sans transmettre l'index", async ({ page }) => {
  const state = await installFakeLearningApi(page);
  await page.goto("/parcours");

  const searchLink = page.getByRole("link", { name: "Rechercher" }).last();
  await searchLink.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main-content")).toBeFocused();
  await page.getByRole("searchbox", { name: "Cours, concept, exercice ou source" }).fill("zorbion");
  await page.getByRole("button", { name: "Rechercher" }).click();
  await expect(page.getByRole("link", { name: /DÉMO FICTIVE — exercice Zorbion/ })).toBeVisible();
  expect(state.searchQueries).toEqual(["zorbion"]);
  await page.getByRole("link", { name: /DÉMO FICTIVE — exercice Zorbion/ }).click();
  await expect(page.getByRole("heading", { name: "DÉMO FICTIVE — exercice Zorbion", level: 1 })).toBeVisible();
  await expectNoSeriousA11yViolations(page);
  expect(state.externalRequests).toEqual([]);
});

test("une référence ouvre exactement la page demandée dans le viewer PDF local", async ({ page }) => {
  const state = await installFakeLearningApi(page);
  await page.goto("/parcours/lecons/lesson-fictive");

  await page.getByRole("link", { name: "Consulter la page fictive 2", exact: true }).click();
  await expect(page).toHaveURL(/\/parcours\/sources\/source-fictive\?page=2$/);
  await expect(page.getByText("Page demandée : 2")).toBeVisible();
  const viewer = page.locator("object.learning-pdf-object");
  await expect(viewer).toHaveAttribute("type", "application/pdf");
  await expect(viewer).toHaveAttribute("data", /^blob:.*#page=2$/);
  await expect(viewer).toHaveAttribute("aria-label", "DÉMO FICTIVE — source blanche, page 2");
  await expectNoSeriousA11yViolations(page);
  expect(state.externalRequests).toEqual([]);
});

test("les indices et la progression restent personnels et réinitialisables", async ({ page }) => {
  const state = await installFakeLearningApi(page);
  await page.goto("/parcours/exercices/exercise-fictif");

  await expect(page.getByText("0/1 ouverts")).toBeVisible();
  await page.getByRole("button", { name: "Ouvrir l'indice 1" }).click();
  await expect(page.getByText("Indice 1 sur 1")).toBeVisible();
  await expect(page.getByText("Commencer par isoler le symbole alpha.")).toBeVisible();
  await page.getByRole("button", { name: "Compris" }).click();
  await page.getByRole("button", { name: "J'ai terminé" }).click();
  await expect.poll(() => state.progress.get("exercise-fictif")?.completed).toBe(true);
  await expect.poll(() => state.progress.get("exercise-fictif")?.self_assessment).toBe(3);

  await page.getByRole("link", { name: "Progression" }).last().click();
  await expect(page.getByRole("heading", { name: "Ma progression" })).toBeVisible();
  const deleteButton = page.getByRole("button", { name: "Tout supprimer" });
  await deleteButton.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Supprimer toute la progression ?" });
  await expect(dialog).toBeFocused();
  await expectNoSeriousA11yViolations(page);
  await page.getByRole("button", { name: "Supprimer", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Aucune progression" })).toBeVisible();
  await expectNoSeriousA11yViolations(page);
  expect(state.progress.size).toBe(0);
  expect(state.attempts).toEqual([]);
  expect(state.externalRequests).toEqual([]);
});

const responsiveCases = [
  { width: 320, height: 720, colorScheme: "light" as const },
  { width: 375, height: 812, colorScheme: "dark" as const },
  { width: 768, height: 900, colorScheme: "light" as const },
  { width: 1024, height: 900, colorScheme: "dark" as const },
  { width: 1440, height: 1000, colorScheme: "light" as const },
];

for (const viewport of responsiveCases) {
  test(`rendu accessible ${viewport.width}px, ${viewport.colorScheme}, reduced-motion`, async ({ page }) => {
    const state = await installFakeLearningApi(page);
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.emulateMedia({ colorScheme: viewport.colorScheme, reducedMotion: "reduce" });
    await page.goto("/parcours");

    await expect(page.getByRole("heading", { name: "Comprendre, pratiquer, progresser" })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("data-theme", viewport.colorScheme);
    expect(await page.evaluate(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(true);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(1);
    await expectNoSeriousA11yViolations(page);
    expect(state.externalRequests).toEqual([]);
  });
}
