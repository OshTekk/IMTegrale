import AxeBuilder from "@axe-core/playwright";
import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import { installFakeAppApi, installFakeEventSource, type FakeAppState, type LogoutFixtureMode } from "./app-fixtures";

interface TwoTabFixture {
  first: Page;
  second: Page;
  state: FakeAppState;
}

async function openAuthenticatedTabs(page: Page, context: BrowserContext): Promise<TwoTabFixture> {
  const state = await installFakeAppApi(page, "imt");
  await installFakeEventSource(page);
  const second = await context.newPage();
  await installFakeAppApi(second, "imt", state);
  await installFakeEventSource(second);
  await Promise.all([
    page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" }),
    second.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" }),
  ]);
  await Promise.all([page.goto("/"), second.goto("/")]);
  await Promise.all([
    expect(page.getByRole("heading", { name: "Vue d'ensemble" })).toBeVisible(),
    expect(second.getByRole("heading", { name: "Vue d'ensemble" })).toBeVisible(),
  ]);
  return { first: page, second, state };
}

async function requestLogout(page: Page, mode: LogoutFixtureMode, state: FakeAppState): Promise<void> {
  state.logoutMode = mode;
  await page.getByRole("button", { name: /Ouvrir le profil/ }).click();
  await page.getByRole("menuitem", { name: "Se déconnecter" }).click();
}

async function expectLogin(page: Page): Promise<void> {
  await expect(page.getByRole("heading", { name: "Connexion avec ton compte IMT" })).toBeVisible();
}

async function expectNoSeriousA11yViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).include("[role='dialog']").withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(
    results.violations
      .filter((violation) => violation.impact === "serious" || violation.impact === "critical")
      .map((violation) => ({ id: violation.id, targets: violation.nodes.map((node) => node.target) })),
  ).toEqual([]);
}

test("un logout confirmé retire les données privées dans les deux onglets", async ({ page, context }) => {
  const { first, second, state } = await openAuthenticatedTabs(page, context);

  await requestLogout(first, "success", state);

  await Promise.all([expectLogin(first), expectLogin(second)]);
  expect(state.logoutRequests).toBe(1);
  expect(state.session.authenticated).toBe(false);
  expect(await first.evaluate(() => localStorage.getItem("botnote:session-change"))).not.toBeNull();
  expect(state.externalRequests).toEqual([]);
});

test("un échec avant serveur ne diffuse rien et conserve les deux onglets authentifiés", async ({ page, context }) => {
  const { first, second, state } = await openAuthenticatedTabs(page, context);

  await requestLogout(first, "fail-before-server", state);

  await expect(first.getByRole("dialog", { name: "Déconnexion non confirmée" })).toBeVisible();
  await expect(first.getByText("La session est encore active. Réessaie dans un instant.")).toBeVisible();
  await expect(first.getByRole("heading", { name: "Vue d'ensemble" })).toBeVisible();
  await expect(second.getByRole("heading", { name: "Vue d'ensemble" })).toBeVisible();
  expect(state.logoutRequests).toBe(1);
  expect(state.session.authenticated).toBe(true);
  expect(await first.evaluate(() => localStorage.getItem("botnote:session-change"))).toBeNull();
  await expectNoSeriousA11yViolations(first);
  expect(state.externalRequests).toEqual([]);
});

test("une réponse perdue après commit est confirmée puis propagée aux deux onglets", async ({ page, context }) => {
  const { first, second, state } = await openAuthenticatedTabs(page, context);

  await requestLogout(first, "lose-after-commit", state);

  await Promise.all([expectLogin(first), expectLogin(second)]);
  expect(state.logoutRequests).toBe(1);
  expect(state.session.authenticated).toBe(false);
  expect(await first.evaluate(() => localStorage.getItem("botnote:session-change"))).not.toBeNull();
  expect(state.externalRequests).toEqual([]);
});

test("un double échec reste indéterminé sans faux signal vers l'autre onglet", async ({ page, context }) => {
  const { first, second, state } = await openAuthenticatedTabs(page, context);
  await first.setViewportSize({ width: 375, height: 812 });
  await first.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });

  await requestLogout(first, "indeterminate", state);

  await expect(first.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(first.getByRole("dialog", { name: "Impossible de confirmer la déconnexion" })).toBeVisible();
  await expect(first.getByRole("button", { name: "Réessayer" })).toBeFocused();
  await expect(first.getByRole("button", { name: "Recharger la page" })).toBeVisible();
  await expect(first.getByRole("heading", { name: "Vue d'ensemble" })).toBeVisible();
  await expect(second.getByRole("heading", { name: "Vue d'ensemble" })).toBeVisible();
  expect(state.logoutRequests).toBe(1);
  expect(state.session.authenticated).toBe(true);
  expect(await first.evaluate(() => localStorage.getItem("botnote:session-change"))).toBeNull();
  await expectNoSeriousA11yViolations(first);
  state.logoutMode = "fail-before-server";
  await first.getByRole("button", { name: "Recharger la page" }).click();
  await expect(first.getByRole("heading", { name: "Vue d'ensemble" })).toBeVisible();
  expect(await first.evaluate(() => localStorage.getItem("botnote:session-change"))).toBeNull();
  expect(state.externalRequests).toEqual([]);
});
