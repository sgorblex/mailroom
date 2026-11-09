import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

function removeQuiet(absolutePath) {
  try {
    fs.rmSync(absolutePath, { recursive: true, force: true });
  } catch (cause) {
    const maybeCode =
      cause instanceof Error &&
      "code" in cause &&
      typeof cause.code === "string" &&
      cause.code.length > 0
        ? ` (${cause.code})`
        : "";
    console.warn(`clean: could not remove ${absolutePath}${maybeCode}:`, cause);
  }
}

function cleanBackend() {
  const backendDirectory = path.join(repositoryRoot, "backend");
  const staticNames = [".venv", ".mypy_cache", ".ruff_cache", ".pytest_cache"];
  for (const name of staticNames) {
    removeQuiet(path.join(backendDirectory, name));
  }
  // The mailroom-core workspace member keeps its own tool caches next to its pyproject.
  for (const name of [".mypy_cache", ".ruff_cache", ".pytest_cache"]) {
    removeQuiet(path.join(backendDirectory, "core", name));
  }
  let entries;
  try {
    entries = fs.readdirSync(backendDirectory, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    if (entry.isDirectory() && entry.name.endsWith(".egg-info")) {
      removeQuiet(path.join(backendDirectory, entry.name));
    }
  }
}

function cleanWeb() {
  const webDirectory = path.join(repositoryRoot, "web");
  for (const name of ["node_modules", "dist", ".tscache"]) {
    removeQuiet(path.join(webDirectory, name));
  }
}

const cleanTarget = process.argv[2];
if (cleanTarget === "backend") {
  cleanBackend();
} else if (cleanTarget === "web") {
  cleanWeb();
} else {
  console.error("Usage: node scripts/clean.mjs <backend|web>");
  process.exit(1);
}
