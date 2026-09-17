// Copies the generated TypeScript contract types from packages/contracts (source of truth). Do not edit the output.
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, "../../../packages/contracts/generated/typescript/contracts.ts");
const dst = resolve(here, "../lib/api/generated/contracts.ts");
mkdirSync(dirname(dst), { recursive: true });
copyFileSync(src, dst);
console.log(`copied ${src} -> ${dst}`);
