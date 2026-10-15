import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

/**
 * Chrome is probed before Chromium on purpose: chrome-devtools-mcp officially
 * supports Google Chrome and Chrome for Testing only, and treats other
 * Chromium-based browsers as "may work". Prefer the supported binary when both
 * are installed; fall back to Chromium rather than failing.
 */
const PATH_CANDIDATES = [
  { command: "google-chrome-stable", kind: "chrome" },
  { command: "google-chrome", kind: "chrome" },
  { command: "chrome", kind: "chrome" },
  { command: "chromium", kind: "chromium" },
  { command: "chromium-browser", kind: "chromium" },
];

/** Checked after PATH, for installs that do not put the browser on PATH (notably macOS bundles). */
const ABSOLUTE_CANDIDATES = [
  { location: "/usr/bin/google-chrome-stable", kind: "chrome" },
  { location: "/usr/bin/google-chrome", kind: "chrome" },
  { location: "/opt/google/chrome/chrome", kind: "chrome" },
  {
    location: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    kind: "chrome",
  },
  { location: "/usr/bin/chromium", kind: "chromium" },
  { location: "/usr/bin/chromium-browser", kind: "chromium" },
  { location: "/snap/bin/chromium", kind: "chromium" },
  {
    location: "/Applications/Chromium.app/Contents/MacOS/Chromium",
    kind: "chromium",
  },
];

const WINDOWS_EXECUTABLE_SUFFIXES = [".exe", ".cmd", ".bat", ""];

function isExecutableFile(candidatePath) {
  try {
    if (!fs.statSync(candidatePath).isFile()) {
      return false;
    }
    fs.accessSync(candidatePath, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

/**
 * PATH lookup without shelling out to `which`/`where`, so this behaves the same
 * on Linux, macOS, and Windows.
 */
export function resolveOnPath(command, environment = process.env) {
  const pathValue = environment.PATH;
  if (pathValue === undefined || pathValue.length === 0) {
    return undefined;
  }
  const suffixes = process.platform === "win32" ? WINDOWS_EXECUTABLE_SUFFIXES : [""];
  for (const directory of pathValue.split(path.delimiter)) {
    if (directory.length === 0) {
      continue;
    }
    for (const suffix of suffixes) {
      const candidatePath = path.join(directory, `${command}${suffix}`);
      if (isExecutableFile(candidatePath)) {
        return candidatePath;
      }
    }
  }
  return undefined;
}

function probeVersion(executablePath) {
  const result = spawnSync(executablePath, ["--version"], {
    stdio: ["ignore", "pipe", "pipe"],
    timeout: 10_000,
  });
  if (result.error || result.status !== 0) {
    return undefined;
  }
  const versionText = result.stdout?.toString("utf8").trim();
  return versionText !== undefined && versionText.length > 0 ? versionText : undefined;
}

/** Chrome and Chromium both report "<Name> <version>", so the name is enough to tell them apart. */
function kindFromVersion(versionText, fallbackKind) {
  if (versionText === undefined) {
    return fallbackKind;
  }
  if (/google chrome/i.test(versionText)) {
    return "chrome";
  }
  if (/chromium/i.test(versionText)) {
    return "chromium";
  }
  return fallbackKind;
}

/**
 * Resolve the browser the MCP launcher should drive.
 *
 * Returns `{ path, kind, version, source }`, or `undefined` when nothing is
 * found — in which case the launcher deliberately omits `--executablePath` and
 * lets chrome-devtools-mcp do its own resolution (installed Chrome, else a
 * downloaded Chrome for Testing).
 *
 * `MAILROOM_BROWSER_EXECUTABLE` wins over all detection, and is reported as
 * unusable rather than silently ignored when it does not point at an
 * executable — a typo there should fail loudly.
 */
export function findBrowser(environment = process.env) {
  const explicitPath = environment.MAILROOM_BROWSER_EXECUTABLE?.trim();
  if (explicitPath !== undefined && explicitPath.length > 0) {
    if (!isExecutableFile(explicitPath)) {
      return {
        path: explicitPath,
        kind: "unknown",
        version: undefined,
        source: "MAILROOM_BROWSER_EXECUTABLE",
        usable: false,
      };
    }
    const version = probeVersion(explicitPath);
    return {
      path: explicitPath,
      kind: kindFromVersion(version, "unknown"),
      version,
      source: "MAILROOM_BROWSER_EXECUTABLE",
      usable: true,
    };
  }

  for (const { command, kind } of PATH_CANDIDATES) {
    const resolvedPath = resolveOnPath(command, environment);
    if (resolvedPath !== undefined) {
      const version = probeVersion(resolvedPath);
      return {
        path: resolvedPath,
        kind: kindFromVersion(version, kind),
        version,
        source: "PATH",
        usable: true,
      };
    }
  }

  for (const { location, kind } of ABSOLUTE_CANDIDATES) {
    if (isExecutableFile(location)) {
      const version = probeVersion(location);
      return {
        path: location,
        kind: kindFromVersion(version, kind),
        version,
        source: "well-known path",
        usable: true,
      };
    }
  }

  return undefined;
}

function readOsReleaseIds() {
  try {
    const osReleaseText = fs.readFileSync("/etc/os-release", "utf8");
    const ids = [];
    for (const line of osReleaseText.split("\n")) {
      const match = /^(ID|ID_LIKE)=(.*)$/.exec(line.trim());
      if (match !== null) {
        ids.push(...match[2].replaceAll('"', "").split(/\s+/).filter(Boolean));
      }
    }
    return ids;
  } catch {
    return [];
  }
}

/**
 * Install hints, most-likely-relevant first. Installing a browser is an
 * operator step — no task in this repository installs one.
 */
export function installHints() {
  const hints = [
    { match: ["arch"], text: "Arch:            sudo pacman -S --needed chromium" },
    { match: ["debian", "ubuntu"], text: "Debian/Ubuntu:   sudo apt install chromium" },
    { match: ["fedora", "rhel"], text: "Fedora:          sudo dnf install chromium" },
    { match: ["opensuse", "suse"], text: "openSUSE:        sudo zypper install chromium" },
  ];
  const ids = readOsReleaseIds();
  const isMac = process.platform === "darwin";
  const ordered = [...hints].sort((first, second) => {
    const firstMatches = first.match.some((id) => ids.includes(id)) ? 0 : 1;
    const secondMatches = second.match.some((id) => ids.includes(id)) ? 0 : 1;
    return firstMatches - secondMatches;
  });
  const lines = ordered.map((hint) => hint.text);
  const macLine = "macOS:           brew install --cask google-chrome";
  if (isMac) {
    lines.unshift(macLine);
  } else {
    lines.push(macLine);
  }
  lines.push("any platform:    export MAILROOM_BROWSER_EXECUTABLE=/path/to/chrome");
  return lines;
}
