import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { installFakeAppApi, installFakeEventSource } from "./app-fixtures";
import { installFakeGpaSimulationsApi } from "./gpa-simulation-fixtures";

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

async function installSimulationApp(page: Page) {
  const app = await installFakeAppApi(page, "imt");
  const gpa = await installFakeGpaSimulationsApi(page, app);
  await installFakeEventSource(page);
  return { app, gpa };
}

async function openEntry(page: Page, index = 0) {
  const trigger = page.locator(".gpa-ue-trigger").nth(index);
  await trigger.click();
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
  return trigger;
}

async function compactEditor(page: Page) {
  const dialog = page.getByRole("dialog", { name: /Modifier \[FICTIF\] Unité GPA/ });
  await expect(dialog).toBeVisible();
  return dialog;
}

test("l’éditeur GPA mobile progresse scénario, semestre, UE et hypothèse sans perdre le contexte", async ({ page }) => {
  const { app, gpa } = await installSimulationApp(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/simulations/gpa");

  await expect(page.getByRole("heading", { name: "Simulation GPA" })).toBeAttached();
  await expect(page.locator(".gpa-ue-card")).toHaveCount(24);
  await expect(page.locator(".gpa-editor-form")).toHaveCount(0);
  await expect(page.locator(".gpa-ue-list input, .gpa-ue-list select")).toHaveCount(0);

  const initialMetrics = await page.evaluate(() => ({
    focusable: Array.from(
      document.querySelectorAll<HTMLElement>(
        "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])",
      ),
    ).filter((element) => element.offsetParent !== null).length,
    height: document.documentElement.scrollHeight,
    bodyWidth: document.body.scrollWidth,
    documentWidth: document.documentElement.scrollWidth,
    viewport: window.innerWidth,
  }));
  expect(initialMetrics.focusable).toBeLessThan(55);
  expect(initialMetrics.height).toBeLessThan(5_000);
  expect(initialMetrics.documentWidth).toBeLessThanOrEqual(initialMetrics.viewport);
  expect(initialMetrics.bodyWidth).toBeLessThanOrEqual(initialMetrics.viewport);

  await page.getByLabel("Semestre").selectOption("S7");
  await expect(page.locator(".gpa-ue-card")).toHaveCount(4);
  const first = await openEntry(page);
  const dialog = await compactEditor(page);
  const gpaBefore = await page.locator(".gpa-summary-primary strong").textContent();
  await dialog.getByRole("spinbutton", { name: "Crédits ECTS" }).fill("5");
  await dialog.getByRole("combobox", { name: "Grade potentiel" }).selectOption("F");
  await dialog.getByRole("button", { name: "Appliquer" }).click();
  await expect(dialog).toBeHidden();
  await expect(first).toBeFocused();
  await expect(first).toContainText("F");
  await expect(page.locator(".gpa-summary-primary strong")).not.toHaveText(gpaBefore ?? "");
  await expect(page.locator(".simulation-save-state")).toContainText("Enregistré");
  expect(gpa.saveRequests.length).toBeGreaterThanOrEqual(1);

  await first.click();
  const reopened = await compactEditor(page);
  await expect(reopened.getByRole("spinbutton", { name: "Crédits ECTS" })).toHaveValue("5");
  await expect(reopened.getByRole("combobox", { name: "Grade potentiel" })).toHaveValue("F");
  const title = reopened.getByRole("textbox", { name: "Intitulé de l’UE" });
  const originalTitle = await title.inputValue();
  await title.fill("[FICTIF] Modification annulée");
  await reopened.getByRole("button", { name: "Annuler" }).click();
  await first.click();
  const afterCancel = await compactEditor(page);
  await expect(afterCancel.getByRole("textbox", { name: "Intitulé de l’UE" })).toHaveValue(originalTitle);
  await afterCancel.getByRole("button", { name: "Annuler" }).click();

  const countBeforeAdd = await page.locator(".gpa-ue-card").count();
  await page.locator("#gpa-add-ue").click();
  const addDialog = page.getByRole("dialog", { name: "Ajouter une UE" });
  await addDialog.getByRole("textbox", { name: "Intitulé de l’UE" }).fill("[FICTIF] UE GPA mobile ajoutée");
  await addDialog.getByRole("textbox", { name: "Code UE" }).fill("MOBFIC");
  await addDialog.getByRole("spinbutton", { name: "Crédits ECTS" }).fill("2");
  await addDialog.getByRole("combobox", { name: "Grade potentiel" }).selectOption("B");
  await addDialog.getByRole("button", { name: "Ajouter l’UE" }).click();
  const added = page.getByRole("button", { name: /Modifier \[FICTIF\] UE GPA mobile ajoutée/ });
  await expect(added).toBeFocused();
  await expect(page.locator(".gpa-ue-card")).toHaveCount(countBeforeAdd + 1);
  await added.click();
  const addedEditor = page.getByRole("dialog", { name: /Modifier \[FICTIF\] UE GPA mobile ajoutée/ });
  await addedEditor.getByRole("button", { name: "Supprimer cette UE" }).click();
  await addedEditor.getByRole("button", { name: "Confirmer la suppression" }).click();
  await expect(added).toHaveCount(0);

  await page.getByRole("button", { name: "Comparer" }).click();
  const comparison = page.getByRole("dialog", { name: "Comparer deux projections GPA" });
  await expect(comparison).toBeVisible();
  expect(await comparison.evaluate((element) => element.scrollWidth - element.clientWidth)).toBe(0);
  await comparison.locator(".gpa-comparison-actions").getByRole("button", { name: "Fermer" }).click();
  await expect(page.getByRole("button", { name: "Comparer" })).toBeFocused();

  expect(gpa.csrfHeaders.every((value) => value === "csrf-app-e2e-fictif")).toBe(true);
  expect(app.externalRequests).toEqual([]);
  await expectNoSeriousA11yViolations(page);
});

test("les erreurs, conflits de version et conflits de source gardent les hypothèses locales", async ({ page }) => {
  const { gpa } = await installSimulationApp(page);
  await page.setViewportSize({ width: 768, height: 1024 });
  await page.goto("/simulations/gpa");
  await expect(page.locator(".gpa-ue-card")).toHaveCount(24);

  await openEntry(page, 2);
  let dialog = await compactEditor(page);
  gpa.failNextSave = true;
  await dialog.getByRole("combobox", { name: "Grade potentiel" }).selectOption("A");
  expect(
    await dialog.locator("input:invalid, select:invalid").evaluateAll((fields) =>
      fields.map((field) => ({
        name: field.getAttribute("aria-label") ?? field.closest("label")?.textContent?.trim(),
        message: (field as HTMLInputElement).validationMessage,
      })),
    ),
  ).toEqual([]);
  await dialog.getByRole("button", { name: "Appliquer" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByText("L’enregistrement n’a pas abouti", { exact: true })).toBeVisible();
  await expect(page.locator(".gpa-ue-trigger").nth(2)).toContainText("A");
  await page.getByRole("button", { name: "Réessayer" }).click();
  await expect(page.locator(".simulation-save-state")).toContainText("Enregistré");

  await openEntry(page, 2);
  dialog = await compactEditor(page);
  gpa.conflictNextSave = true;
  await dialog.getByRole("combobox", { name: "Grade potentiel" }).selectOption("D");
  await dialog.getByRole("button", { name: "Appliquer" }).click();
  await expect(page.getByText("Une version plus récente existe", { exact: true })).toBeVisible();
  await expect(page.locator(".gpa-ue-trigger").nth(2)).toContainText("D");
  await page.getByRole("button", { name: "Recharger" }).click();
  await expect(page.getByText("Une version plus récente existe", { exact: true })).toBeHidden();
  await expect(page.locator(".simulation-save-state")).toContainText("Enregistré");

  const conflictCard = page.locator(".gpa-ue-card.has-conflict").first();
  await expect(conflictCard).toContainText("Conflit à résoudre");
  await conflictCard.locator(".gpa-ue-trigger").click();
  const conflictDialog = page.getByRole("dialog", { name: /Modifier \[FICTIF\] Unité GPA/ });
  await conflictDialog.getByRole("button", { name: "Utiliser la source" }).click();
  await expect(conflictDialog.getByText("La source officielle a changé")).toBeHidden();
  await conflictDialog.getByRole("button", { name: "Annuler" }).click();
});

test("le scénario actif, la duplication, le reset et le rebase restent cohérents", async ({ page }) => {
  const { gpa } = await installSimulationApp(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/simulations/gpa");

  const select = page.getByLabel("Scénario actif");
  await select.selectOption("scenario-gpa-fictif-alternatif");
  await page.reload();
  await expect(page.getByLabel("Scénario actif")).toHaveValue("scenario-gpa-fictif-alternatif");

  await page.getByRole("button", { name: "Actions sur la simulation" }).click();
  await page.getByRole("menuitem", { name: "Dupliquer" }).click();
  await expect.poll(() => gpa.scenarios.length).toBe(3);
  await expect(page.getByLabel("Scénario actif")).toHaveValue(/scenario-gpa-fictif-copie-/);
  await expect(page.locator(".gpa-editor-form")).toHaveCount(0);

  await page.getByRole("button", { name: "Actions sur la simulation" }).click();
  await page.getByRole("menuitem", { name: "Réinitialiser" }).click();
  const reset = page.getByRole("dialog", { name: "Réinitialiser les hypothèses ?" });
  await reset.getByRole("button", { name: "Réinitialiser" }).click();
  await expect(page.locator(".simulation-save-state")).toContainText("Enregistré");
  await expect(page.locator(".gpa-editor-form")).toHaveCount(0);

  await page.getByLabel("Scénario actif").selectOption("scenario-gpa-fictif-principal");
  const rebase = page.getByRole("button", { name: "Actualiser la base" });
  await expect(rebase).toBeVisible();
  await rebase.click();
  await expect(rebase).toBeHidden();
});

const responsiveCases = [
  { width: 320, height: 780 },
  { width: 360, height: 800 },
  { width: 375, height: 812 },
  { width: 390, height: 844 },
  { width: 430, height: 932 },
  { width: 768, height: 1024 },
  { width: 820, height: 1180 },
  { width: 1024, height: 768 },
  { width: 1280, height: 800 },
  { width: 1440, height: 900 },
];

for (const viewport of responsiveCases) {
  test(`simulation GPA sans débordement à ${viewport.width} × ${viewport.height}`, async ({ page }) => {
    const { app } = await installSimulationApp(page);
    await page.setViewportSize(viewport);
    await page.emulateMedia({
      colorScheme: viewport.width % 2 ? "dark" : "light",
      reducedMotion: viewport.width === 375 ? "reduce" : "no-preference",
    });
    await page.goto("/simulations/gpa");
    await expect(page.locator(".gpa-ue-card")).toHaveCount(24);

    const dimensions = await page.evaluate(() => ({
      bodyWidth: document.body.scrollWidth,
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
      pageHeight: document.documentElement.scrollHeight,
      focusable: Array.from(
        document.querySelectorAll<HTMLElement>(
          ".gpa-workbench button:not([disabled]), .gpa-workbench input:not([disabled]), .gpa-workbench select:not([disabled]), .gpa-scenarios button:not([disabled]), .gpa-scenarios select:not([disabled])",
        ),
      ).filter((element) => element.offsetParent !== null).length,
      mountedForms: document.querySelectorAll(".gpa-editor-form").length,
      layout: document.querySelector(".gpa-simulations-page")?.getAttribute("data-layout"),
      containerWidth: document.querySelector(".gpa-simulations-page")?.getBoundingClientRect().width ?? 0,
    }));
    expect(dimensions.documentWidth).toBeLessThanOrEqual(dimensions.viewportWidth);
    expect(dimensions.bodyWidth).toBeLessThanOrEqual(dimensions.viewportWidth);
    expect(dimensions.focusable).toBeLessThan(55);
    expect(dimensions.mountedForms).toBe(0);
    expect(dimensions.layout).toBe(dimensions.containerWidth >= 920 ? "wide" : "compact");
    if (viewport.width <= 430) expect(dimensions.pageHeight).toBeLessThan(5_000);

    if (viewport.width <= 1024) {
      const undersized = await page
        .locator(".gpa-workbench button:visible, .gpa-scenarios button:visible")
        .evaluateAll((buttons) =>
          buttons
            .map((button) => {
              const box = button.getBoundingClientRect();
              return {
                label: button.getAttribute("aria-label") ?? button.textContent?.trim() ?? "",
                width: box.width,
                height: box.height,
              };
            })
            .filter((button) => button.width < 44 || button.height < 44),
        );
      expect(undersized).toEqual([]);
    }

    const trigger = await openEntry(page);
    if (dimensions.layout === "compact") {
      const dialog = await compactEditor(page);
      if (viewport.width <= 430) {
        await page.setViewportSize({ width: viewport.width, height: 520 });
        const actionsBox = await dialog.locator(".gpa-editor-actions").boundingBox();
        expect(actionsBox).not.toBeNull();
        expect(actionsBox!.y + actionsBox!.height).toBeLessThanOrEqual(520);
      }
      await dialog.getByRole("button", { name: "Annuler" }).click();
      await expect(trigger).toBeFocused();
    } else {
      await expect(page.locator(".gpa-inline-editor")).toBeVisible();
      await expect(page.locator(".gpa-editor-form")).toHaveCount(1);
      await page.getByRole("button", { name: "Fermer l’éditeur" }).click();
    }
    await expectNoSeriousA11yViolations(page);
    expect(app.externalRequests).toEqual([]);
  });
}

test("la vue sombre et reduced-motion conserve clavier, focus et comparaison mobile", async ({ page }) => {
  await installSimulationApp(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  await page.goto("/simulations/gpa");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

  const first = page.locator(".gpa-ue-trigger").first();
  await first.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: /Modifier \[FICTIF\] Unité GPA/ })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(first).toBeFocused();

  await page.getByRole("button", { name: "Actions sur la simulation" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("menu")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Actions sur la simulation" })).toBeFocused();

  await page.getByRole("button", { name: "Comparer" }).click();
  const comparison = page.getByRole("dialog", { name: "Comparer deux projections GPA" });
  expect(await comparison.evaluate((element) => element.scrollWidth - element.clientWidth)).toBe(0);
  await expectNoSeriousA11yViolations(page);
});
