import AxeBuilder from "@axe-core/playwright";
import {
  expect,
  test,
  type Browser,
  type BrowserContext,
  type Locator,
  type Page,
  type Response,
} from "@playwright/test";
import { installFakeAppApi, installFakeEventSource, type FakeAppState } from "./app-fixtures";
import {
  configurePrivateComparisonSession,
  createPrivateComparisonE2eState,
  installFakePrivateComparisonApi,
  seedInvitation,
  seedRelation,
  type ComparisonActor,
  type PrivateComparisonE2eState,
} from "./private-comparison-fixtures";

test.use({ trace: "off", screenshot: "off", video: "off" });

interface ActorBrowser {
  app: FakeAppState;
  context: BrowserContext;
  page: Page;
}

async function actorBrowser(
  browser: Browser,
  shared: PrivateComparisonE2eState,
  actor: ComparisonActor,
): Promise<ActorBrowser> {
  const context = await browser.newContext({ serviceWorkers: "block" });
  const page = await context.newPage();
  const mode = actor === "viewer" ? "viewer" : actor === "token" ? "token" : actor === "camille" ? "passkey" : "imt";
  const app = await installFakeAppApi(page, mode);
  configurePrivateComparisonSession(app, shared, actor);
  await installFakePrivateComparisonApi(page, shared, actor);
  await installFakeEventSource(page);
  return { app, context, page };
}

async function expectNoSeriousA11yViolations(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(
    results.violations
      .filter((violation) => violation.impact === "serious" || violation.impact === "critical")
      .map((violation) => ({ id: violation.id, targets: violation.nodes.map((node) => node.target) })),
  ).toEqual([]);
}

async function checkPrivateHeaders(responses: Response[]) {
  expect(responses.length).toBeGreaterThan(0);
  for (const response of responses) {
    expect(await response.headerValue("cache-control")).toBe("private, no-store");
    expect(await response.headerValue("x-content-type-options")).toBe("nosniff");
  }
}

async function acceptConsent(page: Page | Locator) {
  const checkboxes = await page.getByRole("checkbox").all();
  expect(checkboxes).toHaveLength(3);
  for (const checkbox of checkboxes) await checkbox.check();
}

test("le parcours bilatéral one-shot accepte, compare puis révoque sans fuite du token", async ({ browser }) => {
  const shared = createPrivateComparisonE2eState();
  const alice = await actorBrowser(browser, shared, "alice");
  const camille = await actorBrowser(browser, shared, "camille");
  const networkUrls: string[] = [];
  const privateResponses: Response[] = [];
  for (const page of [alice.page, camille.page]) {
    page.on("request", (request) => networkUrls.push(request.url()));
    page.on("response", (response) => {
      if (new URL(response.url()).pathname.startsWith("/api/v1/private-comparisons")) {
        privateResponses.push(response);
      }
    });
  }

  await alice.page.setViewportSize({ width: 390, height: 844 });
  await alice.page.goto("/comparisons");
  await expect(alice.page.getByRole("heading", { name: "Comparaisons privées" })).toBeVisible();
  await alice.page.getByRole("button", { name: "Créer une invitation" }).first().click();
  const creation = alice.page.getByRole("dialog", { name: "Créer une invitation" });
  await expect(creation.getByLabel("Durée de la comparaison après acceptation")).toHaveValue("30");
  await acceptConsent(creation);
  await creation.getByRole("button", { name: "Créer le lien" }).click();
  const oneShot = alice.page.getByRole("dialog", { name: "Lien d’invitation créé" });
  const invitationLink = await oneShot.getByLabel("Lien d’invitation").inputValue();
  expect(new URL(invitationLink).hash).toMatch(/^#invite=pcinv1_[A-Za-z0-9_-]{43}$/);
  const token = new URLSearchParams(new URL(invitationLink).hash.slice(1)).get("invite");
  expect(token).toBeTruthy();
  await oneShot.getByRole("button", { name: "Fermer" }).last().click();
  await expect(alice.page.locator("body")).not.toContainText(token!);

  await camille.page.goto(invitationLink);
  await expect(camille.page).toHaveURL(/\/comparisons\/accept$/);
  await expect(camille.page.getByRole("heading", { name: /Alice Exemple te propose une comparaison/ })).toBeVisible();
  await expect(camille.page.locator("body")).not.toContainText("13,4 / 20");
  await acceptConsent(camille.page);
  await camille.page.getByRole("button", { name: "Accepter la comparaison" }).click();
  await expect(camille.page).toHaveURL(/\/comparisons\/pc_[A-Za-z0-9_-]{24}$/);
  await expect(camille.page.getByRole("heading", { name: "Comparaison avec Alice Exemple" })).toBeVisible();
  const relationPath = new URL(camille.page.url()).pathname;

  await alice.page.goto(relationPath);
  await expect(alice.page.getByRole("heading", { name: /Comparaison avec Camille Exemple/ })).toBeVisible();
  for (const page of [alice.page, camille.page]) {
    await expect(page.getByText("UE-COMMUNE-FICTIVE")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Résumé général" })).toBeVisible();
    await expect(page.locator("body")).not.toContainText("Projet fictif");
    await expect(page.locator("body")).not.toContainText(/gagnant|perdant|meilleur|moins bon/i);
    await expectNoSeriousA11yViolations(page);
  }

  await alice.page.getByRole("button", { name: "Mettre fin à la comparaison" }).click();
  await alice.page.getByRole("button", { name: "Mettre fin", exact: true }).click();
  await expect(alice.page).toHaveURL(/\/comparisons$/);
  await camille.page.reload();
  await expect(camille.page.getByText("Cette comparaison n’est plus disponible")).toBeVisible();

  expect(shared.createCalls).toBe(1);
  expect(shared.previewCalls).toBe(1);
  expect(shared.acceptCalls).toBe(1);
  expect(shared.revokeRelationCalls).toBe(1);
  expect(shared.secretInRequestUrl).toBe(false);
  expect(shared.tokenBodyPosts).toBe(2);
  expect(networkUrls.some((url) => url.includes(token!))).toBe(false);
  expect(alice.app.externalRequests).toEqual([]);
  expect(camille.app.externalRequests).toEqual([]);
  await checkPrivateHeaders(privateResponses);
  await alice.context.close();
  await camille.context.close();
});

test("le refus et toutes les invitations inéligibles restent génériques", async ({ browser }) => {
  const shared = createPrivateComparisonE2eState();
  const declineToken = seedInvitation(shared, "alice");
  const camille = await actorBrowser(browser, shared, "camille");
  await camille.page.goto(`/comparisons/accept#invite=${declineToken}`);
  await expect(camille.page.getByRole("heading", { name: /Alice Exemple te propose une comparaison/ })).toBeVisible();
  await camille.page.getByRole("button", { name: "Refuser" }).click();
  await camille.page.getByRole("button", { name: "Refuser l’invitation" }).click();
  await expect(camille.page).toHaveURL(/\/comparisons$/);
  expect(shared.relations).toHaveLength(0);
  expect(shared.declineCalls).toBe(1);

  for (const status of ["expired", "revoked"] as const) {
    const token = seedInvitation(shared, "alice", status);
    await camille.page.goto(`/comparisons/accept#invite=${token}`);
    await expect(camille.page.getByText("Cette invitation n’est plus disponible")).toBeVisible();
    await expect(camille.page.locator("body")).not.toContainText(/expirée|révoquée par/i);
  }
  await camille.context.close();

  for (const actor of ["other-program", "other-promotion"] as const) {
    const token = seedInvitation(shared, "alice");
    const ineligible = await actorBrowser(browser, shared, actor);
    await ineligible.page.goto(`/comparisons/accept#invite=${token}`);
    await expect(ineligible.page.getByText("Cette invitation n’est plus disponible")).toBeVisible();
    await expect(ineligible.page.locator("body")).not.toContainText(/cursus|promotion précise|FIP-FICTIF/i);
    await ineligible.context.close();
  }
  expect(shared.secretInRequestUrl).toBe(false);
});

test("une double acceptation et un accès direct tiers ne créent ni relation supplémentaire ni IDOR", async ({
  browser,
}) => {
  const shared = createPrivateComparisonE2eState();
  const token = seedInvitation(shared, "alice");
  const camille = await actorBrowser(browser, shared, "camille");
  const outsider = await actorBrowser(browser, shared, "outsider");
  for (const page of [camille.page, outsider.page]) {
    await page.goto(`/comparisons/accept#invite=${token}`);
    await expect(page.getByRole("heading", { name: /Alice Exemple te propose une comparaison/ })).toBeVisible();
    await acceptConsent(page);
  }
  await camille.page.getByRole("button", { name: "Accepter la comparaison" }).click();
  await expect(camille.page).toHaveURL(/\/comparisons\/pc_/);
  const relationPath = new URL(camille.page.url()).pathname;
  await outsider.page.getByRole("button", { name: "Accepter la comparaison" }).click();
  await expect(outsider.page.getByText("Cette invitation n’est plus disponible")).toBeVisible();
  expect(shared.relations).toHaveLength(1);

  await outsider.page.goto(relationPath);
  await expect(outsider.page.getByText("Cette comparaison n’est plus disponible")).toBeVisible();
  await expect(outsider.page.locator("body")).not.toContainText("UE-COMMUNE-FICTIVE");
  await camille.context.close();
  await outsider.context.close();
});

test("la capacité masque entièrement Comparaisons aux sessions et flags non autorisés", async ({ browser }) => {
  const shared = createPrivateComparisonE2eState();
  for (const actor of ["viewer", "token"] as const) {
    const blocked = await actorBrowser(browser, shared, actor);
    const before = shared.privateRequestUrls.length;
    await blocked.page.goto("/comparisons");
    await expect(blocked.page).toHaveURL(/\/$/);
    await expect(blocked.page.getByRole("link", { name: "Comparaisons" })).toHaveCount(0);
    expect(shared.privateRequestUrls).toHaveLength(before);
    await blocked.context.close();
  }

  const disabledState = createPrivateComparisonE2eState();
  disabledState.enabled = false;
  const disabled = await actorBrowser(browser, disabledState, "alice");
  await disabled.page.goto("/comparisons");
  await expect(disabled.page).toHaveURL(/\/$/);
  expect(disabledState.privateRequestUrls).toHaveLength(0);
  await disabled.context.close();
});

const viewports = [
  { width: 320, height: 780 },
  { width: 360, height: 800 },
  { width: 375, height: 812 },
  { width: 390, height: 844 },
  { width: 430, height: 932 },
  { width: 667, height: 375 },
  { width: 768, height: 1024 },
  { width: 820, height: 1180 },
  { width: 1024, height: 768 },
  { width: 1280, height: 800 },
  { width: 1440, height: 900 },
];

test("le détail et la modale one-shot restent lisibles sur toute la matrice responsive", async ({ browser }) => {
  const shared = createPrivateComparisonE2eState();
  const relationId = seedRelation(shared);
  const alice = await actorBrowser(browser, shared, "alice");
  await alice.page.emulateMedia({ reducedMotion: "reduce", colorScheme: "dark" });

  for (const viewport of viewports) {
    await alice.page.setViewportSize(viewport);
    await alice.page.goto(`/comparisons/${relationId}`);
    await expect(alice.page.getByRole("heading", { name: /Comparaison avec Camille Exemple/ })).toBeVisible();
    const dimensions = await alice.page.evaluate(() => ({
      body: document.body.scrollWidth,
      document: document.documentElement.scrollWidth,
      viewport: window.innerWidth,
    }));
    expect(dimensions.body).toBeLessThanOrEqual(dimensions.viewport);
    expect(dimensions.document).toBeLessThanOrEqual(dimensions.viewport);
    const action = await alice.page.getByRole("button", { name: "Mettre fin à la comparaison" }).boundingBox();
    expect(action?.height ?? 0).toBeGreaterThanOrEqual(44);
    if ([320, 768, 1440].includes(viewport.width)) await expectNoSeriousA11yViolations(alice.page);
  }

  await alice.page.setViewportSize({ width: 320, height: 780 });
  await alice.page.goto("/comparisons");
  await alice.page.getByRole("button", { name: "Créer une invitation" }).first().click();
  const modal = alice.page.getByRole("dialog", { name: "Créer une invitation" });
  await expect(modal).toBeVisible();
  const modalDimensions = await alice.page.evaluate(() => ({
    body: document.body.scrollWidth,
    document: document.documentElement.scrollWidth,
    viewport: window.innerWidth,
  }));
  expect(modalDimensions.body).toBeLessThanOrEqual(modalDimensions.viewport);
  expect(modalDimensions.document).toBeLessThanOrEqual(modalDimensions.viewport);
  const transitions = await modal.evaluate((element) => {
    const style = getComputedStyle(element);
    return { animation: style.animationDuration, transition: style.transitionDuration };
  });
  expect(transitions.animation).toMatch(/^(0s|0\.001s)$/);
  expect(transitions.transition).toMatch(/^(0s|0\.001s)(, (0s|0\.001s))*$/);
  await expectNoSeriousA11yViolations(alice.page);
  await alice.context.close();
});
