import axe, { type Result } from "axe-core";
import { expect } from "vitest";

function describeViolation(violation: Result): string {
  const targets = violation.nodes.flatMap((node) => node.target.map((target) => String(target))).join(", ");
  return `${violation.id} (${violation.impact ?? "unknown"}): ${targets}`;
}

export async function expectNoSeriousLearningViolations(root: Element | Document = document): Promise<void> {
  const result = await axe.run(root, {
    runOnly: {
      type: "tag",
      values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"],
    },
    rules: {
      // jsdom has no layout/canvas implementation. Contrast remains a browser-level check.
      "color-contrast": { enabled: false },
    },
  });
  const violations = result.violations.filter(
    (violation) => violation.impact === "serious" || violation.impact === "critical",
  );

  expect(violations.map(describeViolation)).toEqual([]);
}
