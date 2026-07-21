import { readFile } from "node:fs/promises";
import katex from "katex";

const untrustedMathCommand = /\\(?:href|url|includegraphics|htmlClass|htmlData|htmlId|htmlStyle)\b/i;
const activeUri =
  /(?:https?|ftps?|javascript|data|vbscript|file|mailto|tel|sms|blob|wss?|ssh|sftp):|[a-z][a-z0-9+.-]*:\/\//i;

export function validateLearningMath(value) {
  let expressionCount = 0;
  const visit = (node, pointer) => {
    if (Array.isArray(node)) {
      node.forEach((item, index) => visit(item, `${pointer}/${index}`));
      return;
    }
    if (!node || typeof node !== "object") return;
    if (node.type === "math" && typeof node.latex === "string") {
      expressionCount += 1;
      try {
        if (untrustedMathCommand.test(node.latex) || activeUri.test(node.latex)) {
          throw new Error("Untrusted math source");
        }
        katex.renderToString(node.latex, {
          displayMode: pointer.includes("/inlines/") === false,
          output: "mathml",
          throwOnError: true,
          strict: "error",
          trust: false,
          maxExpand: 500,
          maxSize: 20,
        });
      } catch {
        throw new Error(`Invalid learning math at ${pointer}/latex`);
      }
    }
    for (const [key, child] of Object.entries(node)) visit(child, `${pointer}/${key}`);
  };
  visit(value, "$");
  return expressionCount;
}

async function main() {
  const input = process.argv[2] ?? new URL("../../backend/tests/fixtures/learning/math-renderer.json", import.meta.url);
  const payload = JSON.parse(await readFile(input, "utf8"));
  const count = validateLearningMath(payload);
  process.stdout.write(`learning-math: ok (${count} expressions)\n`);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : "Learning math validation failed"}\n`);
    process.exitCode = 1;
  });
}
