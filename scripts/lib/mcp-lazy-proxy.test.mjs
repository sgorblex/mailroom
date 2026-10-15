import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { handshakeCacheKey, runLazyProxy } from "./mcp-lazy-proxy.mjs";

/**
 * The proxy is the only protocol-bearing code in this repository, and a mistake
 * in it corrupts the MCP stream rather than failing loudly, so the state
 * machine is driven here against a stub child rather than a real server.
 */

const INITIALIZE_RESULT = {
  protocolVersion: "2025-06-18",
  capabilities: { tools: {} },
  serverInfo: { name: "chrome-devtools-mcp", version: "1.7.0" },
};
const TOOLS_RESULT = { tools: [{ name: "take_snapshot", inputSchema: { type: "object" } }] };
const CACHE_KEY = handshakeCacheKey({ serverIdentity: "1.7.0", serverArgs: ["--headless"] });

/** Minimal stand-in for a spawned chrome-devtools-mcp: answers what it is asked. */
function createStubChild() {
  const child = new EventEmitter();
  child.received = [];
  child.killed = undefined;
  child.stdout = new EventEmitter();
  child.stdout.setEncoding = () => {};
  child.reply = (id, result) => {
    child.stdout.emit("data", `${JSON.stringify({ jsonrpc: "2.0", id, result })}\n`);
  };
  child.stdin = {
    write: (line) => {
      const message = JSON.parse(line);
      child.received.push(message);
      if (message.method === "initialize") {
        child.reply(message.id, INITIALIZE_RESULT);
      } else if (message.method === "tools/list") {
        child.reply(message.id, TOOLS_RESULT);
      }
    },
  };
  child.kill = (signal) => {
    child.killed = signal;
    child.emit("exit", null, signal);
  };
  return child;
}

function createProxy(cacheHome, { idleMs = 0, eager = false } = {}) {
  let clock = 1_000_000;
  const stdin = new EventEmitter();
  stdin.setEncoding = () => {};
  const sent = [];
  const children = [];
  const exits = [];

  const proxy = runLazyProxy({
    spawnChild: () => {
      const child = createStubChild();
      children.push(child);
      return child;
    },
    cacheKey: CACHE_KEY,
    idleMs,
    eager,
    warn: () => {},
    stdin,
    stdout: { write: (line) => sent.push(JSON.parse(line)) },
    environment: { XDG_CACHE_HOME: cacheHome },
    onExit: (code) => exits.push(code),
    registerSignalHandlers: false,
    now: () => clock,
  });

  const harness = {
    proxy,
    children,
    sent,
    exits,
    send: (message) => stdin.emit("data", `${JSON.stringify(message)}\n`),
    idle: (ms) => {
      clock += ms;
    },
    replyTo: (id) => sent.find((message) => message.id === id),
    endStdin: () => stdin.emit("end"),
  };
  /** Walks the client through the handshake — cold or warm, the client cannot tell. */
  harness.handshake = () => {
    harness.send({
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: { protocolVersion: "2025-06-18" },
    });
    harness.send({ jsonrpc: "2.0", method: "notifications/initialized" });
    harness.send({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} });
  };
  return harness;
}

function cacheHome(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "mailroom-mcp-proxy-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  return directory;
}

/** A cache directory left warm by one completed handshake. */
function warmCacheHome(t) {
  const directory = cacheHome(t);
  createProxy(directory).handshake();
  return directory;
}

test("an uncached run starts the server and records the handshake", (t) => {
  const harness = createProxy(cacheHome(t));

  assert.equal(harness.children.length, 1, "spawns immediately when there is no cache");
  harness.handshake();

  assert.deepEqual(harness.replyTo(1).result, INITIALIZE_RESULT);
  assert.deepEqual(harness.replyTo(2).result, TOOLS_RESULT);
});

test("a cached run answers the handshake without spawning anything", (t) => {
  const harness = createProxy(warmCacheHome(t));
  harness.handshake();

  assert.equal(harness.children.length, 0, "no child for initialize or tools/list");
  assert.deepEqual(harness.replyTo(1).result, INITIALIZE_RESULT);
  assert.deepEqual(harness.replyTo(2).result, TOOLS_RESULT);
});

test("a running server is kept alive by a tool call, not by a ping", (t) => {
  const harness = createProxy(warmCacheHome(t), { idleMs: 60_000 });
  harness.handshake();
  harness.send({ jsonrpc: "2.0", id: 10, method: "tools/call", params: {} });
  harness.children[0].reply(10, { content: [] });

  harness.idle(59_000);
  harness.send({ jsonrpc: "2.0", id: 11, method: "ping" });
  harness.children[0].reply(11, {});
  harness.idle(2_000);

  // A client that pings on a keepalive shorter than the timeout would otherwise
  // hold the server and its browser open for the whole session.
  harness.proxy.reapNow();
  assert.equal(harness.proxy.isRunning(), false);
});

test("a ping is answered cold and does not defer the reaper", (t) => {
  const harness = createProxy(warmCacheHome(t), { idleMs: 60_000 });
  harness.handshake();

  harness.send({ jsonrpc: "2.0", id: 3, method: "ping" });

  assert.deepEqual(harness.replyTo(3).result, {});
  assert.equal(harness.children.length, 0);
});

test("the first tool call spawns once and replays the handshake", (t) => {
  const harness = createProxy(warmCacheHome(t));
  harness.handshake();

  harness.send({ jsonrpc: "2.0", id: 4, method: "tools/call", params: { name: "take_snapshot" } });

  assert.equal(harness.children.length, 1);
  const child = harness.children[0];
  assert.deepEqual(
    child.received.map((message) => message.method),
    ["initialize", "notifications/initialized", "tools/call"],
  );
  // The child's own initialize result must never reach a client that already
  // had id 1 answered from cache.
  assert.equal(harness.sent.filter((message) => message.id === 1).length, 1);

  child.reply(4, { content: [] });
  assert.deepEqual(harness.replyTo(4).result, { content: [] });
});

test("an idle server is stopped and restarted on the next call", (t) => {
  const harness = createProxy(warmCacheHome(t), { idleMs: 60_000 });
  harness.handshake();
  harness.send({ jsonrpc: "2.0", id: 5, method: "tools/call", params: {} });
  harness.children[0].reply(5, { content: [] });

  harness.idle(60_000);
  harness.proxy.reapNow();

  assert.equal(harness.children[0].killed, "SIGTERM", "SIGTERM lets the browser close cleanly");
  assert.equal(harness.proxy.isRunning(), false);

  harness.send({ jsonrpc: "2.0", id: 6, method: "tools/call", params: {} });
  assert.equal(harness.children.length, 2, "the next call brings it back");
  harness.children[1].reply(6, { content: [] });
  assert.deepEqual(harness.replyTo(6).result, { content: [] });
});

test("a call in flight blocks the reaper", (t) => {
  const harness = createProxy(warmCacheHome(t), { idleMs: 60_000 });
  harness.handshake();
  harness.send({ jsonrpc: "2.0", id: 7, method: "tools/call", params: {} });

  harness.idle(60_000);
  harness.proxy.reapNow();
  assert.equal(harness.proxy.isRunning(), true, "never reap with an unanswered request");

  harness.children[0].reply(7, { content: [] });
  harness.proxy.reapNow();
  assert.equal(harness.proxy.isRunning(), false);
});

test("an unexpected exit answers in-flight requests instead of hanging", (t) => {
  const harness = createProxy(warmCacheHome(t));
  harness.handshake();
  harness.send({ jsonrpc: "2.0", id: 8, method: "tools/call", params: {} });

  harness.children[0].emit("exit", 1, null);

  assert.equal(harness.replyTo(8).error.code, -32000);
  harness.send({ jsonrpc: "2.0", id: 9, method: "tools/call", params: {} });
  assert.equal(harness.children.length, 2, "recovers on the next call");
});

test("MAILROOM_BROWSER_EAGER ignores a warm cache", (t) => {
  const harness = createProxy(warmCacheHome(t), { eager: true });

  assert.equal(harness.children.length, 1);
});

test("closed stdin exits cleanly with no child running", (t) => {
  const harness = createProxy(warmCacheHome(t));
  harness.handshake();

  harness.endStdin();

  assert.deepEqual(harness.exits, [0]);
});
