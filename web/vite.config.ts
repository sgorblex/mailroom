import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv, type PluginOption } from "vite";

/** Missing, empty, or whitespace-only values are treated as unset (defaults apply). */
function trimmedNonEmptyEnv(env: Record<string, string>, key: string): string | undefined {
  const value = env[key];
  if (value === undefined) {
    return undefined;
  }
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed;
}

function portFromEnv(env: Record<string, string>, key: string, fallback: number): number {
  const parsed = Number.parseInt(trimmedNonEmptyEnv(env, key) ?? "", 10);
  return Number.isNaN(parsed) ? fallback : parsed;
}

function gitValue(args: string): string {
  try {
    return execSync(`git ${args}`, {
      cwd: path.resolve(import.meta.dirname, ".."),
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  } catch {
    return "";
  }
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const isTest = mode === "test";
  const env = loadEnv(mode, path.resolve(import.meta.dirname), "VITE_");
  // The repository root's `.env` and mise's [env] carry the dev ports, shared with dev-api.
  const rootEnv = {
    ...(process.env as Record<string, string>),
    ...loadEnv(mode, path.resolve(import.meta.dirname, ".."), ""),
  };
  const bindHostRaw = trimmedNonEmptyEnv(rootEnv, "MAILROOM_API_HOST") ?? "127.0.0.1";
  const bindHost = bindHostRaw === "0.0.0.0" ? true : bindHostRaw;
  const apiHost = bindHostRaw === "0.0.0.0" ? "127.0.0.1" : bindHostRaw;
  const apiPort = portFromEnv(rootEnv, "MAILROOM_API_PORT", 8100);
  const vitePort = portFromEnv(rootEnv, "MAILROOM_VITE_PORT", 5180);
  const apiTarget = `http://${apiHost}:${String(apiPort)}`;

  const webPackage = JSON.parse(
    readFileSync(path.resolve(import.meta.dirname, "package.json"), "utf8"),
  ) as { version?: string };
  const appSemver = (webPackage.version ?? "0.0.0").trim() || "0.0.0";

  const gitSha =
    trimmedNonEmptyEnv(env, "VITE_GIT_SHA") ?? (isTest ? "" : gitValue("rev-parse HEAD"));
  const gitBranch =
    trimmedNonEmptyEnv(env, "VITE_GIT_BRANCH") ??
    (isTest ? "" : gitValue("rev-parse --abbrev-ref HEAD"));
  const deployEnvRaw = (env["VITE_MAILROOM_DEPLOY_ENV"] ?? "").trim().toLowerCase();
  const deployEnv = ["staging", "development", "production"].includes(deployEnvRaw)
    ? deployEnvRaw
    : "";

  const plugins: PluginOption[] = [react()];
  if (!isTest) {
    plugins.push(tailwindcss());
  }

  return {
    define: {
      "import.meta.env.VITE_APP_SEMVER": JSON.stringify(appSemver),
      "import.meta.env.VITE_GIT_SHA": JSON.stringify(gitSha),
      "import.meta.env.VITE_GIT_BRANCH": JSON.stringify(gitBranch),
      "import.meta.env.VITE_MAILROOM_DEPLOY_ENV": JSON.stringify(deployEnv),
    },
    plugins,
    resolve: {
      alias: {
        "@": path.resolve(import.meta.dirname, "./src"),
      },
    },
    server: {
      host: bindHost,
      port: vitePort,
      strictPort: true,
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: true,
        },
        // The MCP server and the built-in OAuth server, as the web container's nginx routes them.
        // The browser's Host is kept: /mcp answers only MAILROOM_PUBLIC_URL's host.
        "^/mcp(?:\\?.*)?$": { target: apiTarget },
        "/oauth/": { target: apiTarget },
        // OpenID discovery too: a client probes it for the OAuth server, and the API's 404 is the
        // answer, not the SPA.
        "^/\\.well-known/(?:oauth-|openid-configuration)": { target: apiTarget },
      },
    },
  };
});
