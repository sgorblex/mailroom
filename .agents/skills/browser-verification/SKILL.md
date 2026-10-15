---
name: browser-verification
description: Use when verifying a change in a real browser in this repository — running the app and clicking through it, taking screenshots, checking the console for errors, or confirming a UI flow actually works. Covers the chrome-devtools-mcp setup, the dev URL, and the troubleshooting paths.
---

# Browser verification (chrome-devtools-mcp)

Driving the Mailroom app in a real browser uses **`chrome-devtools-mcp`**, declared in [`.mcp.json`](../../../.mcp.json) and launched by [`scripts/browser-mcp.mjs`](../../../scripts/browser-mcp.mjs).

**What this is not:** a test suite, or anything CI runs. It is a manual/agent verification aid against a **running dev instance**. `docs/REQUIREMENTS.md` — **Non-functional requirements** still excludes end-to-end browser automation *test frameworks* from the engineering baseline, and nothing here changes that — no Woodpecker pipeline, no `web/package.json` dependency. Automated coverage stays **Vitest** (`mise run test-web`) and **pytest** (`mise run test-backend`).

## Prerequisite: the server and a browser

`mise install` fetches the pinned `chrome-devtools-mcp` (`mise.toml`'s `[tools]`). `mise exec` installs it on demand too, so a fresh clone normally needs nothing extra.

```
mise run browser-check              # server, cache, and what will be driven
mise run browser-check -- --launch  # also start it headless (catches sandbox breakage)
```

Installing one is an **operator** step — no task does it. `browser-check` prints the install command for the detected OS (`installHints()` in `scripts/lib/find-browser.mjs`), so it is not repeated here.

Detection prefers **Chrome** over **Chromium**, because `chrome-devtools-mcp` officially supports Google Chrome and Chrome for Testing only. Chromium works for navigate/snapshot/click/console/network; if a *performance trace* tool misbehaves, retry against Chrome before reporting it as a Mailroom bug. If **no** browser is found the launcher omits `--executablePath` and lets `chrome-devtools-mcp` resolve one itself — a slow first run beats a hard failure.

### Environment overrides

All optional; set them in your shell, not in `.mcp.json`.

| Variable | Effect |
| --- | --- |
| `MAILROOM_BROWSER_EXECUTABLE` | Explicit binary; beats detection. A path that is not executable fails loudly rather than being ignored. |
| `MAILROOM_BROWSER_URL` | Attach to an already-running debuggable browser instead of launching one. |
| `MAILROOM_BROWSER_HEADFUL=1` | Show the window (needs a display). |
| `MAILROOM_BROWSER_CHANNEL` | `stable` / `beta` / `dev` / `canary`. Lets `chrome-devtools-mcp` locate that channel's Chrome itself, so local detection is skipped — **mutually exclusive with `MAILROOM_BROWSER_EXECUTABLE`** (upstream refuses both and exits before the handshake; the launcher fails fast with a clear message instead). |
| `MAILROOM_BROWSER_CHROME_ARGS` | Space-separated extra Chrome flags, each passed as `--chromeArg` — e.g. `--no-sandbox` (see Troubleshooting). |
| `MAILROOM_BROWSER_IDLE_MINUTES` | How long the server may sit unused before it and its browser are stopped. Default `10`; `0` never stops it. |
| `MAILROOM_BROWSER_EAGER=1` | Start the server at session start instead of on first use. Debugging escape hatch. |

**Never point these at the user's everyday browser.** The default path (`--headless --isolated`, throwaway profile) cannot disturb it; the overrides can. The user may be working in their own window in parallel — driving it steals focus and can close tabs holding their work, and their clicks can corrupt your flow in return.

Telemetry is off by default: the launcher passes `--no-usage-statistics` and `--no-performance-crux`, and sets `CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS` (the pin is Renovate's job), so neither usage data nor visited dev-instance URLs leave the machine.

**The server is not running until you use it.** Claude Code starts every configured MCP server at session start, so the launcher answers the handshake from a cached copy and spawns the real server on your first browser tool call — a session that never verifies anything costs one small node process instead of ~170 MiB. After `MAILROOM_BROWSER_IDLE_MINUTES` without a call, the server and its browser are stopped again and the next call brings them back.

## Start the app

`mise run dev` (Postgres + API + Vite). Task detail: root [`README.md`](../../../README.md). If an instance is already up, reuse it and leave it running; stop only one you started yourself.

## The URL

**http://127.0.0.1:5180/** — Vite, with `/api` proxied to the API on `8100` (both from `mise.toml`'s `[env]`; a personal `.env` may override them, so check `MAILROOM_VITE_PORT` there first). Ports are fixed rather than per-worktree, so only **one** checkout's app can be up at a time: confirm the page you drive is this checkout's (`/api/v1/version` reports the git branch in a build, and the dev log shows which worktree started it).

Always drive the **Vite** origin, never the API port directly, so requests stay same-origin as in production.

There is no sign-in yet. When owner login lands, its dev credentials go here.

## Verify

A useful pass asserts something concrete, not just "the page rendered":

- `take_snapshot` after each navigation — assert the expected roles/labels are present, in the spirit of `web/README.md`'s guidance to test by accessible role and label.
- `list_console_messages` — **zero `error`-level entries** is the single best pass/fail signal.
- `list_network_requests` — confirm the calls you expect returned 2xx, and that they went same-origin through the Vite proxy.
- `take_screenshot` — an artefact for whoever reviews the change.

## Troubleshooting

**Server not connected / not listed.** `.mcp.json` is project-scoped, so Claude Code asks once per clone before using it, and the committed approval in `.claude/settings.json` only applies after the workspace itself is trusted. Check with `claude mcp get chrome-devtools`: `⏸ Pending approval` → run `claude` interactively once and accept; `✘ Rejected` → it is stuck in `disabledMcpjsonServers`, clear it with `claude mcp reset-project-choices`.

**A local-scope server shadows the project one.** `claude mcp add --scope local` outranks `.mcp.json`. A stale local entry keeps running an old pinned version and silently masks Renovate bumps — remove it rather than editing `.mcp.json` around it.

**Sandbox / user-namespace failure.** Common in containers and on hosts with user namespaces restricted. Pass the flag through to Chrome:

```
export MAILROOM_BROWSER_CHROME_ARGS=--no-sandbox
```

`MAILROOM_BROWSER_CHROME_ARGS` is space-separated and each entry becomes a `--chromeArg`. Prefer this over launching Chrome yourself and attaching with `MAILROOM_BROWSER_URL`: attaching means the server is not launching the browser, so `--isolated`, `--viewport`, and `--acceptInsecureCerts` no longer apply and you inherit whatever profile and window size that process has.

**Self-signed certificate errors.** The launcher already passes `--acceptInsecureCerts`.

**First run is slow or fails offline.** `mise install` fetches the pinned server. The first session after that (or after a version bump) starts it eagerly to record the handshake; every later session answers it from cache and starts nothing. `mise run browser-check` reports whether a handshake is cached.

**Stale element `uid`s after a pause.** The server stops after `MAILROOM_BROWSER_IDLE_MINUTES` idle, taking its browser with it, so `uid`s from an earlier snapshot no longer resolve. Navigate and `take_snapshot` again rather than reusing them; set `MAILROOM_BROWSER_IDLE_MINUTES=0` for a long session you want held open. Attached browsers (`MAILROOM_BROWSER_URL`) are only disconnected from, never closed.

**Windows** is untested. The launcher spawns the server through a shell on `win32` (mise installs a `.cmd` shim, and Node refuses to spawn a `.cmd` directly with `EINVAL`), and browser detection checks the usual `.exe` locations, but nobody has actually run it there — treat a Windows failure as expected-unknown rather than a regression.

## Changing this setup

The version pin lives in `mise.toml`'s `[tools]` as `"npm:chrome-devtools-mcp"`, tracked by Renovate's built-in `mise` manager and grouped by package name in `renovate.json` with the repo-wide 7-day `minimumReleaseAge` mirroring `web/pnpm-workspace.yaml`. If you move it again, make sure a manager still sees the new location in the same change or the pin silently stops being maintained. `docs/requirements/nonfunctional-requirements.md` lists the pin sites too.

The launcher's proxy (`scripts/lib/mcp-lazy-proxy.mjs`) is the only protocol-bearing code in the repository — a mistake there corrupts the MCP stream instead of failing loudly. `mise run check-scripts` covers its state machine; run it after touching either file. Per `AGENTS.md`'s update table, changes here also belong in `AGENTS.md` (Manual / browser-based testing) and the root `README.md` task table.
