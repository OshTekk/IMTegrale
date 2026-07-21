import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { LearningBlockNode } from "../types";
import { LearningRenderer } from "./LearningRenderer";

function render(blocks: LearningBlockNode[], contentId?: string): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <LearningRenderer blocks={blocks} contentId={contentId} />
    </MemoryRouter>,
  );
}

describe("LearningRenderer", () => {
  it("escapes text and never turns raw HTML or external URLs into active content", () => {
    const html = render([
      {
        type: "paragraph",
        inlines: [{ type: "text", text: "<script>alert(1)</script> https://evil.test", marks: [] }],
      },
    ]);

    expect(html).not.toContain("<script>");
    expect(html).not.toContain("href=");
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
  });

  it("renders only the fixed internal reference syntax", () => {
    const html = render(
      [
        {
          type: "paragraph",
          inlines: [
            {
              type: "source_ref",
              id: "ref.one",
              source_id: "source.one",
              page: 12,
              end_page: null,
              label: "Page exacte",
            },
            { type: "concept_ref", concept_id: "concept.one", label: "Concept" },
            { type: "exercise_ref", exercise_id: "exercise.one", label: "Exercice" },
          ],
        },
      ],
      "lesson.one",
    );

    expect(html).toContain('href="/parcours/references/lesson.one/ref.one"');
    expect(html).toContain('href="/parcours/lecons/concept.one"');
    expect(html).toContain('href="/parcours/exercices/exercise.one"');
  });

  it("fails closed for traversal IDs, invalid pages and unknown directives", () => {
    const html = render([
      {
        type: "paragraph",
        inlines: [{ type: "source_ref", id: "ref.bad", source_id: "../private", page: 0, end_page: null, label: null }],
      },
      {
        type: "directive",
        id: "directive.bad",
        name: "iframe" as "note",
        title: "Bad",
        inlines: [{ type: "text", text: "active", marks: [] }],
      },
    ]);

    expect(html).toContain("Référence indisponible");
    expect(html).not.toContain("../private");
    expect(html).not.toContain("iframe");
    expect(html).not.toContain("active");
  });

  it("renders selectable KaTeX with MathML instead of raw source as the primary view", () => {
    const html = render([
      { type: "paragraph", inlines: [{ type: "math", latex: "x^2 + y^2" }] },
      { type: "math", latex: "\\int_0^1 x dx" },
    ]);

    expect(html.match(/data-math-rendered="true"/g)).toHaveLength(2);
    expect(html.match(/class="katex/g)?.length).toBeGreaterThanOrEqual(2);
    expect(html.match(/<math/g)).toHaveLength(2);
    expect(html).toContain('encoding="application/x-tex"');
    expect(html).not.toContain("<code>x^2 + y^2</code>");
    expect(html).not.toContain("<code>\\int_0^1 x dx</code>");
  });

  it("never creates active content from untrusted math commands", () => {
    const html = render([
      { type: "math", latex: "\\href{https://example.invalid}{x}" },
      { type: "math", latex: "\\htmlClass{fixture}{y}" },
    ]);

    expect(html).not.toContain("href=");
    expect(html).not.toContain('example.invalid"');
    expect(html).not.toContain('class="fixture"');
    expect(html).toContain("Formule invalide");
  });

  it("loads illustrations only through a protected asset ID", () => {
    const html = render([
      { type: "image", asset_id: "asset.figure-1", alt_text: "Illustration fictive", caption: "Légende fictive" },
      { type: "image", asset_id: "../private", alt_text: "Interdite", caption: null },
    ]);

    expect(html).toContain('src="/api/v1/learning/assets/asset.figure-1"');
    expect(html).toContain('alt="Illustration fictive"');
    expect(html).toContain("Légende fictive");
    expect(html).toContain("Illustration indisponible");
    expect(html).not.toContain("../private");
  });

  it("applies only the allowed inline marks", () => {
    const html = render([
      { type: "paragraph", inlines: [{ type: "text", text: "important", marks: ["strong", "emphasis", "code"] }] },
    ]);
    expect(html).toContain("<code><em><strong>important</strong></em></code>");
  });

  it("names every directive in text even when its optional title is absent", () => {
    const html = render([
      {
        type: "directive",
        id: "warning.one",
        name: "warning",
        title: null,
        inlines: [{ type: "text", text: "Message fictif", marks: [] }],
      },
    ]);

    expect(html).toContain("Avertissement");
    expect(html).toContain("Message fictif");
  });
});
