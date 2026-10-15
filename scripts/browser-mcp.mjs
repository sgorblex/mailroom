import { spawn } from "node:child_process";
import { findBrowser } from "./lib/find-browser.mjs";
import { handshakeCacheKey, runLazyProxy } from "./lib/mcp-lazy-proxy.mjs";
import { SERVER_COMMAND, resolveServer } from "./lib/mcp-server.mjs";

/**
 * How long the server may sit unused before it and its browser are stopped.
 * chrome-devtools-mcp has no lifetime option of its own, so an abandoned
 * session would otherwise hold both until the client disconnects.
 */
const DEFAULT_IDLE_MINUTES = 10;

/** Pins the responsive breakpoint so snapshots do not vary machine to machine. */
const VIEWPORT = "1280x900";

/**
 * Opt out of both upstream data flows by default. `--no-usage-statistics` stops
 * the server reporting usage to Google; `--no-performance-crux` stops the
 * performance tools sending visited URLs to the Google CrUX API. Mailroom holds
 * personal data and the pages driven here are authenticated dev instances, so
 * neither should leave the machine — see `docs/requirements/nonfunctional-requirements.md`.
 */
const PRIVACY_ARGS = ["--no-usage-statistics", "--no-performance-crux"];

/**
 * Everything this script prints must go to stderr: stdout carries the MCP
 * JSON-RPC stream, and a stray log line there corrupts the protocol.
 */
function warn(message) {
  process.stderr.write(`browser-mcp: ${message}\n`);
}

function buildServerArgs(environment) {
  const browserUrl = environment.MAILROOM_BROWSER_URL?.trim();
  if (browserUrl !== undefined && browserUrl.length > 0) {
    // Attaching to an already-running browser: launch-time flags (headless,
    // isolated profile, viewport, executable) are the other process's business.
    warn(`attaching to running browser at ${browserUrl}`);
    return [...PRIVACY_ARGS, "--browserUrl", browserUrl];
  }

  const args = [...PRIVACY_ARGS, "--isolated", "--acceptInsecureCerts", "--viewport", VIEWPORT];
  if (environment.MAILROOM_BROWSER_HEADFUL === "1") {
    warn("MAILROOM_BROWSER_HEADFUL=1 — running headful");
  } else {
    args.unshift("--headless");
  }

  // Extra Chrome flags, e.g. MAILROOM_BROWSER_CHROME_ARGS='--no-sandbox' when the
  // sandbox cannot start (containers, restricted user namespaces).
  for (const chromeArg of (environment.MAILROOM_BROWSER_CHROME_ARGS ?? "").split(/\s+/).filter(Boolean)) {
    args.push("--chromeArg", chromeArg);
  }

  // --channel and --executablePath are mutually exclusive upstream: passing both
  // makes the server exit before the MCP handshake. A channel means "find the
  // installed Chrome of this release channel yourself", so skip our detection.
  const channel = environment.MAILROOM_BROWSER_CHANNEL?.trim();
  if (channel !== undefined && channel.length > 0) {
    if ((environment.MAILROOM_BROWSER_EXECUTABLE ?? "").trim().length > 0) {
      warn("MAILROOM_BROWSER_CHANNEL and MAILROOM_BROWSER_EXECUTABLE are mutually exclusive — unset one");
      process.exit(1);
    }
    warn(`using Chrome channel "${channel}" — letting chrome-devtools-mcp locate it`);
    args.push("--channel", channel);
    return args;
  }

  const browser = findBrowser(environment);
  if (browser === undefined) {
    // Deliberate: no --executablePath means chrome-devtools-mcp resolves a
    // browser itself (installed Chrome, else a downloaded Chrome for Testing),
    // which is far more useful on an unfamiliar machine than a hard failure.
    warn("no local Chrome/Chromium found — letting chrome-devtools-mcp resolve one");
    warn("run `mise run browser-check` to see install options");
    return args;
  }
  if (!browser.usable) {
    warn(`MAILROOM_BROWSER_EXECUTABLE is not an executable file: ${browser.path}`);
    warn("unset it or point it at a real Chrome/Chromium binary");
    process.exit(1);
  }
  warn(`using ${browser.kind} at ${browser.path}${browser.version ? ` (${browser.version})` : ""}`);
  args.push("--executablePath", browser.path);
  return args;
}

/**
 * Node joins argv with plain spaces when `shell` is used and does not quote, so
 * a path containing spaces (`C:\Program Files\...`) would arrive as two
 * arguments. Only reachable on Windows, where the shell is unavoidable.
 */
function quoteForShell(argument) {
  return /\s/.test(argument) ? `"${argument}"` : argument;
}

function readIdleMs(environment) {
  const raw = environment.MAILROOM_BROWSER_IDLE_MINUTES?.trim();
  if (raw === undefined || raw.length === 0) {
    return DEFAULT_IDLE_MINUTES * 60_000;
  }
  const minutes = Number(raw);
  if (!Number.isFinite(minutes) || minutes < 0) {
    warn(`MAILROOM_BROWSER_IDLE_MINUTES is not a number: ${raw} — using ${DEFAULT_IDLE_MINUTES}`);
    return DEFAULT_IDLE_MINUTES * 60_000;
  }
  return minutes * 60_000;
}

const server = resolveServer(process.env);
if (server === undefined) {
  warn(`${SERVER_COMMAND} not found on PATH — run \`mise install\` to fetch the pinned version`);
  warn("(.mcp.json runs this script via `mise exec --`, which puts mise's tools on PATH)");
  process.exit(1);
}

warn(`using ${SERVER_COMMAND}${server.version ? ` ${server.version}` : ""} at ${server.path}`);

const serverArgs = buildServerArgs(process.env);
const useShell = process.platform === "win32";

runLazyProxy({
  cacheKey: handshakeCacheKey({ serverIdentity: server.identity, serverArgs }),
  idleMs: readIdleMs(process.env),
  eager: process.env.MAILROOM_BROWSER_EAGER === "1",
  warn,
  spawnChild: () =>
    spawn(
      useShell ? quoteForShell(server.path) : server.path,
      useShell ? serverArgs.map(quoteForShell) : serverArgs,
      {
        // stdin/stdout are the proxy's; stderr goes straight through so the
        // server's own diagnostics still reach the client's log.
        stdio: ["pipe", "pipe", "inherit"],
        env: {
          ...process.env,
          // The version is pinned and Renovate-managed, so upstream's update
          // check only costs a detached process and an npm registry request on
          // every start.
          CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS: "1",
        },
        // mise installs a `.cmd` shim on Windows, and Node refuses to spawn a
        // .cmd without a shell (EINVAL) since 18.20/20.12.
        shell: useShell,
      },
    ),
});
