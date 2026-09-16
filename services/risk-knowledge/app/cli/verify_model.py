"""Verify the active model artifact (checksum, stage, feature schema, acceptance metrics) or roll back.

Usage:
  uv run python -m app.cli.verify_model
  uv run python -m app.cli.verify_model --rollback 1.0.0     # repoint artifacts/current to artifacts/1.0.0
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import yaml

from app.main import feature_schema_version
from app.risk.model_loader import ModelUnavailableError, load_active_model
from app.settings import get_settings


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rollback", type=str, default=None)
    args = ap.parse_args()
    s = get_settings()
    base = Path(s.risk_model_artifact_dir)
    if args.rollback:
        src = base / args.rollback
        if not (src / "manifest.json").exists():
            print(json.dumps({"error": f"no artifact version {args.rollback}"}))
            sys.exit(1)
        cur = base / "current"
        if cur.exists():
            shutil.rmtree(cur)
        shutil.copytree(src, cur)
        print(json.dumps({"rolled_back_to": args.rollback}))
    fsv = feature_schema_version(s.feature_schema_path)
    try:
        m = load_active_model(base, expected_feature_schema=fsv)
    except ModelUnavailableError as exc:
        print(json.dumps({"status": "UNAVAILABLE", "reason": exc.reason, "fallback": "rule-baseline"}))
        sys.exit(1)
    acceptance = yaml.safe_load(Path(s.model_acceptance_path).read_text(encoding="utf-8"))
    failed = []
    for k, v in acceptance.get("minimums", {}).items():
        if m.metrics.get(k) is None or m.metrics[k] < v:
            failed.append(f"{k}={m.metrics.get(k)} < {v}")
    for k, v in acceptance.get("maximums", {}).items():
        if m.metrics.get(k) is None or m.metrics[k] > v:
            failed.append(f"{k}={m.metrics.get(k)} > {v}")
    out = {
        "status": "ACTIVE" if not failed else "ACTIVE_BUT_BELOW_ACCEPTANCE",
        "name": m.name,
        "version": m.version,
        "checksum": m.checksum,
        "feature_schema_version": m.feature_schema_version,
        "metrics": m.metrics,
        "acceptance_failures": failed,
        "approved_by": m.manifest.get("approved_by"),
        "trained_at": m.manifest.get("trained_at"),
    }
    print(json.dumps(out, indent=2))
    sys.exit(0 if not failed else 3)


if __name__ == "__main__":
    main()
