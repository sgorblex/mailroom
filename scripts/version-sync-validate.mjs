/**
 * Asserts every copy of the product version Release Please maintains agrees with
 * `version.txt`. Release Please PRs skip the full check suite, so this is what catches a
 * bump that leaves, say, `uv.lock` behind `pyproject.toml` before the release is tagged.
 *
 * Run via `mise run check-version-sync`; also a step of `.woodpecker/scripts.yaml`.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

function read(relativePath) {
  return fs.readFileSync(path.join(repositoryRoot, relativePath), "utf8");
}

function match(text, pattern) {
  return text.match(pattern)?.[1] ?? null;
}

const expected = read("version.txt").trim();
const copies = {
  ".release-please-manifest.json": JSON.parse(read(".release-please-manifest.json"))["."] ?? null,
  "backend/pyproject.toml": match(
    read("backend/pyproject.toml").split(/^(?=\[)/m).find((table) => table.startsWith("[project]\n")) ?? "",
    /^version = "([^"]+)"/m,
  ),
  "backend/uv.lock": match(read("backend/uv.lock"), /^\[\[package\]\]\nname = "mailroom"\nversion = "([^"]+)"/m),
  "web/package.json": JSON.parse(read("web/package.json")).version ?? null,
};

const problems = Object.entries(copies)
  .filter(([, version]) => version !== expected)
  .map(([file, version]) => `${file}: ${version ?? "no version found"}, expected ${expected}`);

const extraFiles = JSON.parse(read("release-please-config.json")).packages["."]["extra-files"].map((file) => file.path);
for (const file of extraFiles.filter((file) => !(file in copies))) {
  problems.push(`${file}: in release-please-config.json extra-files but not checked here`);
}
for (const file of Object.keys(copies).filter((file) => file.includes("/") && !extraFiles.includes(file))) {
  problems.push(`${file}: checked here but not in release-please-config.json extra-files`);
}

if (problems.length > 0) {
  console.error("version-sync-validate: version copies out of sync");
  for (const problem of problems) {
    console.error(`  - ${problem}`);
  }
  process.exit(1);
}

console.log(`version-sync-validate: ${Object.keys(copies).length} files at ${expected}`);
