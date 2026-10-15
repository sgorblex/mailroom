import fs from "node:fs";
import path from "node:path";
import { resolveOnPath } from "./find-browser.mjs";

/**
 * Locate the pinned `chrome-devtools-mcp` binary that `mise.toml`'s `[tools]`
 * installs, and identify the exact install behind it.
 *
 * The identity keys the handshake cache, so it must change whenever the server
 * does. It is read from the package manifest where one is reachable and falls
 * back to the resolved path's inode metadata — deliberately never by running
 * the binary, since a `--version` spawn on every session start is most of the
 * cost this lazy launcher exists to avoid.
 */

export const SERVER_COMMAND = "chrome-devtools-mcp";

/** mise installs the shim beside `lib/node_modules/<package>/package.json`. */
function readPackageVersion(binaryPath) {
  let directory = path.dirname(binaryPath);
  for (let depth = 0; depth < 6; depth += 1) {
    const manifest = path.join(directory, "lib", "node_modules", SERVER_COMMAND, "package.json");
    const sibling = path.join(directory, "node_modules", SERVER_COMMAND, "package.json");
    for (const candidate of [manifest, sibling]) {
      try {
        const parsed = JSON.parse(fs.readFileSync(candidate, "utf8"));
        if (parsed?.name === SERVER_COMMAND && typeof parsed.version === "string") {
          return parsed.version;
        }
      } catch {
        // Keep walking; a missing manifest is expected at most levels.
      }
    }
    const parent = path.dirname(directory);
    if (parent === directory) {
      break;
    }
    directory = parent;
  }
  return undefined;
}

/**
 * @returns `{ path, realPath, version, identity }`, or `undefined` when the
 * binary is not on PATH — meaning `mise install` has not run for this checkout.
 */
export function resolveServer(environment = process.env) {
  const binaryPath = resolveOnPath(SERVER_COMMAND, environment);
  if (binaryPath === undefined) {
    return undefined;
  }
  let realPath = binaryPath;
  let stats;
  try {
    realPath = fs.realpathSync(binaryPath);
    stats = fs.statSync(realPath);
  } catch {
    // Fall through: a path we cannot stat still keys the cache by name alone.
  }
  const version = readPackageVersion(realPath);
  return {
    path: binaryPath,
    realPath,
    version,
    identity: version ?? `${realPath}:${stats?.size ?? 0}:${Math.round(stats?.mtimeMs ?? 0)}`,
  };
}
