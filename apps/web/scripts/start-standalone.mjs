// Runs the standalone production server the same way the Docker image does (copies static + public assets first).
import { spawn } from "node:child_process";
import { cpSync, existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(process.cwd());
// local runs: load .env.local (gitignored) the way `next start` would; containers get env from compose instead
const envFile = resolve(root, ".env.local");
if (existsSync(envFile)) {
  for (const line of readFileSync(envFile, "utf8").split(/\r?\n/)) {
    const m = /^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/.exec(line);
    if (m && process.env[m[1]] === undefined) process.env[m[1]] = m[2].replace(/^"(.*)"$/, "$1");
  }
}
const standalone = resolve(root, ".next/standalone");
if (!existsSync(standalone)) {
  console.error("run `next build` first (output: standalone)");
  process.exit(1);
}
// find the app dir inside standalone (pnpm workspaces nest it under apps/web)
const appDir = existsSync(resolve(standalone, "apps/web/server.js")) ? resolve(standalone, "apps/web") : standalone;
cpSync(resolve(root, ".next/static"), resolve(appDir, ".next/static"), { recursive: true });
cpSync(resolve(root, "public"), resolve(appDir, "public"), { recursive: true });
const child = spawn(process.execPath, [resolve(appDir, "server.js")], {
  stdio: "inherit",
  env: { ...process.env, PORT: process.env.PORT ?? "3000", HOSTNAME: process.env.HOSTNAME ?? "0.0.0.0" },
});
child.on("exit", (code) => process.exit(code ?? 0));
