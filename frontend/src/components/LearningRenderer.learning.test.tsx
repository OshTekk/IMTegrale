// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";
import type { LearningBlockNode } from "../types";
import { expectNoSeriousLearningViolations } from "../test/learningTestA11y";
import { LearningRenderer } from "./LearningRenderer";

afterEach(() => cleanup());

function renderLearning(blocks: LearningBlockNode[]) {
  return render(
    <MemoryRouter>
      <main aria-label="[FICTIF] Leçon de démonstration">
        <LearningRenderer blocks={blocks} />
      </main>
    </MemoryRouter>,
  );
}

describe("LearningRenderer DOM safety", () => {
  it("keeps raw HTML and arbitrary URLs inert text", () => {
    const payload = "[FICTIF] <img src=x onerror=alert(1)><script>alert(2)</script> https://example.invalid";
    const { container } = renderLearning([
      {
        type: "paragraph",
        inlines: [{ type: "text", text: payload, marks: [] }],
      },
    ]);

    expect(screen.getByText(payload)).toBeTruthy();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    expect(container.querySelector("[onerror]")).toBeNull();
    expect(container.innerHTML).toContain("&lt;script&gt;");
    expect(container.textContent).toContain("onerror=alert(1)");
  });

  it("creates only validated internal reference links and exposes them to the keyboard", async () => {
    const user = userEvent.setup();
    renderLearning([
      {
        type: "paragraph",
        inlines: [
          {
            type: "source_ref",
            id: "ref.fictif-12",
            source_id: "source.fictif",
            page: 12,
            end_page: null,
            label: "[FICTIF] Source page 12",
          },
          { type: "line_break" },
          {
            type: "concept_ref",
            concept_id: "concept.fictif",
            label: "[FICTIF] Concept interne",
          },
          { type: "line_break" },
          {
            type: "exercise_ref",
            exercise_id: "exercise.fictif",
            label: "[FICTIF] Exercice interne",
          },
        ],
      },
    ]);

    const source = screen.getByRole("link", { name: "[FICTIF] Source page 12" });
    const concept = screen.getByRole("link", { name: "[FICTIF] Concept interne" });
    const exercise = screen.getByRole("link", { name: "[FICTIF] Exercice interne" });
    expect(source.getAttribute("href")).toBe("/parcours/sources/source.fictif?page=12");
    expect(concept.getAttribute("href")).toBe("/parcours/lecons/concept.fictif");
    expect(exercise.getAttribute("href")).toBe("/parcours/exercices/exercise.fictif");

    await user.tab();
    expect(document.activeElement).toBe(source);
    await user.tab();
    expect(document.activeElement).toBe(concept);
    await user.tab();
    expect(document.activeElement).toBe(exercise);
  });

  it("fails closed for traversal references and unknown directives", () => {
    const { container } = renderLearning([
      {
        type: "paragraph",
        inlines: [
          {
            type: "source_ref",
            id: "ref.fictif-invalid",
            source_id: "../FICTIF-private",
            page: 0,
            end_page: null,
            label: "[FICTIF] Référence invalide",
          },
        ],
      },
      {
        type: "directive",
        id: "directive.fictif-invalid",
        name: "iframe" as "note",
        title: "[FICTIF] Directive inconnue",
        inlines: [{ type: "text", text: "[FICTIF] Ne doit pas apparaître", marks: [] }],
      },
    ]);

    expect(screen.getByText("Référence indisponible")).toBeTruthy();
    expect(screen.queryByText("[FICTIF] Ne doit pas apparaître")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    expect(container.querySelector("iframe")).toBeNull();
  });

  it("renders math as accessible MathML and keeps allow-listed directives named", async () => {
    const { container } = renderLearning([
      {
        type: "heading",
        id: "heading.fictif-alpha",
        level: 2,
        inlines: [{ type: "text", text: "[FICTIF] Notion alpha", marks: [] }],
      },
      {
        type: "paragraph",
        inlines: [
          { type: "text", text: "[FICTIF] Formule : ", marks: [] },
          { type: "math", latex: "x^2 + y^2" },
        ],
      },
      { type: "math", latex: "\\int_0^1 x \\, dx" },
      {
        type: "directive",
        id: "directive.fictif-note",
        name: "note",
        title: "[FICTIF] Note autorisée",
        inlines: [{ type: "text", text: "[FICTIF] Explication synthétique.", marks: [] }],
      },
      {
        type: "directive",
        id: "directive.fictif-hint",
        name: "hint",
        title: "[FICTIF] Indice autorisé",
        inlines: [{ type: "text", text: "[FICTIF] Commencer par isoler alpha.", marks: [] }],
      },
    ]);

    expect(container.querySelectorAll('[data-math-rendered="true"]')).toHaveLength(2);
    expect(container.querySelectorAll("math")).toHaveLength(2);
    expect(container.querySelectorAll(".katex-html")).toHaveLength(2);
    expect(container.querySelectorAll('annotation[encoding="application/x-tex"]')).toHaveLength(2);
    expect(container.querySelector(".learning-math-error")).toBeNull();
    expect(screen.getByText("Note · [FICTIF] Note autorisée")).toBeTruthy();
    expect(screen.getByText("Indice · [FICTIF] Indice autorisé")).toBeTruthy();
    await expectNoSeriousLearningViolations(container);
  });
});
