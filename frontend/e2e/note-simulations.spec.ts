import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { installFakeAppApi, installFakeEventSource } from "./app-fixtures";
import { installFakeNoteSimulationsApi } from "./note-simulation-fixtures";

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
  const notes = await installFakeNoteSimulationsApi(page, app);
  await installFakeEventSource(page);
  return { app, notes };
}

async function openFirstVisibleUe(page: Page) {
  const trigger = page.locator(".note-ue-trigger").first();
  await trigger.click();
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
  return trigger;
}

async function editFirstAssessment(
  page: Page,
  { score, coefficient, resit }: { score: string; coefficient: string; resit: boolean },
) {
  const assessment = page.locator(".note-assessment-card").first();
  await assessment.click();
  const dialog = page.getByRole("dialog", { name: /Modifier \[FICTIF\] Évaluation/ });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("spinbutton", { name: "Note sur 20" }).fill(score);
  await dialog.getByRole("spinbutton", { name: "Coefficient" }).fill(coefficient);
  const resitSwitch = dialog.getByRole("switch", { name: /Rattrapage/ });
  if ((await resitSwitch.isChecked()) !== resit) await dialog.locator(".note-editor-resit").click();
  await dialog.getByRole("button", { name: "Appliquer" }).click();
  await expect(dialog).toBeHidden();
  return assessment;
}

test("l’éditeur mobile progresse scénario, semestre, UE puis évaluation sans perdre le contexte", async ({ page }) => {
  const { app, notes } = await installSimulationApp(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/simulations/notes");

  await expect(page.getByRole("heading", { name: "Simulation de notes" })).toBeAttached();
  await expect(page.locator(".note-workbench-ue")).toHaveCount(20);
  await expect(page.locator(".note-ue-panel")).toHaveCount(0);
  await expect(page.locator(".note-editor-modal")).toHaveCount(0);

  const initialMetrics = await page.evaluate(() => ({
    focusable: Array.from(
      document.querySelectorAll<HTMLElement>(
        "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])",
      ),
    ).filter((element) => element.offsetParent !== null).length,
    height: document.documentElement.scrollHeight,
    width: document.documentElement.scrollWidth,
    viewport: document.documentElement.clientWidth,
  }));
  expect(initialMetrics.focusable).toBeLessThan(60);
  expect(initialMetrics.height).toBeLessThan(5_000);
  expect(initialMetrics.width).toBe(initialMetrics.viewport);

  await page.getByLabel("Semestre").selectOption("S6");
  await expect(page.locator(".note-workbench-ue")).toHaveCount(5);
  const first = await openFirstVisibleUe(page);
  await expect(page.locator(".note-ue-panel")).toHaveCount(1);
  await expect(page.locator(".note-assessment-card")).toHaveCount(3);
  await expect(page.locator(".note-assessment-card input")).toHaveCount(0);

  const second = page.locator(".note-ue-trigger").nth(1);
  await second.click();
  await expect(first).toHaveAttribute("aria-expanded", "false");
  await expect(second).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator(".note-ue-panel")).toHaveCount(1);

  const averageBefore = await page.locator(".note-summary-primary strong").textContent();
  const editedCard = await editFirstAssessment(page, {
    score: "18",
    coefficient: "4",
    resit: true,
  });
  await expect(editedCard).toBeFocused();
  await expect(editedCard).toContainText("18");
  await expect(editedCard).toContainText("Rattrapage");
  await expect(page.locator(".note-summary-primary strong")).not.toHaveText(averageBefore ?? "");
  await expect(page.locator(".simulation-save-state")).toContainText("Enregistré");
  expect(notes.saveRequests.length).toBeGreaterThanOrEqual(1);

  await second.click();
  await expect(second).toHaveAttribute("aria-expanded", "false");
  await second.click();
  await expect(editedCard).toContainText("18");

  const assessmentCount = await page.locator(".note-assessment-card").count();
  await page.getByRole("button", { name: "Ajouter une évaluation" }).click();
  let addDialog = page.getByRole("dialog", { name: "Ajouter une évaluation" });
  await addDialog.getByRole("textbox", { name: /^Nom de l’évaluation/ }).fill("[FICTIF] Hypothèse annulée");
  await addDialog.getByRole("button", { name: "Annuler" }).click();
  await expect(page.locator(".note-assessment-card")).toHaveCount(assessmentCount);

  await page.getByRole("button", { name: "Ajouter une évaluation" }).click();
  addDialog = page.getByRole("dialog", { name: "Ajouter une évaluation" });
  await addDialog.getByRole("textbox", { name: /^Nom de l’évaluation/ }).fill("[FICTIF] Hypothèse ajoutée");
  await addDialog.getByRole("spinbutton", { name: "Note sur 20" }).fill("16");
  await addDialog.getByRole("button", { name: "Ajouter" }).click();
  const added = page.getByRole("button", {
    name: /Modifier \[FICTIF\] Hypothèse ajoutée/,
  });
  await expect(added).toBeFocused();
  await added.click();
  const editAdded = page.getByRole("dialog", {
    name: /Modifier \[FICTIF\] Hypothèse ajoutée/,
  });
  await editAdded.getByRole("button", { name: "Supprimer cette évaluation" }).click();
  await editAdded.getByRole("button", { name: "Confirmer la suppression" }).click();
  await expect(added).toHaveCount(0);

  await page.locator("#note-add-ue").click();
  const ueDialog = page.getByRole("dialog", { name: "Ajouter une UE" });
  await ueDialog.getByRole("textbox", { name: "Intitulé de l’UE" }).fill("[FICTIF] UE mobile ajoutée");
  await ueDialog.getByRole("textbox", { name: "Code UE" }).fill("MOBFIC");
  await ueDialog.getByRole("spinbutton", { name: "Crédits ECTS" }).fill("2");
  await ueDialog.getByRole("button", { name: "Ajouter l’UE" }).click();
  const newUe = page.getByRole("button", {
    name: /Modifier \[FICTIF\] UE mobile ajoutée/,
  });
  await expect(newUe).toBeFocused();
  await expect(newUe).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator(".note-ue-panel")).toHaveCount(1);

  await page.getByRole("button", { name: "Comparer" }).click();
  const comparison = page.getByRole("dialog", {
    name: "Comparer deux simulations de notes",
  });
  await expect(comparison).toBeVisible();
  await expect(comparison.locator(".simulation-comparison-score")).toBeVisible();
  await comparison.getByRole("button", { name: "Fermer" }).last().click();
  await expect(page.getByRole("button", { name: "Comparer" })).toBeFocused();

  expect(notes.csrfHeaders.every((value) => value === "csrf-app-e2e-fictif")).toBe(true);
  expect(app.externalRequests).toEqual([]);
  await expectNoSeriousA11yViolations(page);
});

test("les erreurs et conflits gardent la saisie locale et les options de résolution", async ({ page }) => {
  const { notes } = await installSimulationApp(page);
  await page.setViewportSize({ width: 1023, height: 768 });
  await page.goto("/simulations/notes");
  await expect(page.locator(".note-workbench-ue")).toHaveCount(20);

  await openFirstVisibleUe(page);
  notes.failNextSave = true;
  await editFirstAssessment(page, {
    score: "19",
    coefficient: "2",
    resit: false,
  });
  await expect(page.getByText("L’enregistrement n’a pas abouti", { exact: true })).toBeVisible();
  await expect(page.locator(".note-assessment-card").first()).toContainText("19");
  await page.getByRole("button", { name: "Réessayer" }).click();
  await expect(page.locator(".simulation-save-state")).toContainText("Enregistré");

  notes.conflictNextSave = true;
  await editFirstAssessment(page, {
    score: "17",
    coefficient: "3",
    resit: false,
  });
  await expect(page.getByText("Une version plus récente existe", { exact: true })).toBeVisible();
  await expect(page.locator(".note-assessment-card").first()).toContainText("17");
  await page.getByRole("button", { name: "Recharger" }).click();
  await expect(page.getByText("Une version plus récente existe", { exact: true })).toBeHidden();
  await expect(page.locator(".simulation-save-state")).toContainText("Enregistré");

  const scenarioSelect = page.getByLabel("Scénario actif");
  await scenarioSelect.selectOption("scenario-notes-fictif-conflit");
  await expect(page.locator(".note-ue-panel")).toHaveCount(1);
  const ueConflict = page.locator(".note-workbench-conflict").first();
  await expect(ueConflict).toContainText("Conflit sur l’UE");
  await ueConflict.getByRole("button", { name: "Garder la simulation" }).click();
  await expect(page.locator(".note-workbench-conflict").first()).toContainText("Conflit sur l’évaluation");
  await page.locator(".note-workbench-conflict").first().getByRole("button", { name: "Prendre la source" }).click();
  await expect(page.locator(".note-workbench-conflict")).toHaveCount(0);
});

test("le scénario actif, la duplication et le reset restent cohérents après rechargement", async ({ page }) => {
  const { notes } = await installSimulationApp(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/simulations/notes");

  const select = page.getByLabel("Scénario actif");
  await select.selectOption("scenario-notes-fictif-conflit");
  await page.reload();
  await expect(page.getByLabel("Scénario actif")).toHaveValue("scenario-notes-fictif-conflit");

  await page.getByRole("button", { name: "Actions sur la simulation" }).click();
  await page.getByRole("button", { name: "Dupliquer" }).click();
  await expect.poll(() => notes.scenarios.length).toBe(3);
  await expect(page.getByLabel("Scénario actif")).toHaveValue(/scenario-notes-fictif-copie-/);
  await expect(page.locator(".note-ue-panel")).toHaveCount(0);

  await page.getByRole("button", { name: "Actions sur la simulation" }).click();
  await page.getByRole("button", { name: "Réinitialiser" }).click();
  const resetDialog = page.getByRole("dialog", {
    name: "Réinitialiser les hypothèses ?",
  });
  await resetDialog.getByRole("button", { name: "Réinitialiser" }).click();
  await expect(page.locator(".simulation-save-state")).toContainText("Enregistré");
  await expect(page.locator(".note-ue-panel")).toHaveCount(0);
});

const responsiveCases = [
  { width: 320, height: 780 },
  { width: 360, height: 800 },
  { width: 375, height: 812 },
  { width: 390, height: 844 },
  { width: 430, height: 932 },
  { width: 768, height: 1024 },
  { width: 1024, height: 768 },
  { width: 1280, height: 800 },
  { width: 1440, height: 900 },
];

for (const viewport of responsiveCases) {
  test(`simulation de notes sans débordement à ${viewport.width} × ${viewport.height}`, async ({ page }) => {
    const { app } = await installSimulationApp(page);
    await page.setViewportSize(viewport);
    await page.emulateMedia({
      colorScheme: viewport.width % 2 ? "dark" : "light",
      reducedMotion: viewport.width === 375 ? "reduce" : "no-preference",
    });
    await page.goto("/simulations/notes");
    await expect(page.locator(".note-workbench-ue")).toHaveCount(20);

    const dimensions = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      pageHeight: document.documentElement.scrollHeight,
      focusable: Array.from(
        document.querySelectorAll<HTMLElement>(
          ".note-workbench button:not([disabled]), .note-workbench input:not([disabled]), .note-workbench select:not([disabled]), .note-workbench-scenarios button:not([disabled]), .note-workbench-scenarios select:not([disabled])",
        ),
      ).filter((element) => element.offsetParent !== null).length,
    }));
    expect(dimensions.scrollWidth).toBe(dimensions.clientWidth);
    expect(dimensions.focusable).toBeLessThan(40);
    if (viewport.width <= 430) expect(dimensions.pageHeight).toBeLessThan(5_000);

    if (viewport.width <= 1023) {
      const undersized = await page
        .locator(".note-workbench button:visible, .note-workbench-scenarios button:visible")
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

    await openFirstVisibleUe(page);
    await page.locator(".note-assessment-card").first().click();
    const dialog = page.getByRole("dialog", {
      name: /Modifier \[FICTIF\] Évaluation/,
    });
    await expect(dialog).toBeVisible();
    if (viewport.width <= 430) {
      await page.setViewportSize({ width: viewport.width, height: 520 });
      const actionsBox = await dialog.locator(".note-editor-actions").boundingBox();
      expect(actionsBox).not.toBeNull();
      expect(actionsBox!.y + actionsBox!.height).toBeLessThanOrEqual(520);
    }
    await dialog.getByRole("button", { name: "Annuler" }).click();
    await expectNoSeriousA11yViolations(page);
    expect(app.externalRequests).toEqual([]);
  });
}

test("la vue sombre et reduced-motion conserve contrastes, focus et comparaison mobile", async ({ page }) => {
  await installSimulationApp(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  await page.goto("/simulations/notes");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

  const first = page.locator(".note-ue-trigger").first();
  await first.focus();
  await page.keyboard.press("Enter");
  await expect(first).toHaveAttribute("aria-expanded", "true");
  const assessment = page.locator(".note-assessment-card").first();
  await assessment.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: /Modifier \[FICTIF\] Évaluation/ })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(assessment).toBeFocused();

  await page.getByRole("button", { name: "Comparer" }).click();
  const comparison = page.getByRole("dialog", {
    name: "Comparer deux simulations de notes",
  });
  const width = await comparison.evaluate((element) => element.scrollWidth - element.clientWidth);
  expect(width).toBe(0);
  await expectNoSeriousA11yViolations(page);
});
