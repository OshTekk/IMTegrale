import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { installFakeAppApi, installFakeEventSource } from "./app-fixtures";

type ToastKind = "success" | "error" | "warning" | "info";
type ToastPhase = "first-visible" | "quarter" | "middle" | "end" | "stable";

type ToastSample = {
  axe: Array<{ id: string; impact: string | null; targets: unknown[] }>;
  count: number;
  duration: number;
  focusContrast: number;
  focusOutlineWidth: number;
  iconContrast: number;
  kind: ToastKind;
  opacity: number;
  phase: ToastPhase;
  stackSize: number;
  textContrast: number;
  visible: boolean;
};

const phases: ToastPhase[] = ["first-visible", "quarter", "middle", "end", "stable"];
const kinds: ToastKind[] = ["success", "error", "warning", "info"];

async function installToastHarness(page: Page) {
  const app = await installFakeAppApi(page, "imt");
  await installFakeEventSource(page);
  await page.goto("/");
  await expect(page.getByRole("region", { name: "Notifications" })).toBeAttached();
  return app;
}

async function mountToasts(page: Page, kind: ToastKind, count: number) {
  await page.evaluate(
    ({ toastKind, toastCount }) => {
      const region = document.querySelector<HTMLElement>(".toast-region");
      if (!region) throw new Error("toast region missing");
      region.replaceChildren();

      for (let index = 0; index < toastCount; index += 1) {
        const toast = document.createElement("div");
        toast.className = `toast toast-${toastKind}`;
        toast.setAttribute("role", toastKind === "error" ? "alert" : "status");
        toast.dataset.toastKind = toastKind;

        const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        icon.setAttribute("width", "18");
        icon.setAttribute("height", "18");
        icon.setAttribute("viewBox", "0 0 24 24");
        icon.setAttribute("fill", "none");
        icon.setAttribute("stroke", "currentColor");
        icon.setAttribute("aria-hidden", "true");
        icon.dataset.toastIcon = "true";
        const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        circle.setAttribute("cx", "12");
        circle.setAttribute("cy", "12");
        circle.setAttribute("r", "9");
        icon.append(circle);

        const message = document.createElement("span");
        message.textContent = `[FICTIF] Notification ${toastKind} ${index + 1}`;

        const close = document.createElement("button");
        close.type = "button";
        close.setAttribute("aria-label", "Masquer");
        close.textContent = "×";
        close.addEventListener("click", () => toast.remove());

        toast.append(icon, message, close);
        region.append(toast);
      }
    },
    { toastKind: kind, toastCount: count },
  );
  await expect(page.locator(".toast-region > .toast")).toHaveCount(count);
}

async function seekToastAnimation(page: Page, phase: ToastPhase) {
  return page.evaluate(async (requestedPhase) => {
    const animations = document.querySelectorAll<HTMLElement>(".toast")[0]?.getAnimations() ?? [];
    await Promise.all(animations.map((animation) => animation.ready));
    const duration = Number(animations[0]?.effect?.getTiming().duration ?? 0);
    const times: Record<ToastPhase, number> = {
      "first-visible": Math.min(1000 / 60, duration),
      quarter: duration * 0.25,
      middle: duration * 0.5,
      end: duration,
      stable: duration,
    };
    for (const toast of document.querySelectorAll<HTMLElement>(".toast")) {
      for (const animation of toast.getAnimations()) {
        animation.pause();
        animation.currentTime = times[requestedPhase];
      }
    }
    return duration;
  }, phase);
}

async function measureToast(page: Page) {
  return page.evaluate(() => {
    type Rgb = { b: number; g: number; r: number };
    const parseColor = (value: string): Rgb => {
      const channels = value
        .match(/[\d.]+/g)
        ?.slice(0, 3)
        .map(Number);
      if (!channels || channels.length !== 3) throw new Error(`unsupported color: ${value}`);
      return { r: channels[0]!, g: channels[1]!, b: channels[2]! };
    };
    const blend = (foreground: Rgb, background: Rgb, alpha: number): Rgb => ({
      r: foreground.r * alpha + background.r * (1 - alpha),
      g: foreground.g * alpha + background.g * (1 - alpha),
      b: foreground.b * alpha + background.b * (1 - alpha),
    });
    const luminance = (color: Rgb): number => {
      const channel = (value: number) => {
        const normalized = value / 255;
        return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * channel(color.r) + 0.7152 * channel(color.g) + 0.0722 * channel(color.b);
    };
    const contrast = (first: Rgb, second: Rgb): number => {
      const lighter = Math.max(luminance(first), luminance(second));
      const darker = Math.min(luminance(first), luminance(second));
      return (lighter + 0.05) / (darker + 0.05);
    };

    const toast = document.querySelector<HTMLElement>(".toast");
    const text = toast?.querySelector<HTMLElement>("span");
    const icon = toast?.querySelector<SVGElement>("[data-toast-icon]");
    const close = toast?.querySelector<HTMLButtonElement>("button");
    if (!toast || !text || !icon || !close) throw new Error("toast structure missing");

    close.focus();
    const toastStyle = getComputedStyle(toast);
    const textStyle = getComputedStyle(text);
    const iconStyle = getComputedStyle(icon);
    const closeStyle = getComputedStyle(close);
    const bodyStyle = getComputedStyle(document.body);
    const opacity = Number(toastStyle.opacity);
    const backdrop = parseColor(bodyStyle.backgroundColor);
    const background = blend(parseColor(toastStyle.backgroundColor), backdrop, opacity);
    const effective = (color: string) => blend(parseColor(color), backdrop, opacity);
    const box = toast.getBoundingClientRect();

    return {
      focusContrast: contrast(effective(closeStyle.outlineColor), background),
      focusOutlineWidth: Number.parseFloat(closeStyle.outlineWidth),
      iconContrast: contrast(effective(iconStyle.color), background),
      opacity,
      textContrast: contrast(effective(textStyle.color), background),
      visible:
        box.width > 0 &&
        box.height > 0 &&
        toastStyle.display !== "none" &&
        toastStyle.visibility === "visible" &&
        opacity > 0,
    };
  });
}

async function collectMatrix(page: Page): Promise<ToastSample[]> {
  const samples: ToastSample[] = [];
  for (const kind of kinds) {
    for (const count of [1, 2]) {
      for (const phase of phases) {
        await mountToasts(page, kind, count);
        const duration = await seekToastAnimation(page, phase);
        const measured = await measureToast(page);
        const axe = await new AxeBuilder({ page }).include(".toast-region").withTags(["wcag2a", "wcag2aa"]).analyze();
        samples.push({
          ...measured,
          axe: axe.violations
            .filter((violation) => violation.impact === "serious" || violation.impact === "critical")
            .map((violation) => ({
              id: violation.id,
              impact: violation.impact ?? null,
              targets: violation.nodes.map((node) => node.target),
            })),
          count,
          duration,
          kind,
          phase,
          stackSize: count,
        });
      }

      const firstToast = page.locator(".toast-region > .toast").first();
      const removed = await firstToast.evaluate((toast) => {
        const close = toast.querySelector<HTMLButtonElement>("button");
        if (!close) throw new Error("close button missing");
        close.click();
        return { connected: toast.isConnected, focusableDescendants: toast.querySelectorAll("button").length };
      });
      expect(removed).toEqual({ connected: false, focusableDescendants: 1 });
      await expect(page.locator(".toast-region > .toast")).toHaveCount(count - 1);
    }
  }
  return samples;
}

for (const presentation of [
  { label: "clair motion normale", colorScheme: "light", reducedMotion: "no-preference" },
  { label: "sombre motion normale", colorScheme: "dark", reducedMotion: "no-preference" },
  { label: "clair reduced-motion", colorScheme: "light", reducedMotion: "reduce" },
  { label: "sombre reduced-motion", colorScheme: "dark", reducedMotion: "reduce" },
] as const) {
  test(`chaque frame visible du toast conserve son contraste en ${presentation.label}`, async ({ page }) => {
    await page.emulateMedia({
      colorScheme: presentation.colorScheme,
      reducedMotion: presentation.reducedMotion,
    });
    const app = await installToastHarness(page);
    await expect(page.locator("html")).toHaveAttribute("data-theme", presentation.colorScheme);

    const samples = await collectMatrix(page);
    const failures = samples
      .filter(
        (sample) =>
          sample.axe.length > 0 ||
          !sample.visible ||
          sample.opacity !== 1 ||
          sample.textContrast < 4.5 ||
          sample.iconContrast < 3 ||
          sample.focusOutlineWidth < 3 ||
          sample.focusContrast < 3,
      )
      .map((sample) => ({
        axe: sample.axe,
        count: sample.count,
        focusContrast: sample.focusContrast,
        focusOutlineWidth: sample.focusOutlineWidth,
        iconContrast: sample.iconContrast,
        kind: sample.kind,
        opacity: sample.opacity,
        phase: sample.phase,
        textContrast: sample.textContrast,
        visible: sample.visible,
      }));
    expect(failures).toEqual([]);

    const roles = await page.locator(".toast-region > .toast").evaluateAll((toasts) =>
      toasts.map((toast) => ({
        kind: (toast as HTMLElement).dataset.toastKind,
        role: toast.getAttribute("role"),
      })),
    );
    expect(roles.every(({ kind, role }) => role === (kind === "error" ? "alert" : "status"))).toBe(true);
    expect(app.externalRequests).toEqual([]);
  });
}
