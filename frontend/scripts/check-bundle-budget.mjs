import { brotliCompressSync, gzipSync } from "node:zlib";
import { readFileSync } from "node:fs";
import { extname, join, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const dist = join(root, "dist");
const manifest = JSON.parse(readFileSync(join(dist, ".vite", "manifest.json"), "utf8"));
const budgets = {
  initial: { gzip: 155 * 1024, brotli: 135 * 1024 },
  largestNonPdfJavaScriptGzip: 110 * 1024,
  largestStylesheetGzip: 36 * 1024,
  learningRoute: { gzip: 115 * 1024, brotli: 98 * 1024 },
  sourceViewerGzip: 16 * 1024,
  nonPdfApplication: { gzip: 520 * 1024, brotli: 455 * 1024 },
  pdfRuntimeGzip: 135 * 1024,
  pdfWorker: { raw: 1_300 * 1024, gzip: 390 * 1024 },
};

const entries = Object.entries(manifest);
const entryKey = entries.find(([, value]) => value.isEntry)?.[0];
const learningKey = "src/pages/LearningPage.tsx";
const viewerKey = "src/components/LearningSourceViewer.tsx";
const pdfRuntimeKey = entries.find(([key]) => key.includes("pdfjs-dist") && key.endsWith("/build/pdf.mjs"))?.[0];
const pdfWorkerKey = entries.find(
  ([key]) => key.includes("pdfjs-dist") && key.endsWith("/build/pdf.worker.min.mjs"),
)?.[0];
const failures = [];

for (const [label, key] of [
  ["application entry", entryKey],
  ["learning route", learningKey],
  ["source viewer", viewerKey],
  ["PDF.js runtime", pdfRuntimeKey],
  ["PDF.js worker", pdfWorkerKey],
]) {
  if (!key || !manifest[key]) failures.push(`${label} is missing from the Vite manifest`);
}

if (failures.length) {
  console.error(`Bundle topology invalid:\n${failures.map((failure) => `- ${failure}`).join("\n")}`);
  process.exit(1);
}

const measurementCache = new Map();
function measure(file) {
  if (!measurementCache.has(file)) {
    const content = readFileSync(join(dist, file));
    measurementCache.set(file, {
      file,
      extension: extname(file),
      raw: content.byteLength,
      gzip: gzipSync(content, { level: 9 }).byteLength,
      brotli: brotliCompressSync(content).byteLength,
    });
  }
  return measurementCache.get(file);
}

function staticGraph(rootKey) {
  const visited = new Set();
  const files = new Set();
  const visit = (key) => {
    if (visited.has(key)) return;
    visited.add(key);
    const entry = manifest[key];
    if (!entry) return;
    if (entry.file) files.add(entry.file);
    for (const css of entry.css ?? []) files.add(css);
    for (const imported of entry.imports ?? []) visit(imported);
  };
  visit(rootKey);
  return files;
}

function aggregate(files) {
  return [...files].map(measure).reduce(
    (total, item) => ({
      raw: total.raw + item.raw,
      gzip: total.gzip + item.gzip,
      brotli: total.brotli + item.brotli,
    }),
    { raw: 0, gzip: 0, brotli: 0 },
  );
}

function difference(files, excluded) {
  return new Set([...files].filter((file) => !excluded.has(file)));
}

const initialFiles = staticGraph(entryKey);
const learningFiles = staticGraph(learningKey);
const viewerFiles = staticGraph(viewerKey);
const learningIncrementalFiles = difference(learningFiles, initialFiles);
const viewerIncrementalFiles = difference(viewerFiles, new Set([...initialFiles, ...learningIncrementalFiles]));
const initial = aggregate(initialFiles);
const learningRoute = aggregate(learningIncrementalFiles);
const sourceViewer = aggregate(viewerIncrementalFiles);
const pdfRuntime = measure(manifest[pdfRuntimeKey].file);
const pdfWorker = measure(manifest[pdfWorkerKey].file);

const builtCodeAndStyles = entries
  .flatMap(([, entry]) => [entry.file, ...(entry.css ?? [])])
  .filter((file, index, files) => file && files.indexOf(file) === index)
  .filter((file) => [".css", ".js", ".mjs"].includes(extname(file)))
  .map(measure);
const nonPdfAssets = builtCodeAndStyles.filter(
  (asset) => asset.file !== pdfRuntime.file && asset.file !== pdfWorker.file,
);
const nonPdfApplication = aggregate(new Set(nonPdfAssets.map((asset) => asset.file)));
const largestNonPdfJavaScriptGzip = Math.max(
  0,
  ...nonPdfAssets.filter((asset) => asset.extension === ".js").map((asset) => asset.gzip),
);
const largestStylesheetGzip = Math.max(
  0,
  ...nonPdfAssets.filter((asset) => asset.extension === ".css").map((asset) => asset.gzip),
);

function limit(label, actual, maximum) {
  if (actual > maximum) failures.push(`${label} ${actual} > ${maximum}`);
}

limit("initial gzip", initial.gzip, budgets.initial.gzip);
limit("initial brotli", initial.brotli, budgets.initial.brotli);
limit("largest non-PDF JavaScript gzip", largestNonPdfJavaScriptGzip, budgets.largestNonPdfJavaScriptGzip);
limit("largest stylesheet gzip", largestStylesheetGzip, budgets.largestStylesheetGzip);
limit("learning route gzip", learningRoute.gzip, budgets.learningRoute.gzip);
limit("learning route brotli", learningRoute.brotli, budgets.learningRoute.brotli);
limit("source viewer gzip", sourceViewer.gzip, budgets.sourceViewerGzip);
limit("non-PDF application gzip", nonPdfApplication.gzip, budgets.nonPdfApplication.gzip);
limit("non-PDF application brotli", nonPdfApplication.brotli, budgets.nonPdfApplication.brotli);
limit("PDF.js runtime gzip", pdfRuntime.gzip, budgets.pdfRuntimeGzip);
limit("PDF.js worker raw", pdfWorker.raw, budgets.pdfWorker.raw);
limit("PDF.js worker gzip", pdfWorker.gzip, budgets.pdfWorker.gzip);

const learningEntry = manifest[learningKey];
const viewerEntry = manifest[viewerKey];
if (initialFiles.has(viewerEntry.file) || initialFiles.has(pdfRuntime.file) || initialFiles.has(pdfWorker.file)) {
  failures.push("PDF.js or its viewer leaked into the initial application graph");
}
if (!(learningEntry.dynamicImports ?? []).includes(viewerKey)) {
  failures.push("the source viewer is not a dynamic import of the learning route");
}
if (!(viewerEntry.dynamicImports ?? []).includes(pdfRuntimeKey)) {
  failures.push("PDF.js is not a dynamic import of the source viewer");
}
if (!(viewerEntry.assets ?? []).includes(pdfWorker.file)) {
  failures.push("the local PDF.js worker is not attached to the source viewer chunk");
}

console.log(
  JSON.stringify(
    {
      initial,
      largestNonPdfJavaScriptGzip,
      largestStylesheetGzip,
      learningRoute,
      sourceViewer,
      nonPdfApplication,
      pdfRuntime,
      pdfWorker,
      budgets,
    },
    null,
    2,
  ),
);
if (failures.length) {
  console.error(`Bundle budget exceeded:\n${failures.map((failure) => `- ${failure}`).join("\n")}`);
  process.exit(1);
}
