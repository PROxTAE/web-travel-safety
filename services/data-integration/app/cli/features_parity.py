"""Online/offline feature parity + batch feature export (05 plan Phase 6).

Usage:
  uv run python -m app.cli.features_parity --snapshots DIR_OR_FILE [--out features.parquet]

Re-runs ``build_snapshot`` from the stored (request, external_context) pairs and compares the recomputed
feature vector with the stored one bitwise (tolerance 1e-9). Exports a Polars frame so the training
pipeline (คน 6) consumes exactly the online features.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import polars as pl
from sta_contracts.models import ExternalContext, IntegratedTravelContext, TravelRequest

from app.domain.features import feature_names, load_schema
from app.pipeline.build_snapshot import build_snapshot
from app.settings import get_settings


def _load(path: Path) -> list[dict]:  # type: ignore[type-arg]
    files = [path] if path.is_file() else sorted(path.glob("*.json"))
    return [json.loads(p.read_text(encoding="utf-8")) for p in files]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshots", type=Path, required=True, help="JSON with {request, external_context, snapshot}")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--tolerance", type=float, default=1e-9)
    args = ap.parse_args()
    s = get_settings()
    schema = load_schema(s.feature_schema_path)
    names = feature_names(schema)
    rows = []
    mismatches = 0
    for item in _load(args.snapshots):
        req = TravelRequest.model_validate(item["request"])
        ctx = ExternalContext.model_validate(item["external_context"])
        stored = IntegratedTravelContext.model_validate(item["snapshot"])
        rebuilt = build_snapshot(req, ctx, s, schema, now=stored.created_at).snapshot
        for k in rebuilt.features:
            a, b = rebuilt.features.get(k), stored.features.get(k)
            if a is None and b is None:
                continue
            if a is None or b is None or abs(float(a) - float(b)) > args.tolerance:
                mismatches += 1
                print(f"MISMATCH {stored.snapshot_id} {k}: online={b} offline={a}", file=sys.stderr)
        rows.append(
            {
                "snapshot_id": str(stored.snapshot_id),
                "created_at": stored.created_at,
                **{k: stored.features.get(k) for k in names},
            }
        )
    if rows:
        df = pl.DataFrame(rows)
        if args.out:
            df.write_parquet(args.out)
        print(df.describe())
    print(f"snapshots={len(rows)} feature_mismatches={mismatches} feature_schema={schema['version']}")
    sys.exit(1 if mismatches else 0)


if __name__ == "__main__":
    main()
