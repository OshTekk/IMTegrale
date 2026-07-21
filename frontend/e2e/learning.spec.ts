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

async function expectCleanReaderCopy(page: Page) {
  const body = page.locator("body");
  await expect(body).not.toContainText(/private[ _-]?preview/i);
  await expect(body).not.toContainText(/Brouillon privé/i);
  await expect(body).not.toContainText(/titre non renseigné/i);
  await expect(body).not.toContainText("synthetic-release-private-preview-001");
}

test("la route directe refuse un token owner sans sonder Parcours", async ({ page }) => {
  const state = await installFakeLearningApi(page, "token");
  await page.goto("/parcours");

  await expect(page).toHaveURL("/");
  await expect(page.getByRole("link", { name: "Parcours" })).toHaveCount(0);
  await expectNoSeriousA11yViolations(page);
  expect(state.learningRequests).toEqual([]);
  expect(state.externalRequests).toEqual([]);
});

test("un propriétaire primaire non éligible ne voit ni navigation ni CTA", async ({ page }) => {
  const state = await installFakeLearningApi(page, "noneligible");
  await page.goto("/parcours");

  await expect(page).toHaveURL("/");
  await expect(page.getByRole("link", { name: "Parcours" })).toHaveCount(0);
  await expectNoSeriousA11yViolations(page);
  expect(state.learningRequests).toEqual([]);
  expect(state.externalRequests).toEqual([]);
});

test("une passkey expirée affiche la revérification sans audience interne et piège le focus", async ({ page }) => {
  const state = await installFakeLearningApi(page, "reverify");
  await page.goto("/parcours");

  await expect(page.getByRole("heading", { name: "Confirme ton statut étudiant" })).toBeVisible();
  await expect(page.locator("#main-content").getByText("Niveau 2A fictif", { exact: true })).toBeVisible();
  await expect(page.locator("body")).not.toContainText(/private[ _-]?preview/i);
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
  await expect(page.getByRole("heading", { name: "Apprendre avec un fil clair" })).toBeVisible();
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

test("l'accueil éditorial reprend la progression et ne révèle aucun détail de fabrication", async ({ page }) => {
  const state = await installFakeLearningApi(page);
  const accessResponse = page.waitForResponse((response) => response.url().endsWith("/api/v1/learning/access"));
  await page.goto("/parcours");

  await expect(page.getByRole("heading", { name: "Apprendre avec un fil clair" })).toBeVisible();
  await expect(page.getByText("Cursus fictif 2099 · Niveau 2A fictif").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Continuer là où je me suis arrêté" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "UE disponibles" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "À revoir" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Favoris" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Progression récente" })).toBeVisible();
  await expect(page.getByRole("link", { name: /Lire une relation/ }).first()).toBeVisible();
  await expectCleanReaderCopy(page);

  const response = await accessResponse;
  expect(response.headers()["cache-control"]).toBe("private, no-store");
  expect(response.headers()["x-robots-tag"]).toBe("noindex, nofollow, noarchive");
  expect(response.headers()["x-content-type-options"]).toBe("nosniff");
  await expectNoSeriousA11yViolations(page);
  expect(state.externalRequests).toEqual([]);
});

test("la page module sépare cours, pratique, annales, révision, glossaire et documents", async ({ page }) => {
  const state = await installFakeLearningApi(page);
  await page.goto("/parcours");

  const ueLink = page.getByRole("link", { name: /Sciences imaginaires/ }).first();
  await ueLink.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/parcours\/ues\/ue-fictive$/);
  await expect(page.locator("#main-content")).toBeFocused();
  await page.getByRole("link", { name: /Raisonnement symbolique/ }).click();

  await expect(page.getByRole("heading", { name: "Raisonnement symbolique", level: 1 })).toBeVisible();
  await expect(page.getByText("MOD-FIC", { exact: true })).toBeVisible();
  await expect(page.getByText("Version de travail", { exact: true })).toHaveCount(1);
  await expect(page.getByRole("heading", { name: "Comprendre" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "S'entraîner" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Annales" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Réviser" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Glossaire" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Documents" })).toBeVisible();

  const course = page.locator("#comprendre");
  await expect(course).toContainText("Lire une relation");
  await expect(course).not.toContainText("Symbole alpha");
  await expect(course).not.toContainText("Carnet synthétique");
  await expect(page.locator("#glossaire")).toContainText("Symbole alpha");
  await expect(page.locator("#documents")).toContainText("Carnet synthétique");
  await expect(page.locator("#documents")).toContainText("Document pédagogique");
  await expect(page.locator("#documents .learning-editorial-row-marker b")).toHaveCount(0);
  await expectCleanReaderCopy(page);

  await page.getByRole("button", { name: "Revue" }).click();
  const reviewPanel = page.getByRole("complementary", { name: "Métadonnées de revue" });
  await expect(reviewPanel).toBeVisible();
  await expect(reviewPanel).toContainText("synthetic-release-private-preview-001");
  await expect(reviewPanel).toContainText("Version de travail");
  await expectNoSeriousA11yViolations(page);
  expect(state.externalRequests).toEqual([]);
});

test("les formules sont rendues par KaTeX et restent accessibles", async ({ page }) => {
  const state = await installFakeLearningApi(page);
  await page.goto("/parcours/lecons/lesson-fictive");

  await expect(page.getByRole("heading", { name: "Lire une relation", level: 1 })).toBeVisible();
  await expect(page.locator("[data-math-rendered='true']")).toHaveCount(2);
  await expect(page.locator(".learning-math-inline .katex")).toBeVisible();
  await expect(page.locator(".learning-math-block .katex-display")).toBeVisible();
  await expect(page.locator(".learning-math math")).toHaveCount(2);
  await expect(page.locator(".learning-math-error")).toHaveCount(0);
  await expect(page.locator(".learning-reading-main code").filter({ hasText: "alpha" })).toHaveCount(0);
  await expectCleanReaderCopy(page);
  await expectNoSeriousA11yViolations(page);
  expect(state.externalRequests).toEqual([]);
});

test("la recherche serveur ouvre un résultat nettoyé", async ({ page }) => {
  const state = await installFakeLearningApi(page);
  await page.goto("/parcours");

  const searchLink = page.getByRole("link", { name: "Rechercher" }).last();
  await searchLink.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main-content")).toBeFocused();
  await page.getByRole("searchbox", { name: "Cours, concept, exercice ou source" }).fill("zorbion");
  await page.getByRole("button", { name: "Rechercher" }).click();
  await expect(page.getByRole("link", { name: /Manipuler un zorbion/ })).toBeVisible();
  await expectCleanReaderCopy(page);
  expect(state.searchQueries).toEqual(["zorbion"]);
  await page.getByRole("link", { name: /Manipuler un zorbion/ }).click();
  await expect(page.getByRole("heading", { name: "Manipuler un zorbion", level: 1 })).toBeVisible();
  await expectNoSeriousA11yViolations(page);
  expect(state.externalRequests).toEqual([]);
});

test("une référence ouvre la page exacte dans PDF.js et le lecteur reste pilotable au clavier", async ({ page }) => {
  const state = await installFakeLearningApi(page);
  await page.goto("/parcours/lecons/lesson-fictive");

  await page.getByRole("link", { name: "Consulter la page fictive 2", exact: true }).click();
  await expect(page).toHaveURL(/\/parcours\/sources\/source-fictive\?page=2$/);
  const viewer = page.getByRole("region", { name: "Lecteur PDF : Carnet synthétique" });
  await expect(viewer).toBeVisible();
  await expect(viewer.locator("canvas")).toBeVisible();
  const pageInput = viewer.getByRole("spinbutton", { name: "Page" });
  await expect(pageInput).toHaveValue("2");
  await expect(viewer).toContainText("sur 2");
  await expect(page.locator("object, embed")).toHaveCount(0);
  await expect(page.locator("[data^='blob:'], [src^='blob:']")).toHaveCount(0);

  await viewer.focus();
  await page.keyboard.press("ArrowLeft");
  await expect(pageInput).toHaveValue("1");
  const documentSearch = viewer.getByRole("searchbox", { name: "Rechercher dans le document" });
  await documentSearch.fill("zorbion");
  await viewer.getByRole("button", { name: "Rechercher" }).click();
  await expect(pageInput).toHaveValue("2");
  await expect(viewer).toContainText("1 page");
  await expectCleanReaderCopy(page);
  await expectNoSeriousA11yViolations(page);
  expect(state.assetRequests).toBeGreaterThan(0);
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
  test(`module accessible à ${viewport.width}px, ${viewport.colorScheme}, reduced-motion`, async ({ page }) => {
    const state = await installFakeLearningApi(page);
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.emulateMedia({ colorScheme: viewport.colorScheme, reducedMotion: "reduce" });
    await page.goto("/parcours/modules/module-fictif");

    await expect(page.getByRole("heading", { name: "Raisonnement symbolique", level: 1 })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("data-theme", viewport.colorScheme);
    expect(await page.evaluate(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(true);
    const reducedMotion = await page
      .locator(".learning-editorial-row")
      .first()
      .evaluate((element) => {
        const style = getComputedStyle(element);
        return { animationDuration: style.animationDuration, transitionDuration: style.transitionDuration };
      });
    expect(Number.parseFloat(reducedMotion.animationDuration)).toBeLessThanOrEqual(0.001);
    expect(Number.parseFloat(reducedMotion.transitionDuration)).toBeLessThanOrEqual(0.001);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(1);
    await expectCleanReaderCopy(page);
    await expectNoSeriousA11yViolations(page);
    expect(state.externalRequests).toEqual([]);
  });
}
