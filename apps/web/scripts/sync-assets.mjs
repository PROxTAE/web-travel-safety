// Copies the approved asset pack into public/assets (source PNGs are never modified; Next Image optimizes at runtime).
import { cpSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, "../../../assets");
const dst = resolve(here, "../public/assets");
mkdirSync(dst, { recursive: true });
for (const dir of ["branding", "icons", "illustrations", "mascot"]) {
  cpSync(resolve(src, dir), resolve(dst, dir), { recursive: true });
}
// MapLibre's ESM tile worker (+ its shared chunk) must be served as static modules: the bundler rewrites the
// `new URL(..., import.meta.url)` worker reference to a page route that returns HTML.
mkdirSync(resolve(here, "../public/maplibre"), { recursive: true });
for (const f of ["maplibre-gl.mjs", "maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"]) {
  cpSync(resolve(here, "../node_modules/maplibre-gl/dist", f), resolve(here, "../public/maplibre", f));
}
writeFileSync(
  resolve(dst, "ATTRIBUTION.md"),
  "Assets copied from /assets (Smart Travel Assistant UI & Asset Pack). Source PNGs unchanged; see assets/README.md.\n",
);
console.log(`synced assets -> ${dst}`);
