import katex from "katex";
import "katex/dist/katex.min.css";
import { useLearningReviewMode } from "./LearningReviewMode";

const untrustedMathCommand = /\\(?:href|url|includegraphics|htmlClass|htmlData|htmlId|htmlStyle)\b/i;
const activeUri =
  /(?:https?|ftps?|javascript|data|vbscript|file|mailto|tel|sms|blob|wss?|ssh|sftp):|[a-z][a-z0-9+.-]*:\/\//i;

function renderLearningMath(latex: string, displayMode: boolean): string {
  if (untrustedMathCommand.test(latex) || activeUri.test(latex)) throw new Error("Untrusted math source");
  return katex.renderToString(latex, {
    displayMode,
    output: "htmlAndMathml",
    throwOnError: true,
    strict: "error",
    trust: false,
    maxExpand: 500,
    maxSize: 20,
  });
}

export function LearningMath({ latex, displayMode }: { latex: string; displayMode: boolean }) {
  const { enabled: reviewMode } = useLearningReviewMode();
  let html: string;
  try {
    html = renderLearningMath(latex, displayMode);
  } catch {
    return (
      <span className={displayMode ? "learning-math-error learning-math-block" : "learning-math-error"} role="note">
        {import.meta.env.DEV || reviewMode ? (
          <>
            Formule invalide : <code>{latex}</code>
          </>
        ) : (
          "Formule indisponible"
        )}
      </span>
    );
  }

  const Tag = displayMode ? "div" : "span";
  return (
    <Tag
      className={displayMode ? "learning-math learning-math-block" : "learning-math learning-math-inline"}
      data-math-rendered="true"
      // KaTeX owns this isolated subtree. The input is a strict AST field and
      // rendering keeps trust disabled, strict errors enabled and expansion bounded.
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
