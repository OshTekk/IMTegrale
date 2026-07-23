import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { installFakeAppApi, installFakeEventSource, syntheticDashboard } from "./app-fixtures";

async function expectNoSeriousA11yViolations(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(
    results.violations
      .filter((violation) => violation.impact === "serious" || violation.impact === "critical")
      .map((violation) => ({
        id: violation.id,
        targets: violation.nodes.map((node) => node.target),
      })),
  ).toEqual([]);
}

async function openResults(page: Page, url = "/results") {
  await page.goto(url);
  await expect(page.getByRole("heading", { name: "Résultats", exact: true })).toBeVisible();
  await expect(page.getByText("Résultats officiels")).toBeVisible();
}

test("Résultats remplace les deux anciennes entrées et conserve l'historique des vues", async ({ page }) => {
  const state = await installFakeAppApi(page, "imt");
  await installFakeEventSource(page);
  await openResults(page);

  await expect(page).toHaveURL(/\/results\?view=ues$/);
  await expect(page.getByRole("link", { name: "Résultats" }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: "Notes", exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "UE & ECTS", exact: true })).toHaveCount(0);

  const ueTab = page.getByRole("tab", { name: "Par UE" });
  await ueTab.focus();
  await ueTab.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Évaluations" })).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/view=evaluations/);

  await page.getByRole("tab", { name: "Nouveautés" }).click();
  await expect(page).toHaveURL(/view=recent/);
  await page.goBack();
  await expect(page.getByRole("tab", { name: "Évaluations" })).toHaveAttribute("aria-selected", "true");
  await page.reload();
  await expect(page.getByRole("heading", { name: "Évaluations", exact: true })).toBeVisible();
  expect(state.dashboardRequests).toBeGreaterThanOrEqual(1);
  expect(state.externalRequests).toEqual([]);
});

test("les anciennes routes redirigent sans boucle et le relevé conserve sa route", async ({ page }) => {
  const state = await installFakeAppApi(page, "imt");
  await installFakeEventSource(page);

  await page.goto("/notes?semester=S6&q=reseau");
  await expect(page).toHaveURL(/\/results\?view=evaluations&semester=S6&q=reseau$/);
  await expect(page.getByRole("heading", { name: "Évaluations", exact: true })).toBeVisible();

  await page.goto("/ues?semester=S5");
  await expect(page).toHaveURL(/\/results\?view=ues&semester=S5$/);
  await expect(page.getByRole("heading", { name: "Unités d'enseignement" })).toBeVisible();

  await page.goto("/ues/releve");
  await expect(page).toHaveURL("/ues/releve");
  await expect(page.getByRole("heading", { name: "Relevé académique", exact: true })).toBeVisible();
  expect(state.externalRequests).toEqual([]);
});

test("la recherche, les filtres et les libellés de détection restent exacts", async ({ page }) => {
  const state = await installFakeAppApi(page, "imt");
  await installFakeEventSource(page);
  await openResults(page, "/results?view=evaluations");

  await page.getByLabel("Rechercher").fill("EVALUATION RESEAU");
  await expect(page.locator(".results-evaluation-item")).toHaveCount(1);
  await expect(page.getByText("Évaluation réseau fictive")).toBeVisible();
  await page.getByLabel("Rechercher").fill("");
  await page.getByLabel("Type").selectOption("resit");
  await expect(page.getByText("Session de rattrapage fictive")).toBeVisible();
  await expect(page.getByText("Évaluation réseau fictive")).toHaveCount(0);

  await page.getByRole("button", { name: "Réinitialiser" }).click();
  await page.getByLabel("Trier par").selectOption("coefficient");
  await expect(page.locator(".results-evaluation-item").first()).toContainText("Projet fictif");
  await expect(page.getByText(/Importée le/).first()).toBeVisible();
  await expect(page.getByText(/Date de l'examen|Évaluée le/)).toHaveCount(0);
  await expect(page.getByRole("button", { name: /modifier|ajouter|supprimer/i })).toHaveCount(0);
  expect(state.externalRequests).toEqual([]);
});

test("une UE est partageable par lien direct et le retour conserve les filtres", async ({ page }) => {
  const state = await installFakeAppApi(page, "imt");
  await installFakeEventSource(page);
  await openResults(page, "/results?view=evaluations&semester=S6");

  const evaluation = page.locator(".results-evaluation-item").filter({ hasText: "Évaluation réseau fictive" });
  await evaluation.getByRole("link", { name: "Voir l'UE" }).click();
  await expect(page).toHaveURL(/\/results\/ue\/RES-FICTIF\?view=evaluations&semester=S6$/);
  await expect(page.getByRole("heading", { name: "Réseaux entièrement imaginaires" })).toBeVisible();
  await expect(page.getByText("Validée après rattrapage")).toBeVisible();
  await expect(page.getByText("Grade calculé depuis PASS", { exact: false })).toBeVisible();

  await page.getByLabel("Fil d'Ariane").getByRole("link", { name: "Résultats", exact: true }).click();
  await expect(page).toHaveURL(/\/results\?view=evaluations&semester=S6$/);
  await expect(page.getByRole("heading", { name: "Évaluations", exact: true })).toBeVisible();

  await page.goto("/results/ue/INCONNUE?view=ues");
  await expect(page.getByRole("heading", { name: "UE introuvable" })).toBeVisible();
  expect(state.externalRequests).toEqual([]);
});

test("le relevé reste réservé au propriétaire primaire", async ({ page }) => {
  const owner = await installFakeAppApi(page, "imt");
  await installFakeEventSource(page);
  await openResults(page);
  await expect(page.getByRole("link", { name: "Relevé académique" })).toBeVisible();
  expect(owner.externalRequests).toEqual([]);
});

test("un viewer consulte Résultats sans action propriétaire ni édition", async ({ page }) => {
  const state = await installFakeAppApi(page, "viewer");
  await installFakeEventSource(page);
  await openResults(page, "/results?view=ues");
  await expect(page.getByRole("link", { name: "Relevé académique" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /modifier|ajouter|supprimer/i })).toHaveCount(0);
  await page.goto("/ues/releve");
  await expect(page).toHaveURL("/results?view=ues");
  await expectNoSeriousA11yViolations(page);
  expect(state.externalRequests).toEqual([]);
});

test("les détails se déplient au clavier et le focus reste sur leur commande", async ({ page }) => {
  const state = await installFakeAppApi(page, "imt");
  await installFakeEventSource(page);
  await openResults(page);

  const card = page.locator(".results-ue-card").filter({ hasText: "UE-DEMO" });
  const toggle = card.locator(".results-expand-button");
  await toggle.focus();
  await toggle.press("Enter");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(card.getByText("Contrôle synthétique")).toBeVisible();
  await toggle.press("Enter");
  await expect(card.getByRole("button", { name: "Voir les évaluations" })).toBeFocused();
  expect(state.externalRequests).toEqual([]);
});

test("les états vide et erreur restent compréhensibles", async ({ page }) => {
  const state = await installFakeAppApi(page, "imt");
  state.dashboard = {
    ...structuredClone(syntheticDashboard),
    summary: {
      ...syntheticDashboard.summary,
      average: null,
      gpa: null,
      validated_credits: 0,
      note_count: 0,
      ue_count: 0,
    },
    years: [],
    semesters: [],
    ues: [],
    notes: [],
  };
  await installFakeEventSource(page);
  await openResults(page);
  await expect(page.getByRole("heading", { name: "Aucune UE" })).toBeVisible();

  state.dashboardError = true;
  await page.reload();
  await expect(page.getByRole("heading", { name: "Résultats indisponibles" })).toBeVisible();
  await expect(page.getByText("Service fictif indisponible.")).toBeVisible();
  expect(state.externalRequests).toEqual([]);
});

test("un seul dashboard alimente 100 UE et 500 évaluations", async ({ page }) => {
  const state = await installFakeAppApi(page, "imt");
  const large = structuredClone(syntheticDashboard);
  large.ues = Array.from({ length: 100 }, (_, ueIndex) => ({
    ...syntheticDashboard.ues[0]!,
    code: `UE-FICTIVE-${String(ueIndex).padStart(3, "0")}`,
    title: `UE synthétique ${ueIndex}`,
    note_count: 5,
  }));
  large.notes = large.ues.flatMap((ue, ueIndex) =>
    Array.from({ length: 5 }, (_, noteIndex) => ({
      ...syntheticDashboard.notes[0]!,
      id: `note-synthetique-${ueIndex}-${noteIndex}`,
      ue_code: ue.code,
      label: `Évaluation synthétique ${noteIndex}`,
    })),
  );
  large.summary = {
    ...large.summary,
    note_count: 500,
    ue_count: 100,
  };
  state.dashboard = large;
  await installFakeEventSource(page);
  await openResults(page, "/results?view=evaluations");

  await expect(page.locator(".results-evaluation-item")).toHaveCount(500);
  expect(state.dashboardRequests).toBe(1);
  expect(state.externalRequests).toEqual([]);
});

const responsiveCases = [
  { width: 320, height: 780, theme: "light" as const },
  { width: 360, height: 800, theme: "dark" as const },
  { width: 375, height: 812, theme: "light" as const },
  { width: 390, height: 844, theme: "dark" as const },
  { width: 430, height: 932, theme: "light" as const },
  { width: 768, height: 1024, theme: "dark" as const },
  { width: 1024, height: 768, theme: "light" as const },
  { width: 1440, height: 900, theme: "dark" as const },
];

for (const viewport of responsiveCases) {
  test(`Résultats reste lisible à ${viewport.width} × ${viewport.height} en thème ${viewport.theme}`, async ({
    page,
  }) => {
    const state = await installFakeAppApi(page, "imt");
    await installFakeEventSource(page);
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.emulateMedia({ colorScheme: viewport.theme, reducedMotion: "reduce" });
    await openResults(page);

    await expect(page.locator("html")).toHaveAttribute("data-theme", viewport.theme);
    const layoutWidths = await page.evaluate(() => ({
      body: document.body.scrollWidth,
      document: document.documentElement.scrollWidth,
      viewport: window.innerWidth,
    }));
    expect(layoutWidths.document).toBeLessThanOrEqual(layoutWidths.viewport);
    expect(layoutWidths.body).toBeLessThanOrEqual(layoutWidths.viewport);
    const targets = page.locator(".results-tabs button, .results-ue-card > footer a, .results-ue-card > footer button");
    for (let index = 0; index < (await targets.count()); index += 1) {
      const box = await targets.nth(index).boundingBox();
      expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
      expect(box?.x ?? -1).toBeGreaterThanOrEqual(0);
      expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(viewport.width + 0.5);
    }

    const filters = page.locator(".results-filters");
    if (viewport.width <= 760) {
      await expect(filters).not.toHaveAttribute("open", "");
      await filters.locator("summary").click();
      await expect(filters).toHaveAttribute("open", "");
      await filters.locator("summary").click();
    } else {
      await expect(filters).toHaveAttribute("open", "");
    }
    await expectNoSeriousA11yViolations(page);
    const screenshot = await page.screenshot({ fullPage: true, animations: "disabled" });
    expect(screenshot.byteLength).toBeGreaterThan(1_000);
    expect(state.externalRequests).toEqual([]);
  });
}
