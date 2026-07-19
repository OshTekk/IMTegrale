import { Fragment, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { learningAssetUrl, learningContentHref, safeLearningId } from "../lib/learning";
import type { LearningBlockNode, LearningInlineNode } from "../types";

const directiveLabels = {
  note: "Note",
  warning: "Avertissement",
  definition: "Définition",
  hint: "Indice",
  solution: "Correction",
} as const;

function markedText(node: Extract<LearningInlineNode, { type: "text" }>, key: string): ReactNode {
  let content: ReactNode = node.text;
  for (const mark of node.marks ?? []) {
    if (mark === "emphasis") content = <em>{content}</em>;
    else if (mark === "strong") content = <strong>{content}</strong>;
    else if (mark === "code") content = <code>{content}</code>;
  }
  return <Fragment key={key}>{content}</Fragment>;
}

function renderInline(node: LearningInlineNode, key: string, contentId?: string): ReactNode {
  switch (node.type) {
    case "text":
      return markedText(node, key);
    case "math":
      return (
        <code className="learning-math-inline" key={key} aria-label={`Formule mathématique : ${node.latex}`}>
          {node.latex}
        </code>
      );
    case "line_break":
      return <br key={key} />;
    case "source_ref": {
      const sourceId = safeLearningId(node.source_id);
      const page = Number.isInteger(node.page) && node.page > 0 ? node.page : null;
      if (!sourceId || !page)
        return (
          <span className="learning-reference-invalid" key={key}>
            Référence indisponible
          </span>
        );
      const safeContentId = safeLearningId(contentId);
      const referenceId = safeLearningId(node.id);
      const to =
        safeContentId && referenceId
          ? `/parcours/references/${encodeURIComponent(safeContentId)}/${encodeURIComponent(referenceId)}`
          : `/parcours/sources/${encodeURIComponent(sourceId)}?page=${page}`;
      return (
        <Link className="learning-reference" key={key} to={to}>
          {node.label?.trim() || `Source · p. ${page}`}
        </Link>
      );
    }
    case "concept_ref": {
      const href = learningContentHref("concept", node.concept_id);
      return href ? (
        <Link className="learning-reference" key={key} to={href}>
          {node.label?.trim() || "Voir le concept"}
        </Link>
      ) : (
        <span className="learning-reference-invalid" key={key}>
          Concept indisponible
        </span>
      );
    }
    case "exercise_ref": {
      const href = learningContentHref("exercise", node.exercise_id);
      return href ? (
        <Link className="learning-reference" key={key} to={href}>
          {node.label?.trim() || "Voir l'exercice"}
        </Link>
      ) : (
        <span className="learning-reference-invalid" key={key}>
          Exercice indisponible
        </span>
      );
    }
    default:
      return null;
  }
}

function inlineChildren(children: LearningInlineNode[], key: string, contentId?: string): ReactNode[] {
  return children.map((node, index) => renderInline(node, `${key}-${index}`, contentId));
}

function renderBlock(node: LearningBlockNode, key: string, contentId?: string): ReactNode {
  switch (node.type) {
    case "paragraph":
      return <p key={key}>{inlineChildren(node.inlines, key, contentId)}</p>;
    case "heading": {
      const headingId = safeLearningId(node.id) ?? undefined;
      const content = inlineChildren(node.inlines, key, contentId);
      if (node.level === 2)
        return (
          <h2 id={headingId} key={key}>
            {content}
          </h2>
        );
      if (node.level === 3)
        return (
          <h3 id={headingId} key={key}>
            {content}
          </h3>
        );
      if (node.level === 4)
        return (
          <h4 id={headingId} key={key}>
            {content}
          </h4>
        );
      if (node.level === 5)
        return (
          <h5 id={headingId} key={key}>
            {content}
          </h5>
        );
      return (
        <h6 id={headingId} key={key}>
          {content}
        </h6>
      );
    }
    case "list": {
      const items = node.items.map((item, index) => (
        <li key={`${key}-${index}`}>{inlineChildren(item.inlines, `${key}-${index}`, contentId)}</li>
      ));
      return node.ordered ? (
        <ol key={key} start={node.start ?? undefined}>
          {items}
        </ol>
      ) : (
        <ul key={key}>{items}</ul>
      );
    }
    case "quote":
      return <blockquote key={key}>{inlineChildren(node.inlines, key, contentId)}</blockquote>;
    case "code":
      return (
        <pre className="learning-code-block" key={key}>
          <code data-language={node.language || undefined}>{node.code}</code>
        </pre>
      );
    case "math":
      return (
        <pre className="learning-math-block" key={key} aria-label={`Formule mathématique : ${node.latex}`}>
          <code>{node.latex}</code>
        </pre>
      );
    case "image": {
      const src = learningAssetUrl(node.asset_id);
      if (!src)
        return (
          <p className="learning-reference-invalid" key={key}>
            Illustration indisponible
          </p>
        );
      return (
        <figure className="learning-figure" key={key}>
          <img src={src} alt={node.alt_text} loading="lazy" decoding="async" />
          {node.caption && <figcaption>{node.caption}</figcaption>}
        </figure>
      );
    }
    case "directive":
      if (!Object.hasOwn(directiveLabels, node.name)) return null;
      return (
        <aside className={`learning-callout learning-callout-${node.name}`} key={key}>
          <strong>
            {directiveLabels[node.name]}
            {node.title ? ` · ${node.title}` : ""}
          </strong>
          <p>{inlineChildren(node.inlines, key, contentId)}</p>
        </aside>
      );
    case "thematic_break":
      return <hr key={key} />;
    default:
      return null;
  }
}

export function LearningRenderer({ blocks, contentId }: { blocks: LearningBlockNode[]; contentId?: string }) {
  return (
    <div className="learning-renderer">
      {blocks.map((block, index) => renderBlock(block, `block-${index}`, contentId))}
    </div>
  );
}
