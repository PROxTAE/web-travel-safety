# ADR-0001 — Pydantic models are the contract source of truth

- Status: Accepted (2026-09-17)
- Deciders: Team Lead, คน 2 (contracts maintainer), คน 1 (TypeScript consumer)

## Context

`00_API_AND_DATA_CONTRACTS.md §9` requires one source that produces both Python models and
TypeScript types, with CI failing when generated output drifts. Two options were considered:

1. Hand-written JSON Schema → `datamodel-code-generator` (Python) + `json-schema-to-typescript` (TS)
2. Pydantic v2 models → `model_json_schema()` (JSON Schema) → in-repo TypeScript emitter

## Decision

Option 2. `packages/contracts/sta_contracts/{enums,geo,models}.py` are authoritative.
`scripts/generate.py` writes `jsonschema/common/*.schema.json`, `jsonschema/common/common.schema.json`
(all `$defs` incl. enums) and `generated/typescript/contracts.ts`. The seven Python services depend on
`sta-contracts` directly (path dependency) so there is no second Python copy to drift.

## Consequences

- Validation rules (coordinate order, timezone-aware timestamps, enum whitelists, `extra="forbid"`)
  live once, in Python, and are exercised by `packages/contracts/tests`.
- The TypeScript emitter is small and covers the subset Pydantic emits (objects, enums, `anyOf` nullables,
  arrays, `Record`, literals). Anything outside that subset fails generation loudly.
- Breaking changes still require a version bump (`CONTRACT_VERSION`) and a `/v2` path per the delivery rules.
- Every real-sanitized example under `examples/real-sanitized/` must carry `_fixture.{source,captured_at,
  schema_version,license,redaction}` and is validated against both Pydantic and the JSON Schema in CI.
