"""Generate JSON Schema (jsonschema/common/*.schema.json) and TypeScript (generated/typescript/)
from the Pydantic source of truth. CI fails if running this changes the working tree.

Usage: uv run python scripts/generate.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sta_contracts import EXPORTED_MODELS, enums  # noqa: E402
from sta_contracts.enums import CONTRACT_VERSION  # noqa: E402

SCHEMA_DIR = ROOT / "jsonschema" / "common"
TS_DIR = ROOT / "generated" / "typescript"
PY_DIR = ROOT / "generated" / "python"

HEADER = "// GENERATED FILE — do not edit. Source: packages/contracts/sta_contracts (run `make contracts-generate`).\n"


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


# ------------------------------------------------------------------ JSON Schema
def write_json_schemas() -> dict[str, Any]:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    for old in SCHEMA_DIR.glob("*.schema.json"):
        old.unlink()
    combined_defs: dict[str, Any] = {}
    index: dict[str, str] = {}
    for model in EXPORTED_MODELS:
        schema = model.model_json_schema(mode="serialization")
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"https://smart-travel-assistant.local/schemas/{CONTRACT_VERSION}/{model.__name__}.schema.json"
        schema["x-contract-version"] = CONTRACT_VERSION
        for k, v in schema.get("$defs", {}).items():
            combined_defs.setdefault(k, v)
        combined_defs[model.__name__] = {k: v for k, v in schema.items() if k not in ("$defs", "$schema", "$id")}
        path = SCHEMA_DIR / f"{_snake(model.__name__)}.schema.json"
        _write_json(path, schema)
        index[model.__name__] = path.name
    enum_defs = {}
    for name in dir(enums):
        obj = getattr(enums, name)
        if isinstance(obj, type) and issubclass(obj, enums.StrEnum) and obj is not enums.StrEnum:
            enum_defs[name] = {"type": "string", "enum": [m.value for m in obj], "title": name}
    combined = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://smart-travel-assistant.local/schemas/{CONTRACT_VERSION}/common.schema.json",
        "title": "Smart Travel Assistant common contracts",
        "x-contract-version": CONTRACT_VERSION,
        "$defs": {**enum_defs, **combined_defs},
    }
    _write_json(SCHEMA_DIR / "common.schema.json", combined)
    _write_json(SCHEMA_DIR / "index.json", index)
    return combined


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


# ------------------------------------------------------------------ TypeScript
class TsEmitter:
    def __init__(self, defs: dict[str, Any]) -> None:
        self.defs = defs
        self.out: list[str] = [HEADER, f'export const CONTRACT_VERSION = "{CONTRACT_VERSION}" as const;', ""]

    def ref_name(self, ref: str) -> str:
        return ref.rsplit("/", 1)[-1]

    def ts_type(self, schema: dict[str, Any]) -> str:
        if "$ref" in schema:
            return self.ref_name(schema["$ref"])
        if "const" in schema:
            return json.dumps(schema["const"])
        if "enum" in schema:
            return " | ".join(json.dumps(v) for v in schema["enum"])
        if "anyOf" in schema:
            return " | ".join(self.ts_type(s) for s in schema["anyOf"])
        if "oneOf" in schema:
            return " | ".join(self.ts_type(s) for s in schema["oneOf"])
        if "allOf" in schema and len(schema["allOf"]) == 1:
            return self.ts_type(schema["allOf"][0])
        t = schema.get("type")
        if isinstance(t, list):
            return " | ".join(self.ts_type({**schema, "type": x}) for x in t)
        if t == "string":
            return "string"
        if t in ("integer", "number"):
            return "number"
        if t == "boolean":
            return "boolean"
        if t == "null":
            return "null"
        if t == "array":
            items = schema.get("items")
            if isinstance(items, dict):
                inner = self.ts_type(items)
                return f"Array<{inner}>" if "|" in inner else f"{inner}[]"
            if "prefixItems" in schema:
                return "[" + ", ".join(self.ts_type(s) for s in schema["prefixItems"]) + "]"
            return "unknown[]"
        if t == "object" or "properties" in schema:
            if "properties" in schema:
                return self.inline_object(schema)
            ap = schema.get("additionalProperties")
            if isinstance(ap, dict):
                return f"Record<string, {self.ts_type(ap)}>"
            return "Record<string, unknown>"
        return "unknown"

    def inline_object(self, schema: dict[str, Any]) -> str:
        required = set(schema.get("required", []))
        parts = []
        for name, prop in schema.get("properties", {}).items():
            opt = "" if name in required else "?"
            parts.append(f"{name}{opt}: {self.ts_type(prop)}")
        return "{ " + "; ".join(parts) + " }"

    def emit_def(self, name: str, schema: dict[str, Any]) -> None:
        desc = schema.get("description")
        if desc:
            self.out.append("/** " + desc.replace("\n", " ") + " */")
        if "enum" in schema and schema.get("type") == "string":
            self.out.append(f"export type {name} = " + " | ".join(json.dumps(v) for v in schema["enum"]) + ";")
            self.out.append(
                f"export const {name}Values = [" + ", ".join(json.dumps(v) for v in schema["enum"]) + "] as const;"
            )
            self.out.append("")
            return
        if "properties" in schema:
            required = set(schema.get("required", []))
            self.out.append(f"export interface {name} {{")
            for pname, prop in schema["properties"].items():
                pdesc = prop.get("description")
                if pdesc:
                    self.out.append("  /** " + pdesc.replace("\n", " ") + " */")
                opt = "" if pname in required else "?"
                self.out.append(f"  {pname}{opt}: {self.ts_type(prop)};")
            self.out.append("}")
            self.out.append("")
            return
        self.out.append(f"export type {name} = {self.ts_type(schema)};")
        self.out.append("")

    def render(self) -> str:
        for name in sorted(self.defs):
            self.emit_def(name, self.defs[name])
        return "\n".join(self.out) + "\n"


def write_typescript(combined: dict[str, Any]) -> None:
    TS_DIR.mkdir(parents=True, exist_ok=True)
    body = TsEmitter(combined["$defs"]).render()
    (TS_DIR / "contracts.ts").write_text(body, encoding="utf-8", newline="\n")
    (TS_DIR / "index.ts").write_text(HEADER + 'export * from "./contracts";\n', encoding="utf-8", newline="\n")


def write_python_index() -> None:
    PY_DIR.mkdir(parents=True, exist_ok=True)
    (PY_DIR / "README.md").write_text(
        "# Python contracts\n\nPython services import the source package directly:\n"
        "`from sta_contracts.models import ...`.\n"
        "No generated copy is needed because the Pydantic models *are* the source of truth (ADR-0001).\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    combined = write_json_schemas()
    write_typescript(combined)
    write_python_index()
    print(f"generated {len(EXPORTED_MODELS)} schemas + typescript ({CONTRACT_VERSION})")
