import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const learningCss = readFileSync(new URL("../src/learning.css", import.meta.url), "utf8");
const appCss = ["../src/styles/core.css", "../src/styles.css"]
  .map((path) => readFileSync(new URL(path, import.meta.url), "utf8"))
  .join("\n");

describe("Parcours responsive and theme contracts", () => {
  it("keeps explicit layouts for the required compact and tablet widths", () => {
    expect(appCss).toContain("min-width: 320px");
    expect(appCss).toContain("@media (max-width: 375px)");
    expect(learningCss).toContain("@media (max-width: 768px)");
    expect(learningCss).toContain("@media (max-width: 1024px)");
    expect(appCss).toContain("max-width: 1500px");
  });

  it("inherits dark mode and explicitly disables decorative motion", () => {
    expect(appCss).toContain('html[data-theme="dark"]');
    expect(learningCss).toContain("@media (prefers-reduced-motion: reduce)");
    expect(learningCss).toContain("animation-duration: 0.01ms !important");
  });

  it("uses opaque high-contrast focus indicators in both themes", () => {
    expect(appCss).toContain("--focus-ring: #315f8f");
    expect(appCss).toContain("--focus-ring: #63c8bb");
  });
});
