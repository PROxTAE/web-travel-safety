"""Model registry loader: serves only an artifact whose manifest is stage ACTIVE and whose checksum matches.

Artifact layout (``RISK_MODEL_ARTIFACT_DIR``):
  <dir>/current/manifest.json   {name, version, stage, feature_schema_version, feature_names, checksum(sha256 of
                                 model.joblib), metrics, thresholds_version, trained_at, dataset_manifest, approved_by}
  <dir>/current/model.joblib    sklearn Pipeline (imputer -> scaler -> calibrated classifier)
  <dir>/<version>/...           previous versions kept for rollback (python -m app.cli.verify_model --rollback <v>)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sta_common.logging import get_logger

log = get_logger("model-loader")


class ModelUnavailableError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(slots=True)
class LoadedModel:
    name: str
    version: str
    stage: str
    feature_schema_version: str
    feature_names: list[str]
    checksum: str
    metrics: dict[str, Any]
    thresholds_version: str
    pipeline: Any
    manifest: dict[str, Any]

    def _with_derived(self, features: dict[str, Any]) -> dict[str, Any]:
        if "rule_score" in self.feature_names and "rule_score" not in features:
            from app.risk.inference import rule_baseline_probability

            features = {**features, "rule_score": rule_baseline_probability(features)}
        return features

    def predict_proba(self, features: dict[str, Any]) -> float:
        features = self._with_derived(features)
        row = np.array([[_num(features.get(n)) for n in self.feature_names]], dtype=float)
        p = float(self.pipeline.predict_proba(row)[0, 1])
        return max(0.0, min(1.0, p))

    def contributions(self, features: dict[str, Any]) -> dict[str, float]:
        """Signed contribution per feature for linear final estimators (coef * standardized value).
        For non-linear estimators returns an empty dict; reason codes then rely on overrides + feature thresholds."""
        try:
            pipe = self.pipeline
            est = pipe
            # CalibratedClassifierCV wraps the base estimator
            if hasattr(pipe, "named_steps"):
                est = pipe.named_steps.get("clf", pipe)
            if hasattr(est, "calibrated_classifiers_"):
                est = est.calibrated_classifiers_[0].estimator
            coef = getattr(est, "coef_", None)
            if coef is None:
                return {}
            features = self._with_derived(features)
            row = np.array([[_num(features.get(n)) for n in self.feature_names]], dtype=float)
            x = row
            if hasattr(pipe, "named_steps"):
                for name, step in pipe.named_steps.items():
                    if name == "clf":
                        break
                    x = step.transform(x)
            contrib = (coef[0] * x[0]).tolist()
            return {n: round(float(c), 4) for n, c in zip(self.feature_names, contrib, strict=True)}
        except Exception as exc:  # noqa: BLE001 - explanation must never break inference
            log.warning("contribution_failed", error_type=type(exc).__name__)
            return {}


def _num(v: Any) -> float:
    if v is None:
        return float("nan")
    return float(v)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_active_model(artifact_dir: str | Path, *, expected_feature_schema: str) -> LoadedModel:
    base = Path(artifact_dir) / "current"
    manifest_path = base / "manifest.json"
    model_path = base / "model.joblib"
    if not manifest_path.exists() or not model_path.exists():
        raise ModelUnavailableError("no active model artifact (run training + promote)")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("stage") != "ACTIVE":
        raise ModelUnavailableError(f"model stage is {manifest.get('stage')}, not ACTIVE")
    if manifest.get("feature_schema_version") != expected_feature_schema:
        got = manifest.get("feature_schema_version")
        raise ModelUnavailableError(f"feature schema mismatch: model {got} vs service {expected_feature_schema}")
    checksum = sha256_file(model_path)
    if checksum != manifest.get("checksum"):
        raise ModelUnavailableError("model checksum mismatch — refusing to serve")
    pipeline = joblib.load(model_path)
    log.info("model_loaded", model=manifest["name"], version=manifest["version"], checksum=checksum[:12])
    return LoadedModel(
        name=str(manifest["name"]),
        version=str(manifest["version"]),
        stage=str(manifest["stage"]),
        feature_schema_version=str(manifest["feature_schema_version"]),
        feature_names=list(manifest["feature_names"]),
        checksum=checksum,
        metrics=dict(manifest.get("metrics", {})),
        thresholds_version=str(manifest.get("thresholds_version", "1.0.0")),
        pipeline=pipeline,
        manifest=manifest,
    )
