import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

/**
 * A stdio MCP proxy that keeps the real server out of memory until it is used.
 *
 * Claude Code starts every configured stdio server at session start, so a
 * session that never touches a browser still pays for a resident node process
 * holding a bundled puppeteer-core. The handshake is the only part the client
 * needs immediately, and it is identical for a given (server version, protocol
 * version, argv) triple — so it is cached on disk, replayed from here, and the
 * child is spawned on the first request that actually needs it.
 *
 * The same machinery bounds lifetime in the other direction: chrome-devtools-mcp
 * has no idle-timeout option, but a proxy that already sees every message can
 * reap a child that has gone quiet. Killing it closes the browser with it.
 */

/** Methods answerable from the cached handshake, so they never spawn a child. */
const CACHEABLE_LIST_METHODS = new Set([
  "tools/list",
  "resources/list",
  "resources/templates/list",
  "prompts/list",
]);

const CACHE_SHAPE_VERSION = 1;

export function handshakeCacheDirectory(environment = process.env) {
  const base = environment.XDG_CACHE_HOME?.trim() || path.join(os.homedir(), ".cache");
  return path.join(base, "mailroom", "browser-mcp");
}

/**
 * Keyed on everything that can change the handshake: the exact server install
 * and its argv, since `--slim` and the `--category*` flags change the tool
 * list. A stale key is a miss, which costs one eager start and nothing else.
 */
export function handshakeCacheKey({ serverIdentity, serverArgs }) {
  const material = JSON.stringify([CACHE_SHAPE_VERSION, serverIdentity, serverArgs]);
  return crypto.createHash("sha256").update(material).digest("hex").slice(0, 32);
}

function handshakeCachePath(key, environment = process.env) {
  return path.join(handshakeCacheDirectory(environment), `${key}.json`);
}

function readHandshakeCache(key, environment = process.env) {
  try {
    const parsed = JSON.parse(fs.readFileSync(handshakeCachePath(key, environment), "utf8"));
    // A half-written or older-shaped file is a miss, not an error: the only
    // cost of being wrong here is one eager start.
    if (parsed?.key !== key || parsed.initializeResult === undefined) {
      return undefined;
    }
    return parsed;
  } catch {
    return undefined;
  }
}

function writeHandshakeCache(key, payload, environment = process.env) {
  const target = handshakeCachePath(key, environment);
  try {
    fs.mkdirSync(path.dirname(target), { recursive: true });
    // Written via rename so a concurrent reader never sees a partial file —
    // several worktree sessions share this cache.
    const temporary = `${target}.${process.pid}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify({ key, ...payload }), "utf8");
    fs.renameSync(temporary, target);
  } catch {
    // A cache that cannot be written costs a spawn, never correctness.
  }
}

function jsonRpcResult(id, result) {
  return { jsonrpc: "2.0", id, result };
}

function jsonRpcError(id, code, message) {
  return { jsonrpc: "2.0", id, error: { code, message } };
}

function isRequest(message) {
  return message.method !== undefined && message.id !== undefined && message.id !== null;
}

/**
 * Split a byte stream into newline-delimited JSON messages — MCP stdio framing
 * is one JSON value per line.
 */
function createLineReader(onLine) {
  let buffer = "";
  return (chunk) => {
    buffer += chunk;
    let newline = buffer.indexOf("\n");
    while (newline !== -1) {
      const line = buffer.slice(0, newline);
      buffer = buffer.slice(newline + 1);
      if (line.trim().length > 0) {
        onLine(line);
      }
      newline = buffer.indexOf("\n");
    }
  };
}

/**
 * @param {object} options
 * @param {() => import("node:child_process").ChildProcess} options.spawnChild
 * @param {string} options.cacheKey
 * @param {number} options.idleMs 0 disables reaping.
 * @param {boolean} options.eager Ignore the cache and start the server at once.
 * @param {(message: string) => void} options.warn Must write to stderr only.
 */
export function runLazyProxy({
  spawnChild,
  cacheKey,
  idleMs = 0,
  eager = false,
  warn,
  stdin = process.stdin,
  stdout = process.stdout,
  environment = process.env,
  onExit = (code) => process.exit(code),
  registerSignalHandlers = true,
  now = () => Date.now(),
}) {
  const cached = eager ? undefined : readHandshakeCache(cacheKey, environment);
  /** Only for messages: a sub-minute timeout should not be reported as "0 min". */
  const idleLabel = `${Number((idleMs / 60_000).toFixed(1))} min`;

  let child;
  /** Client lines received while a child is starting, replayed once it is up. */
  let pending = [];
  /** The client's own `initialize` params, replayed to every child we spawn. */
  let initializeParams;
  let clientInitialized = false;
  let lastActivity = now();
  let starting = false;
  let shuttingDown = false;
  let reapedDeliberately = false;
  /** Request ids the client is still waiting on, so a reap cannot strand one. */
  const inFlight = new Set();
  /** Snooped during an uncached run, so the next session starts warm. */
  const observed = { initializeResult: undefined, lists: {} };
  let cacheWritten = cached !== undefined;
  /** Set by the running child; maps a client request id to the field it fills. */
  let snoop = () => {};

  /** Our own signal handlers, so a re-raise does not re-enter them. */
  const signalHandlers = new Map();

  /**
   * Exit under the signal that killed the child. Our handler must come off
   * first: re-raising into it would call this again, forever.
   */
  function reraise(signal) {
    const handler = signalHandlers.get(signal);
    if (handler !== undefined) {
      process.off(signal, handler);
      signalHandlers.delete(signal);
    }
    process.kill(process.pid, signal);
  }

  function send(message) {
    stdout.write(`${JSON.stringify(message)}\n`);
  }

  function toChild(line) {
    child.stdin.write(`${line}\n`);
  }

  function maybeWriteCache() {
    // Written as soon as `initialize` and `tools/list` are known: the other
    // lists are optional, and a client that never asks for them should still
    // leave a usable cache behind.
    if (
      cacheWritten ||
      observed.initializeResult === undefined ||
      observed.lists["tools/list"] === undefined
    ) {
      return;
    }
    cacheWritten = true;
    writeHandshakeCache(
      cacheKey,
      { initializeResult: observed.initializeResult, lists: observed.lists },
      environment,
    );
  }

  function startChild() {
    starting = true;
    const started = spawnChild();
    child = started;
    const handshakeId = `mailroom-proxy-initialize-${process.pid}-${Date.now()}`;
    const needsHandshakeReplay = initializeParams !== undefined;
    /** Client request ids whose replies should be recorded for the cache. */
    const snooped = new Map();

    snoop = (message) => {
      if (cacheWritten || !isRequest(message)) {
        return;
      }
      if (message.method === "initialize") {
        snooped.set(message.id, "initialize");
      } else if (CACHEABLE_LIST_METHODS.has(message.method)) {
        snooped.set(message.id, message.method);
      }
    };

    started.stdout.setEncoding("utf8");
    started.stdout.on(
      "data",
      createLineReader((line) => {
        let message;
        try {
          message = JSON.parse(line);
        } catch {
          // Not ours to interpret — hand it to the client unchanged.
          stdout.write(`${line}\n`);
          return;
        }
        if (message.id !== undefined && message.id !== null) {
          if (message.id === handshakeId) {
            // Our own replayed handshake: the client already holds the cached
            // result, and answering a settled id twice would corrupt its state.
            if (clientInitialized) {
              toChild(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }));
            }
            starting = false;
            flushPending();
            return;
          }
          inFlight.delete(message.id);
          const field = snooped.get(message.id);
          if (field !== undefined) {
            snooped.delete(message.id);
            if (message.result !== undefined) {
              if (field === "initialize") {
                observed.initializeResult = message.result;
              } else {
                observed.lists[field] = message.result;
              }
              maybeWriteCache();
            }
          }
        }
        send(message);
      }),
    );

    started.on("error", (error) => {
      warn(`failed to start the browser MCP server: ${error.message}`);
      onExit(1);
    });

    started.on("exit", (code, signal) => {
      child = undefined;
      starting = false;
      snoop = () => {};
      if (shuttingDown) {
        if (signal !== null) {
          reraise(signal);
          return;
        }
        onExit(code ?? 1);
        return;
      }
      if (reapedDeliberately) {
        reapedDeliberately = false;
        return;
      }
      // An unexpected death must not leave the client waiting forever: answer
      // everything outstanding, then fall back to cold so the next call retries.
      warn(
        `server exited unexpectedly (${signal ?? `code ${code}`}) — restarting on the next browser call`,
      );
      for (const id of inFlight) {
        send(jsonRpcError(id, -32000, "browser MCP server exited; retry the call"));
      }
      inFlight.clear();
      pending = [];
    });

    if (!needsHandshakeReplay) {
      // No cached handshake to replay: the client's own is still to come, so
      // just wire the streams together and let it flow.
      starting = false;
      flushPending();
      return;
    }

    toChild(
      JSON.stringify({
        jsonrpc: "2.0",
        id: handshakeId,
        method: "initialize",
        params: initializeParams,
      }),
    );
  }

  function flushPending() {
    const queued = pending;
    pending = [];
    for (const line of queued) {
      try {
        snoop(JSON.parse(line));
      } catch {
        // Unparseable lines are forwarded verbatim; nothing to snoop.
      }
      toChild(line);
    }
  }

  function reap() {
    if (child === undefined || starting || inFlight.size > 0) {
      return;
    }
    if (now() - lastActivity < idleMs) {
      return;
    }
    warn(`idle for ${idleLabel} — stopping the server and its browser`);
    reapedDeliberately = true;
    // SIGTERM, not SIGKILL: upstream's handler runs closeBrowser(), which shuts
    // Chrome down cleanly and removes the --isolated temporary profile.
    child.kill("SIGTERM");
  }

  function forward(line, message) {
    // A ping is the client holding the connection open, not using the browser.
    // Counting it would keep a live server alive for as long as the session is,
    // which is what the reaper exists to prevent.
    if (message.method !== "ping") {
      lastActivity = now();
    }
    if (isRequest(message)) {
      inFlight.add(message.id);
    }
    if (child === undefined) {
      pending.push(line);
      startChild();
      return;
    }
    if (starting) {
      pending.push(line);
      return;
    }
    snoop(message);
    toChild(line);
  }

  function handleClientLine(line) {
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      forward(line, {});
      return;
    }

    if (message.method === "initialize") {
      // Kept for replay even on a cache hit: every child we ever spawn needs it.
      initializeParams = message.params;
      if (cached !== undefined) {
        send(jsonRpcResult(message.id, cached.initializeResult));
        return;
      }
      forward(line, message);
      return;
    }

    if (message.method === "notifications/initialized") {
      clientInitialized = true;
      if (child === undefined) {
        // Held back and replayed against whichever child starts first.
        return;
      }
    }

    if (child === undefined && cached !== undefined) {
      if (message.method === "ping") {
        send(jsonRpcResult(message.id, {}));
        return;
      }
      const cachedList = cached.lists?.[message.method];
      if (cachedList !== undefined) {
        send(jsonRpcResult(message.id, cachedList));
        return;
      }
    }

    forward(line, message);
  }

  if (idleMs > 0) {
    // Unref'd so the reaper alone never keeps this process alive.
    setInterval(reap, Math.max(15_000, Math.min(idleMs, 60_000))).unref();
  }

  stdin.setEncoding("utf8");
  stdin.on("data", createLineReader(handleClientLine));
  stdin.on("end", () => {
    shuttingDown = true;
    if (child === undefined) {
      onExit(0);
      return;
    }
    child.kill("SIGTERM");
  });

  for (const signal of registerSignalHandlers ? ["SIGINT", "SIGTERM"] : []) {
    const handler = () => {
      shuttingDown = true;
      if (child === undefined) {
        reraise(signal);
        return;
      }
      child.kill(signal);
    };
    signalHandlers.set(signal, handler);
    process.on(signal, handler);
  }

  if (cached === undefined) {
    warn(
      eager
        ? "MAILROOM_BROWSER_EAGER=1 — starting the server now"
        : "no cached handshake yet — starting the server now to record one",
    );
    startChild();
  } else if (idleMs > 0) {
    warn(`server starts on first browser use, stops after ${idleLabel} idle`);
  } else {
    warn("server starts on first browser use (idle stop disabled)");
  }

  return {
    /** For tests: run the idle check now instead of waiting for the timer. */
    reapNow: reap,
    isRunning: () => child !== undefined,
  };
}
