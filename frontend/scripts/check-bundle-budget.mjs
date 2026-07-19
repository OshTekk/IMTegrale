import { brotliCompressSync, gzipSync } from "node:zlib";
import { readdirSync, readFileSync } from "node:fs";
import { extname, join, relative, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const dist = join(root, "dist");
const budgets = {
  maxJavaScriptGzip: 110 * 1024,
  maxStylesheetGzip: 36 * 1024,
  totalGzip: 412 * 1024,
  totalBrotli: 358 * 1024,
};

const assets = [];
const visit = (directory) => {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) visit(path);
    else if ([".css", ".js"].includes(extname(path))) {
      const content = readFileSync(path);
      assets.push({
        path: relative(dist, path),
        extension: extname(path),
        gzip: gzipSync(content, { level: 9 }).byteLength,
        brotli: brotliCompressSync(content).byteLength,
      });
    }
  }
};
visit(dist);

const largestJavaScript = Math.max(
  0,
  ...assets.filter((asset) => asset.extension === ".js").map((asset) => asset.gzip),
);
const largestStylesheet = Math.max(
  0,
  ...assets.filter((asset) => asset.extension === ".css").map((asset) => asset.gzip),
);
const totalGzip = assets.reduce((total, asset) => total + asset.gzip, 0);
const totalBrotli = assets.reduce((total, asset) => total + asset.brotli, 0);
const failures = [];

if (largestJavaScript > budgets.maxJavaScriptGzip)
  failures.push(`largest JavaScript gzip ${largestJavaScript} > ${budgets.maxJavaScriptGzip}`);
if (largestStylesheet > budgets.maxStylesheetGzip)
  failures.push(`largest stylesheet gzip ${largestStylesheet} > ${budgets.maxStylesheetGzip}`);
if (totalGzip > budgets.totalGzip) failures.push(`total gzip ${totalGzip} > ${budgets.totalGzip}`);
if (totalBrotli > budgets.totalBrotli) failures.push(`total brotli ${totalBrotli} > ${budgets.totalBrotli}`);

console.log(JSON.stringify({ largestJavaScript, largestStylesheet, totalGzip, totalBrotli, budgets }, null, 2));
if (failures.length) {
  console.error(`Bundle budget exceeded:\n${failures.map((failure) => `- ${failure}`).join("\n")}`);
  process.exit(1);
}
