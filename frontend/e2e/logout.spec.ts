import AxeBuilder from "@axe-core/playwright";
import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import {
  installFakeAppApi,
  SYNTHETIC_LOGOUT_ACCOUNT_B_ID,
  type FakeAppState,
  type LogoutFixtureMode,
} from "./app-fixtures";

interface TwoTabFixture {
  first: Page;
  second: Page;
  state: FakeAppState;
}

async function installSignalCounter(page: Page): Promise<void> {
  await page.evaluate(() => {
    const trackedWindow = window as Window & { __syntheticLogoutSignals?: number };
    trackedWindow.__syntheticLogoutSignals = 0;
    window.addEventListener("storage", (event) => {
      if (event.key === "botnote:session-change") trackedWindow.__syntheticLogoutSignals! += 1;
    });
  });
}

async function signalCount(page: Page): Promise<number> {
  return page.evaluate(() => (window as Window & { __syntheticLogoutSignals?: number }).__syntheticLogoutSignals ?? 0);
}

async function openAuthenticatedTabs(page: Page, context: BrowserContext): Promise<TwoTabFixture> {
  const state = await installFakeAppApi(page, "imt");
  const second = await context.newPage();
  await installFakeAppApi(second, "imt", state);
  await Promise.all([
    page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" }),
    second.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" }),
  ]);
  await Promise.all([page.goto("/"), second.goto("/")]);
  await Promise.all([
    expect(page.getByRole("heading", { name: "Vue d'ensemble" })).toBeVisible(),
    expect(second.getByRole("heading", { name: "Vue d'ensemble" })).toBeVisible(),
  ]);
  await Promise.all([installSignalCounter(page), installSignalCounter(second)]);
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

test("un logout normal ne publie le succès qu'après confirmation et une seule fois", async ({ page, context }) => {
  const { first, second, state } = await openAuthenticatedTabs(page, context);
  state.logoutDelayMs = 350;

  await requestLogout(first, "success", state);

  await expect(first.getByRole("menuitem", { name: "Se déconnecter" })).toBeDisabled();
  await expect(second.getByRole("heading", { name: "Vue d'ensemble" })).toBeVisible();
  expect(await signalCount(second)).toBe(0);

  await Promise.all([expectLogin(first), expectLogin(second)]);
  expect(state.logoutRequests).toBe(1);
  expect(state.session.authenticated).toBe(false);
  expect(await signalCount(second)).toBe(1);
  expect(state.externalRequests).toEqual([]);
});

test("un échec avant commit conserve les deux onglets, les données et le focus", async ({ page, context }) => {
  const { first, second, state } = await openAuthenticatedTabs(page, context);

  await requestLogout(first, "fail-before-server", state);

  await expect(first.getByRole("dialog", { name: "Session active" })).toBeVisible();
  await expect(first.getByRole("button", { name: "Réessayer" })).toBeFocused();
  await expect(first.getByRole("heading", { name: "Vue d'ensemble" })).toBeVisible();
  await expect(second.getByRole("heading", { name: "Vue d'ensemble" })).toBeVisible();
  expect(state.logoutRequests).toBe(1);
  expect(state.session.authenticated).toBe(true);
  expect(await signalCount(second)).toBe(0);
  expect(await first.evaluate(() => localStorage.getItem("botnote:session-change"))).toBeNull();
  await expectNoSeriousA11yViolations(first);
  expect(state.externalRequests).toEqual([]);
});

test("une réponse perdue après commit est vérifiée puis propagée aux deux onglets", async ({ page, context }) => {
  const { first, second, state } = await openAuthenticatedTabs(page, context);

  await requestLogout(first, "lose-after-commit", state);

  await Promise.all([expectLogin(first), expectLogin(second)]);
  expect(state.logoutRequests).toBe(1);
  expect(state.session.authenticated).toBe(false);
  expect(await signalCount(second)).toBe(1);
  expect(state.externalRequests).toEqual([]);
});

test("un double échec reste indéterminé en mobile sombre sans faux signal", async ({ page, context }) => {
  const { first, second, state } = await openAuthenticatedTabs(page, context);
  await first.setViewportSize({ width: 375, height: 812 });
  await first.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  await first.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));

  await requestLogout(first, "indeterminate", state);

  await expect(first.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(first.getByRole("dialog", { name: "Session inconnue" })).toBeVisible();
  await expect(first.getByRole("button", { name: "Réessayer" })).toBeFocused();
  await expect(first.getByRole("link", { name: "Recharger" })).toBeVisible();
  await expect(first.getByRole("heading", { name: "Vue d'ensemble" })).toBeVisible();
  await expect(second.getByRole("heading", { name: "Vue d'ensemble" })).toBeVisible();
  expect(state.logoutRequests).toBe(1);
  expect(state.session.authenticated).toBe(true);
  expect(await signalCount(second)).toBe(0);
  await expectNoSeriousA11yViolations(first);
  expect(state.externalRequests).toEqual([]);
});

test("hors ligne, seul le rechargement demandé par l'utilisateur relit l'autorité", async ({ page, context }) => {
  const state = await installFakeAppApi(page, "imt");
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Vue d'ensemble" })).toBeVisible();
  const sessionReadsBefore = state.sessionRequests;
  await context.setOffline(true);

  await requestLogout(page, "success", state);

  await expect(page.getByRole("dialog", { name: "Session inconnue" })).toBeVisible();
  expect(state.logoutRequests).toBe(0);
  expect(state.sessionRequests).toBe(sessionReadsBefore);
  await context.setOffline(false);
  await page.getByRole("link", { name: "Recharger" }).click();
  await expect(page.getByRole("heading", { name: "Vue d'ensemble" })).toBeVisible();
  expect(state.session.authenticated).toBe(true);
  expect(state.externalRequests).toEqual([]);
});

test("deux clics synchrones coalescent sur un seul POST", async ({ page }) => {
  const state = await installFakeAppApi(page, "imt");
  state.logoutMode = "success";
  state.logoutDelayMs = 350;
  await page.goto("/");
  await page.getByRole("button", { name: /Ouvrir le profil/ }).click();
  const button = page.getByRole("menuitem", { name: "Se déconnecter" });

  await button.evaluate((element) => {
    (element as HTMLButtonElement).click();
    (element as HTMLButtonElement).click();
  });

  await expect(page.getByRole("menuitem", { name: "Se déconnecter" })).toBeDisabled();
  await expectLogin(page);
  expect(state.logoutRequests).toBe(1);
  expect(state.externalRequests).toEqual([]);
});

test("une réponse ancienne ne reprend pas l'autorité après une nouvelle session B", async ({ page }) => {
  const state = await installFakeAppApi(page, "imt");
  state.logoutMode = "stale-success";
  state.logoutDelayMs = 500;
  await page.goto("/");

  await page.getByRole("button", { name: /Ouvrir le profil/ }).click();
  await page.getByRole("menuitem", { name: "Se déconnecter" }).click();
  state.session = {
    ...state.session,
    account: {
      id: SYNTHETIC_LOGOUT_ACCOUNT_B_ID,
      display_name: "Étudiant fictif B",
      imt_username: "demo.b.fictif",
    },
  };
  await page.evaluate(() =>
    window.dispatchEvent(
      new StorageEvent("storage", { key: "botnote:session-change", newValue: "synthetic-principal-change" }),
    ),
  );

  await expect(page.getByRole("button", { name: /Ouvrir le profil de Étudiant fictif B/ })).toBeVisible();
  await page.waitForTimeout(550);
  await expect(page.getByRole("heading", { name: "Vue d'ensemble" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Ouvrir le profil de Étudiant fictif B/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Connexion avec ton compte IMT" })).toHaveCount(0);
  expect(state.session.authenticated).toBe(true);
  expect((state.session.account as { id: string }).id).toBe(SYNTHETIC_LOGOUT_ACCOUNT_B_ID);
  expect(await page.evaluate(() => localStorage.getItem("botnote:session-change"))).toBeNull();
  expect(state.externalRequests).toEqual([]);
});

test("un principal concurrent B remplace A sans être présenté comme anonyme", async ({ page, context }) => {
  const { first, second, state } = await openAuthenticatedTabs(page, context);

  await requestLogout(first, "principal-changed", state);

  await Promise.all([
    expect(first.getByRole("button", { name: /Ouvrir le profil de Étudiant fictif B/ })).toBeVisible(),
    expect(second.getByRole("button", { name: /Ouvrir le profil de Étudiant fictif B/ })).toBeVisible(),
  ]);
  await expect(first.getByRole("heading", { name: "Connexion avec ton compte IMT" })).toHaveCount(0);
  await expect(second.getByRole("heading", { name: "Connexion avec ton compte IMT" })).toHaveCount(0);
  expect((state.session.account as { id: string }).id).toBe(SYNTHETIC_LOGOUT_ACCOUNT_B_ID);
  expect(await signalCount(second)).toBe(1);
  expect(state.externalRequests).toEqual([]);
});

test("une query A tardive ne restaure pas l'interface après confirmation", async ({ page }) => {
  const state = await installFakeAppApi(page, "imt");
  state.dashboardDelayMs = 700;
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Vue d'ensemble" })).toBeVisible();

  await requestLogout(page, "success", state);

  await expectLogin(page);
  await page.waitForTimeout(750);
  await expectLogin(page);
  expect(state.dashboardRequests).toBeGreaterThanOrEqual(1);
  expect(state.externalRequests).toEqual([]);
});

test("un broadcast confirmé pendant une vérification invalide la tentative devenue ancienne", async ({
  page,
  context,
}) => {
  const { first, second, state } = await openAuthenticatedTabs(page, context);
  state.logoutMode = "fail-before-server";
  state.sessionVerificationDelayMs = 500;

  await first.getByRole("button", { name: /Ouvrir le profil/ }).click();
  await first.getByRole("menuitem", { name: "Se déconnecter" }).click();
  await expect(first.getByRole("menuitem", { name: "Se déconnecter" })).toBeDisabled();
  state.logoutMode = "success";
  await requestLogout(second, "success", state);

  await Promise.all([expectLogin(first), expectLogin(second)]);
  await expect(first.getByRole("dialog")).toHaveCount(0);
  expect(state.logoutRequests).toBe(2);
  expect(state.session.authenticated).toBe(false);
  expect(state.externalRequests).toEqual([]);
});

test("le retour BFCache ne restaure jamais le principal déconnecté", async ({ page }) => {
  const state = await installFakeAppApi(page, "imt");
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "Paramètres" })).toBeVisible();

  await requestLogout(page, "success", state);
  await expectLogin(page);
  await page.goBack();

  await expectLogin(page);
  expect(state.session.authenticated).toBe(false);
  expect(state.externalRequests).toEqual([]);
});
