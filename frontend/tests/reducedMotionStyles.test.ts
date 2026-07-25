import { readdirSync, readFileSync } from "node:fs";
import { extname, join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const sourceRoot = new URL("../src", import.meta.url);

function cssFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return cssFiles(path);
    return extname(entry.name) === ".css" ? [path] : [];
  });
}

function mediaBodies(source: string): string[] {
  const marker = "@media (prefers-reduced-motion: reduce)";
  const bodies: string[] = [];
  let offset = 0;
  while (offset < source.length) {
    const mediaStart = source.indexOf(marker, offset);
    if (mediaStart === -1) break;
    const bodyStart = source.indexOf("{", mediaStart + marker.length);
    if (bodyStart === -1) break;
    let depth = 1;
    let cursor = bodyStart + 1;
    while (cursor < source.length && depth > 0) {
      if (source[cursor] === "{") depth += 1;
      if (source[cursor] === "}") depth -= 1;
      cursor += 1;
    }
    expect(depth, "Bloc reduced-motion CSS non fermé").toBe(0);
    bodies.push(source.slice(bodyStart + 1, cursor - 1));
    offset = cursor;
  }
  return bodies;
}

function durationInMilliseconds(value: string): number {
  const match = value.trim().match(/^(\d+(?:\.\d+)?)(ms|s)(?:\s*!important)?$/);
  if (!match) return Number.POSITIVE_INFINITY;
  const amount = Number(match[1]);
  return match[2] === "s" ? amount * 1000 : amount;
}

describe("contrat global de mouvement réduit", () => {
  const files = cssFiles(sourceRoot.pathname);

  it("inspecte toutes les feuilles CSS, y compris celles des routes chargées paresseusement", () => {
    expect(files.map((file) => relative(sourceRoot.pathname, file))).toEqual(
      expect.arrayContaining([
        "learning.css",
        "pages/gpa-simulations/gpaSimulations.css",
        "pages/note-simulations/noteSimulations.css",
        "pages/results/results.css",
        "styles.css",
      ]),
    );
  });

  it("ne contient aucune animation ou transition longue dans un bloc reduced-motion", () => {
    const violations: string[] = [];
    for (const file of files) {
      const source = readFileSync(file, "utf8");
      for (const body of mediaBodies(source)) {
        for (const match of body.matchAll(/(?:animation|transition)-duration\s*:\s*([^;]+);/g)) {
          if (durationInMilliseconds(match[1]!) > 0.01) {
            violations.push(`${relative(sourceRoot.pathname, file)}: ${match[0]}`);
          }
        }
        for (const match of body.matchAll(/(?:animation|transition)\s*:\s*([^;]+);/g)) {
          if (!/^none(?:\s*!important)?$/.test(match[1]!.trim())) {
            violations.push(`${relative(sourceRoot.pathname, file)}: ${match[0]}`);
          }
        }
      }
    }
    expect(violations).toEqual([]);
  });

  it("termine la feuille globale par une neutralisation impérative de tout mouvement", () => {
    const source = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
    const finalBlock = mediaBodies(source).at(-1);
    expect(finalBlock).toContain("animation: none !important");
    expect(finalBlock).toContain("transition: none !important");
    expect(finalBlock).toContain("scroll-behavior: auto !important");
    expect(source.trim().endsWith("}")).toBe(true);
  });

  it("interdit à une feuille chargée plus tard de réactiver un mouvement avec important", () => {
    const violations = files.flatMap((file) => {
      const source = readFileSync(file, "utf8");
      return Array.from(source.matchAll(/((?:animation|transition)(?:-[a-z-]+)?)\s*:\s*([^;]*!important)\s*;/g))
        .filter((match) => {
          const property = match[1]!;
          const value = match[2]!.replace(/\s*!important\s*$/, "").trim();
          if (property === "animation" || property === "transition") return value !== "none";
          if (property.endsWith("-duration") || property.endsWith("-delay")) {
            return durationInMilliseconds(value) > 0.01;
          }
          if (property === "animation-iteration-count") return value !== "1";
          return true;
        })
        .map((match) => `${relative(sourceRoot.pathname, file)}: ${match[0]}`);
    });
    expect(violations).toEqual([]);
  });
});
