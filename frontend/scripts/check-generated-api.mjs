import { spawnSync } from "node:child_process";
import { readdirSync, readFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const generated = join(root, "src", "generated", "api");

function snapshot(directory) {
  const files = new Map();
  const visit = (current) => {
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      const path = join(current, entry.name);
      if (entry.isDirectory()) visit(path);
      else files.set(relative(directory, path), readFileSync(path, "utf8"));
    }
  };
  visit(directory);
  return files;
}

const before = snapshot(generated);
const result = spawnSync(join(root, "node_modules", ".bin", "openapi-ts"), [], {
  cwd: root,
  encoding: "utf8",
  stdio: "inherit",
});
if (result.status !== 0) process.exit(result.status ?? 1);

const after = snapshot(generated);
const paths = new Set([...before.keys(), ...after.keys()]);
const changed = [...paths].filter((path) => before.get(path) !== after.get(path)).sort();
if (changed.length) {
  console.error("Generated API client drift detected:");
  for (const path of changed) console.error(`- src/generated/api/${path}`);
  console.error("Run `pnpm generate:api` and commit the generated files.");
  process.exit(1);
}
console.log(`generated API client: current (${after.size} files)`);
