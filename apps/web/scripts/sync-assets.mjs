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
writeFileSync(
  resolve(dst, "ATTRIBUTION.md"),
  "Assets copied from /assets (Smart Travel Assistant UI & Asset Pack). Source PNGs unchanged; see assets/README.md.\n",
);
console.log(`synced assets -> ${dst}`);
