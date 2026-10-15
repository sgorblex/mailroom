import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { findBrowser, installHints } from "./lib/find-browser.mjs";
import { handshakeCacheDirectory } from "./lib/mcp-lazy-proxy.mjs";
import { SERVER_COMMAND, resolveServer } from "./lib/mcp-server.mjs";

/**
 * Reports the browser `scripts/browser-mcp.mjs` will drive, so the doctor
 * command and the real launch can never disagree — they call the same resolver.
 *
 * Pass --launch to additionally start the browser headless and render a page,
 * which catches sandbox/user-namespace breakage that plain detection cannot.
 */
const shouldLaunch = process.argv.includes("--launch");

/**
 * The launcher only starts the server on first use, so a cold cache is the
 * difference between an instant first tool call and one that waits on a spawn.
 * Reported here rather than left to be discovered mid-verification.
 */
function reportServer(environment) {
  const server = resolveServer(environment);
  if (server === undefined) {
    console.error(`server:   ${SERVER_COMMAND} not found on PATH`);
    console.error(`\nbrowser-check: run \`mise install\` — the pin lives in mise.toml's [tools].`);
    process.exit(1);
  }
  console.log(`server:   ${SERVER_COMMAND} ${server.version ?? "(version unknown)"}`);
  console.log(`path:     ${server.path}`);
  const directory = handshakeCacheDirectory(environment);
  let warm = 0;
  try {
    warm = fs.readdirSync(directory).filter((name) => name.endsWith(".json")).length;
  } catch {
    // No directory yet simply means nothing has handshaked on this machine.
  }
  console.log(
    `cache:    ${warm > 0 ? `${warm} cached in ${directory}` : `none cached — the first session starts the server eagerly`}`,
  );
}

function reportOverrides(environment) {
  const browserUrl = environment.MAILROOM_BROWSER_URL?.trim();
  if (browserUrl !== undefined && browserUrl.length > 0) {
    console.log(`mode:     attach to running browser at ${browserUrl}`);
    console.log("          (MAILROOM_BROWSER_URL set — no browser is launched, detection skipped)");
    return true;
  }
  const headful = environment.MAILROOM_BROWSER_HEADFUL === "1";
  console.log(`mode:     launch ${headful ? "headful" : "headless"}, isolated profile`);
  const chromeArgs = (environment.MAILROOM_BROWSER_CHROME_ARGS ?? "").trim();
  if (chromeArgs.length > 0) {
    console.log(`chromeArg: ${chromeArgs}`);
  }
  const channel = environment.MAILROOM_BROWSER_CHANNEL?.trim();
  if (channel !== undefined && channel.length > 0) {
    // Mirrors the launcher: a channel skips local detection entirely, because
    // --channel and --executablePath are mutually exclusive upstream.
    if ((environment.MAILROOM_BROWSER_EXECUTABLE ?? "").trim().length > 0) {
      console.error(`channel:  ${channel}`);
      console.error(
        "\nbrowser-check: MAILROOM_BROWSER_CHANNEL and MAILROOM_BROWSER_EXECUTABLE are mutually exclusive.",
      );
      console.error("The MCP server refuses to start with both — unset one.");
      process.exit(1);
    }
    console.log(`channel:  ${channel} (chrome-devtools-mcp locates it; local detection skipped)`);
    console.log("\nbrowser-check: ok");
    process.exit(0);
  }
  return false;
}

function launchProbe(executablePath) {
  const profileDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "mailroom-browser-check-"));
  try {
    const result = spawnSync(
      executablePath,
      [
        "--headless",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        `--user-data-dir=${profileDirectory}`,
        "--virtual-time-budget=5000",
        "--dump-dom",
        "about:blank",
      ],
      { stdio: ["ignore", "pipe", "pipe"], timeout: 60_000 },
    );
    if (result.error) {
      return { ok: false, detail: result.error.message };
    }
    if (result.status !== 0) {
      const stderrText = result.stderr?.toString("utf8").trim() ?? "";
      return { ok: false, detail: `exited with code ${result.status}\n${stderrText}` };
    }
    return { ok: true };
  } finally {
    fs.rmSync(profileDirectory, { recursive: true, force: true });
  }
}

reportServer(process.env);
console.log("");

const isAttaching = reportOverrides(process.env);
if (isAttaching) {
  console.log("\nbrowser-check: ok");
  process.exit(0);
}

const browser = findBrowser(process.env);

if (browser !== undefined && !browser.usable) {
  console.error(`browser:  MAILROOM_BROWSER_EXECUTABLE=${browser.path}`);
  console.error("\nbrowser-check: that path is not an executable file.");
  console.error("Unset MAILROOM_BROWSER_EXECUTABLE to fall back to detection, or fix the path.");
  process.exit(1);
}

if (browser === undefined) {
  console.error("browser:  none found");
  console.error(
    "\nbrowser-check: no local Chrome/Chromium. chrome-devtools-mcp will try to resolve",
  );
  console.error("one itself (installed Chrome, else a downloaded Chrome for Testing), but");
  console.error("installing a browser is faster and more predictable:\n");
  for (const hint of installHints()) {
    console.error(`  ${hint}`);
  }
  process.exit(1);
}

console.log(`browser:  ${browser.path}`);
console.log(`kind:     ${browser.kind}${browser.kind === "chromium" ? " (Chrome is preferred where available)" : ""}`);
console.log(`version:  ${browser.version ?? "unknown"}`);
console.log(`found by: ${browser.source}`);

if (!shouldLaunch) {
  console.log("\nbrowser-check: ok (pass --launch to also start it headless)");
  process.exit(0);
}

const probe = launchProbe(browser.path);
if (!probe.ok) {
  console.error(`\nbrowser-check: headless launch failed — ${probe.detail}`);
  console.error("\nIf this is a sandbox/user-namespace failure (common in containers), pass the");
  console.error("flag through to Chrome and re-run:");
  console.error("  export MAILROOM_BROWSER_CHROME_ARGS=--no-sandbox");
  process.exit(1);
}

console.log("\nbrowser-check: ok (headless launch succeeded)");
