import { describe, expect, it } from "vitest";
import { validateLearningMath } from "./validate-learning-math.mjs";

describe("learning math validator", () => {
  it("validates inline and block formulas in a generic fixture", () => {
    expect(
      validateLearningMath({
        blocks: [
          { type: "paragraph", inlines: [{ type: "math", latex: "q = \\alpha + 1" }] },
          { type: "math", latex: "\\sum_{k=1}^{n} k" },
        ],
      }),
    ).toBe(2);
  });

  it.each([
    "\\href{https://example.invalid}{x}",
    "\\url{javascript:alert(1)}",
    "\\htmlClass{fixture}{x}",
    "\\frac{a}{b",
  ])("rejects invalid or untrusted syntax without echoing it: %s", (latex) => {
    expect(() => validateLearningMath({ blocks: [{ type: "math", latex }] })).toThrowError(
      "Invalid learning math at $/blocks/0/latex",
    );
    try {
      validateLearningMath({ blocks: [{ type: "math", latex }] });
    } catch (error) {
      expect(String(error)).not.toContain(latex);
    }
  });
});
