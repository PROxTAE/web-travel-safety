// Fails the production build if any runtime mock switch, service worker or sample payload leaks into app code.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

const roots = ["app", "components", "features", "lib"].map((d) => resolve(process.cwd(), d));
const banned = [/NEXT_PUBLIC_USE_MOCKS/, /msw\/browser/, /setupWorker\(/, /mockServiceWorker/, /sample-payload/i, /fixtures\//];
const hits = [];
function walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p);
    else if (/\.(ts|tsx|js|mjs)$/.test(name)) {
      const text = readFileSync(p, "utf8");
      for (const re of banned) if (re.test(text)) hits.push(`${p}: ${re}`);
    }
  }
}
for (const r of roots) walk(r);
if (hits.length) {
  console.error("runtime mock artefacts found:\n" + hits.join("\n"));
  process.exit(1);
}
console.log("no runtime mocks in app code");
