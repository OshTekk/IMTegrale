import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { installFakeAppApi, installFakeEventSource } from "./app-fixtures";

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
] as const;

interface TouchTargetMeasurement {
  height: number;
  name: string;
  width: number;
}

async function visibleTouchTargets(root: Locator): Promise<TouchTargetMeasurement[]> {
  return root.evaluateAll((elements) =>
    elements.flatMap((element) => {
      const node = element as HTMLElement;
      const rect = node.getBoundingClientRect();
      const style = window.getComputedStyle(node);
      if (
        rect.width === 0 ||
        rect.height === 0 ||
        style.display === "none" ||
        style.visibility === "hidden" ||
        node.getAttribute("aria-hidden") === "true"
      ) {
        return [];
      }
      return [
        {
          height: Number(rect.height.toFixed(2)),
          name:
            node.getAttribute("aria-label") ??
            node.getAttribute("title") ??
            node.textContent?.trim().replace(/\s+/g, " ").slice(0, 80) ??
            node.tagName,
          width: Number(rect.width.toFixed(2)),
        },
      ];
    }),
  );
}

async function expectTargetsAtLeast44(targets: Locator, context: string) {
  const measurements = await visibleTouchTargets(targets);
  for (const target of measurements) {
    expect(target.height, `${context}: hauteur de « ${target.name} »`).toBeGreaterThanOrEqual(43.5);
    expect(target.width, `${context}: largeur de « ${target.name} »`).toBeGreaterThanOrEqual(43.5);
  }
  return measurements;
}

async function pageMetrics(page: Page) {
  return page.evaluate(() => {
    const navigation = document.querySelector<HTMLElement>(".mobile-nav");
    const workspace = document.querySelector<HTMLElement>(".workspace");
    const footer = document.querySelector<HTMLElement>(".product-footer");
    const footerContent = footer?.querySelector<HTMLElement>(".source-notice");
    const navRect = navigation?.getBoundingClientRect();
    const footerContentRect = footerContent?.getBoundingClientRect();
    return {
      bodyWidth: document.body.scrollWidth,
      documentWidth: document.documentElement.scrollWidth,
      footerContentBottom: footerContentRect ? Number(footerContentRect.bottom.toFixed(2)) : null,
      footerPaddingBottom: footer ? Number.parseFloat(window.getComputedStyle(footer).paddingBottom) : null,
      navHeight: navRect ? Number(navRect.height.toFixed(2)) : null,
      navTop: navRect ? Number(navRect.top.toFixed(2)) : null,
      pageHeight: document.documentElement.scrollHeight,
      viewportHeight: window.innerHeight,
      viewportWidth: window.innerWidth,
      workspacePaddingBottom: workspace ? Number.parseFloat(window.getComputedStyle(workspace).paddingBottom) : null,
    };
  });
}

async function expectNoGlobalOverflow(page: Page) {
  const metrics = await pageMetrics(page);
  expect(metrics.documentWidth).toBeLessThanOrEqual(metrics.viewportWidth);
  expect(metrics.bodyWidth).toBeLessThanOrEqual(metrics.viewportWidth);
  return metrics;
}

async function reducedMotionState(page: Page) {
  return page.evaluate(() => {
    const calendar = document.querySelector(".personal-calendar");
    if (!calendar) return null;
    const line = document.createElement("span");
    line.className = "calendar-loading-line";
    calendar.append(line);
    const lineStyle = window.getComputedStyle(line, "::after");
    const modal = document.querySelector<HTMLElement>(".modal");
    const modalStyle = modal ? window.getComputedStyle(modal) : null;
    const result = {
      lineAnimation: lineStyle.animationName,
      lineDuration: lineStyle.animationDuration,
      modalAnimation: modalStyle?.animationName ?? null,
      modalDuration: modalStyle?.animationDuration ?? null,
      modalTransition: modalStyle?.transitionDuration ?? null,
    };
    line.remove();
    return result;
  });
}

async function expectNoSeriousA11yViolations(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(
    results.violations
      .filter((violation) => violation.impact === "serious" || violation.impact === "critical")
      .map((violation) => ({ id: violation.id, targets: violation.nodes.map((node) => node.target) })),
  ).toEqual([]);
}

async function openSyntheticCalendar(page: Page, width = 390, height = 844) {
  const state = await installFakeAppApi(page, "imt");
  await installFakeEventSource(page);
  await page.setViewportSize({ width, height });
  await page.goto("/calendar");
  await expect(page.getByRole("heading", { name: "Agenda", level: 1 })).toBeVisible();
  return state;
}

test("le shell et l’Agenda respectent toute la matrice responsive", async ({ page }) => {
  const state = await installFakeAppApi(page, "imt");
  await installFakeEventSource(page);
  state.session.learning = {
    available: true,
    audience_label: "Promotion fictive 2028",
    level_label: "2A fictive",
    reverify_required: false,
    catalog_version: "catalogue-fictif-mobile",
  };

  const inventory = [];
  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    await page.emulateMedia({
      colorScheme: viewport.width % 2 === 0 ? "dark" : "light",
      reducedMotion: "reduce",
    });
    await page.goto("/calendar");
    await expect(page.getByRole("heading", { name: "Agenda", level: 1 })).toBeVisible();
    await expect(page.locator(".fullcalendar-frame")).toBeVisible();

    const expectedView = viewport.width <= 700 ? "Liste" : "Mois";
    await expect(page.getByRole("button", { name: `Afficher la vue ${expectedView}` })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    const calendarTargets = await expectTargetsAtLeast44(
      page.locator(
        ".calendar-section-tabs button, .calendar-period-controls button, .calendar-view-switch button, .calendar-sync-band > button, .calendar-data-controls > button, .fullcalendar-frame .fc-list-event button",
      ),
      `Agenda ${viewport.width}×${viewport.height}`,
    );
    let shellTargets: TouchTargetMeasurement[] = [];
    if (viewport.width <= 980) {
      shellTargets = await expectTargetsAtLeast44(
        page.locator(".topbar button, .mobile-nav > a, .mobile-nav > button"),
        `Shell ${viewport.width}×${viewport.height}`,
      );
      const navItems = page.locator(".mobile-nav > a, .mobile-nav > button");
      await expect(navItems).toHaveCount(5);
    }

    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    const metrics = await expectNoGlobalOverflow(page);
    if (viewport.width <= 980) {
      const reservedBottomSpace = (metrics.workspacePaddingBottom ?? 0) + (metrics.footerPaddingBottom ?? 0);
      expect(reservedBottomSpace).toBeGreaterThanOrEqual((metrics.navHeight ?? 0) + 15);
      expect(metrics.footerContentBottom ?? Number.POSITIVE_INFINITY).toBeLessThanOrEqual((metrics.navTop ?? 0) - 8);
    }

    const motion = await reducedMotionState(page);
    expect(motion).toMatchObject({
      lineAnimation: "none",
      lineDuration: "0s",
    });
    inventory.push({
      ...viewport,
      calendarTargetCount: calendarTargets.length,
      pageHeight: metrics.pageHeight,
      shellTargetCount: shellTargets.length,
      view: expectedView,
    });

    if ([320, 768, 1440].includes(viewport.width)) {
      await expectNoSeriousA11yViolations(page);
    }
  }

  console.log(`MOBILE_FINAL_INVENTORY ${JSON.stringify(inventory)}`);
  expect(state.externalRequests).toEqual([]);
});

test("la navigation mobile reflète exactement le niveau d’accès", async ({ browser }) => {
  const variants = [
    { mode: "imt" as const, labels: ["Accueil", "Résultats", "Agenda", "Simuler"], more: true },
    { mode: "passkey" as const, labels: ["Accueil", "Résultats", "Agenda", "Simuler"], more: true },
    { mode: "token" as const, labels: ["Accueil", "Résultats", "Réglages"], more: true },
    { mode: "viewer" as const, labels: ["Accueil", "Résultats", "Réglages"], more: false },
  ];

  for (const variant of variants) {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page = await context.newPage();
    const state = await installFakeAppApi(page, variant.mode);
    await installFakeEventSource(page);
    if (variant.mode === "imt" || variant.mode === "passkey") {
      state.session.learning = {
        available: true,
        audience_label: "Promotion fictive 2028",
        level_label: "2A fictive",
        reverify_required: false,
        catalog_version: "catalogue-fictif-mobile",
      };
    }

    await page.goto("/");
    await expect(page.locator(".mobile-nav")).toBeVisible();
    await expect(page.locator(".mobile-nav > a")).toHaveCount(variant.labels.length);
    expect(await page.locator(".mobile-nav > a span").allTextContents()).toEqual(variant.labels);
    await expect(page.getByRole("button", { name: "Ouvrir les autres pages" })).toHaveCount(variant.more ? 1 : 0);
    await expect(page.locator(".mobile-nav > a, .mobile-nav > button")).toHaveCount(
      variant.labels.length + (variant.more ? 1 : 0),
    );
    await expect(page.locator(".mobile-nav a").first()).toHaveAttribute("aria-current", "page");
    if (variant.mode === "token" || variant.mode === "viewer") {
      await expect(page.locator(".mobile-nav")).not.toContainText("Agenda");
      await expect(page.locator(".mobile-nav")).not.toContainText("Simuler");
    }
    await expectNoSeriousA11yViolations(page);
    expect(state.externalRequests).toEqual([]);
    await context.close();
  }
});

test("Plus piège puis restitue le focus et reste actif sur une page secondaire", async ({ page }) => {
  const state = await installFakeAppApi(page, "imt");
  await installFakeEventSource(page);
  state.session.learning = {
    available: true,
    audience_label: "Promotion fictive 2028",
    level_label: "2A fictive",
    reverify_required: false,
    catalog_version: "catalogue-fictif-mobile",
  };
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");

  const more = page.getByRole("button", { name: "Ouvrir les autres pages" });
  await more.focus();
  await more.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Autres pages" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("link").first()).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.getByRole("button", { name: "Fermer" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(more).toBeFocused();

  await more.click();
  await dialog.getByRole("link", { name: /Paramètres/ }).click();
  await expect(page).toHaveURL("/settings");
  await expect(dialog).toHaveCount(0);
  await expect(page.locator("#main-content")).toBeFocused();
  await expect(more).toHaveAttribute("aria-current", "page");
  await page.goBack();
  await expect(page.locator(".mobile-nav > a").first()).toHaveAttribute("aria-current", "page");
  expect(state.externalRequests).toEqual([]);
});

test("la topbar compacte garde la reconnexion et le nom complet accessibles", async ({ page }) => {
  const state = await installFakeAppApi(page, "imt");
  await installFakeEventSource(page);
  const longName = "Profil fictif avec un nom volontairement très long";
  state.session.account = {
    ...(state.session.account as Record<string, unknown>),
    display_name: longName,
  };
  const dashboardAccount = state.dashboard.account as Record<string, unknown>;
  dashboardAccount.manual_sync = {
    ...(dashboardAccount.manual_sync as Record<string, unknown>),
    state: "reauth_required",
    can_start: false,
  };
  await page.setViewportSize({ width: 320, height: 780 });
  await page.goto("/calendar");

  await expect(page.getByRole("button", { name: `Ouvrir le profil de ${longName}` })).toBeVisible();
  const reconnect = page.getByRole("button", {
    name: "Reconnexion IMT requise avant la prochaine synchronisation",
  });
  await expect(reconnect).toBeVisible();
  await expect(reconnect).toHaveClass(/is-reauth-required/);
  await expectNoGlobalOverflow(page);
  await reconnect.click();
  await expect(page.getByRole("dialog", { name: "Renouveler la session IMT" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(reconnect).toBeFocused();
  expect(state.externalRequests).toEqual([]);
});

test("l’Agenda mobile conserve le contexte, le focus et les informations disponibles", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-07-28T12:00:00+02:00"));
  const state = await openSyntheticCalendar(page);
  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

  const listView = page.getByRole("button", { name: "Afficher la vue Liste" });
  await expect(listView).toHaveAttribute("aria-pressed", "true");
  const weekView = page.getByRole("button", { name: "Afficher la vue Semaine" });
  await weekView.focus();
  await weekView.press("Enter");
  await expect(weekView).toHaveAttribute("aria-pressed", "true");
  await listView.focus();
  await listView.press("Space");
  await expect(listView).toHaveAttribute("aria-pressed", "true");
  const currentPeriod = await page.locator(".calendar-toolbar h2").textContent();
  await page.getByRole("button", { name: "Période suivante" }).click();
  await expect(page.locator(".calendar-toolbar h2")).not.toHaveText(currentPeriod ?? "");
  await page.getByRole("button", { name: "Aujourd'hui" }).click();
  await expect(page.locator(".calendar-toolbar h2")).toHaveText(currentPeriod ?? "");

  const event = page.getByRole("button", { name: /Atelier de conception entièrement fictif/ });
  await event.click();
  const eventDialog = page.getByRole("dialog", { name: /Atelier de conception entièrement fictif/ });
  await expect(eventDialog).toContainText("Salle fictive A-101");
  await page.keyboard.press("Escape");
  await expect(event).toBeFocused();

  const noLocationEvent = page.getByRole("button", { name: /Projet collectif de démonstration/ });
  await noLocationEvent.click();
  await expect(page.getByRole("dialog", { name: "Projet collectif de démonstration" })).toContainText(
    "Non indiqué dans INPASS",
  );
  await page.getByRole("button", { name: "Fermer" }).click();
  await expect(noLocationEvent).toBeFocused();

  const manage = page.getByRole("button", { name: "Gérer" });
  await manage.click();
  await expect(page.getByLabel("Lien iCalendar INPASS")).toBeFocused();
  await page.getByLabel("Lien iCalendar INPASS").fill("https://calendar.example.invalid/export?secret=fictif");
  await page.getByRole("button", { name: "Annuler" }).click();
  await expect(manage).toBeFocused();

  const remove = page.getByRole("button", { name: "Supprimer mon agenda" });
  await remove.click();
  await expect(page.getByRole("dialog", { name: "Supprimer l'agenda ?" })).toBeVisible();
  await page.getByRole("button", { name: "Annuler" }).click();
  await expect(remove).toBeFocused();

  await page.getByRole("button", { name: "Formation FIP" }).click();
  await expect(page.locator(".training-mobile-overview")).toBeVisible();
  await expect(page.locator(".training-timeline-chart")).toBeHidden();
  await expect(page.getByText("Projet international fictif")).toBeVisible();
  await expectNoGlobalOverflow(page);
  await expectNoSeriousA11yViolations(page);
  expect(state.calendarConnectRequests).toEqual([]);
  expect(state.calendarDisconnects).toBe(0);
  expect(state.externalRequests).toEqual([]);
});

test("les états non configuré, vide et erreur de l’Agenda restent actionnables", async ({ page }) => {
  const state = await installFakeAppApi(page, "imt");
  await installFakeEventSource(page);
  state.calendarStatus = {
    ...state.calendarStatus,
    configured: false,
    event_count: 0,
  };
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto("/calendar");
  await expect(page.getByRole("heading", { name: "Ton agenda n'est pas encore connecté" })).toBeVisible();
  const add = page.getByRole("button", { name: "Ajouter mon lien" });
  await add.click();
  await expect(page.getByLabel("Lien iCalendar INPASS")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(add).toBeFocused();

  state.calendarStatus = {
    ...state.calendarStatus,
    configured: true,
    event_count: 4,
  };
  state.calendarEvents = [];
  await page.reload();
  await expect(page.getByText("Aucun cours importé sur cette période.")).toBeVisible();

  state.calendarEventsError = true;
  await page.reload();
  await expect(page.getByText("Cours fictifs indisponibles.")).toBeVisible();
  await expectNoGlobalOverflow(page);
  expect(state.externalRequests).toEqual([]);
});

test("les événements restent des cibles tactiles sur tablette tactile", async ({ browser }) => {
  const context = await browser.newContext({
    viewport: { width: 768, height: 1024 },
    hasTouch: true,
  });
  const page = await context.newPage();
  const state = await installFakeAppApi(page, "imt");
  await installFakeEventSource(page);
  await page.goto("/calendar");
  await page.getByRole("button", { name: "Afficher la vue Semaine" }).click();
  await expectTargetsAtLeast44(page.locator(".fullcalendar-frame .fc-timegrid-event"), "Événements Agenda tactiles");
  await expectNoGlobalOverflow(page);
  expect(state.externalRequests).toEqual([]);
  await context.close();
});

test("les commandes communes restent tactiles sur les écrans standards", async ({ browser }) => {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    hasTouch: true,
  });
  const page = await context.newPage();
  const state = await installFakeAppApi(page, "imt");
  await installFakeEventSource(page);
  const routes = ["/", "/results", "/sharing", "/settings"];

  for (const viewport of [
    { width: 390, height: 844 },
    { width: 768, height: 1024 },
  ]) {
    await page.setViewportSize(viewport);
    for (const route of routes) {
      await page.goto(route);
      await expect(page.locator("#main-content")).toBeVisible();
      await expectTargetsAtLeast44(
        page.locator(
          ".page-content :is(button, a).icon-button, .page-content :is(button, a).primary-button, .page-content :is(button, a).secondary-button, .page-content :is(button, a).danger-button, .page-content :is(button, a).text-button, .page-content .segmented button, .page-content .segmented-control button",
        ),
        `${route} ${viewport.width}×${viewport.height}`,
      );
      await expectNoGlobalOverflow(page);
    }
  }

  expect(state.externalRequests).toEqual([]);
  await context.close();
});

test("la connexion reste lisible et tactile sur le plus petit écran", async ({ browser }) => {
  const context = await browser.newContext({
    viewport: { width: 320, height: 780 },
    hasTouch: true,
  });
  const page = await context.newPage();
  const state = await installFakeAppApi(page, "anonymous");
  await installFakeEventSource(page);
  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Connexion avec ton compte IMT" })).toBeVisible();
  await expectTargetsAtLeast44(
    page.locator(
      ".login-topbar button, .login-panel button, .login-panel [role='tab'], .login-public-links a, .login-form .field-icon",
    ),
    "Connexion 320×780",
  );
  await expectNoGlobalOverflow(page);
  await expectNoSeriousA11yViolations(page);
  expect(state.externalRequests).toEqual([]);
  await context.close();
});
