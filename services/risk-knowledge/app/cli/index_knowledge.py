"""Ingest approved sources, build a versioned Qdrant collection, evaluate, then release the alias.

Usage:
  uv run python -m app.cli.index_knowledge                 # ingest + build + evaluate + release
  uv run python -m app.cli.index_knowledge --no-release    # build only (candidate)
  uv run python -m app.cli.index_knowledge --rollback <collection_name>
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from qdrant_client import QdrantClient
from sta_contracts.enums import DisasterEventType

from app.knowledge.embeddings import make_embedder
from app.knowledge.index import KnowledgeIndex
from app.knowledge.ingest import IngestedDocument, ingest_document
from app.knowledge.manifest import SourceManifest
from app.settings import get_settings

# Minimal golden relevance set: query -> expected document ids (evaluated before release)
GOLDEN = [
    ("flash flood road driving turn around", [DisasterEventType.FLOOD], {"fema-ready-floods"}),
    ("earthquake drop cover hold on aftershock", [DisasterEventType.EARTHQUAKE], {"fema-ready-earthquakes"}),
    ("hurricane evacuation order storm surge", [DisasterEventType.CYCLONE], {"fema-ready-hurricanes"}),
    ("wildfire smoke evacuate", [DisasterEventType.WILDFIRE], {"fema-ready-wildfires"}),
    ("lightning thunderstorm shelter", [DisasterEventType.STORM], {"fema-ready-thunderstorms"}),
]


def evaluate(index: KnowledgeIndex, top_k: int, min_score: float) -> dict[str, float]:
    hits = 0
    for q, hz, expected in GOLDEN:
        ev = index.retrieve(query=q, hazards=hz, country=None, language="en", top_k=top_k, min_score=min_score)
        if any(e.document_id in expected for e in ev[:3]):
            hits += 1
    # expiry / geography exclusion is covered by unit tests (test_knowledge.py)
    return {"recall_at_3": hits / len(GOLDEN), "queries": len(GOLDEN)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-release", action="store_true")
    ap.add_argument("--rollback", type=str, default=None)
    ap.add_argument("--min-recall", type=float, default=0.8)
    args = ap.parse_args()
    s = get_settings()
    client = QdrantClient(location=":memory:") if s.app_env == "test" else QdrantClient(url=s.qdrant_url, timeout=30)
    embedder = make_embedder(s.embedding_provider, s.embedding_model_name, s.app_env)
    index = KnowledgeIndex(client, embedder, s.knowledge_collection_prefix)
    if args.rollback:
        index.release(args.rollback)
        print(json.dumps({"rolled_back_to": args.rollback, **index.status().__dict__}, default=str))
        return
    manifest = SourceManifest.load(s.knowledge_sources_path)
    cache = Path(s.knowledge_cache_dir)
    docs: list[IngestedDocument] = []
    failures = []
    for d in manifest.documents:
        try:
            docs.append(ingest_document(d, manifest, cache))
        except Exception as exc:  # noqa: BLE001
            failures.append({"document_id": d.document_id, "error": f"{type(exc).__name__}: {exc}"})
    if not docs:
        print(json.dumps({"error": "no documents ingested", "failures": failures}))
        sys.exit(1)
    name = index.build(manifest.collection_version, docs)
    # evaluate on the candidate collection before the alias switch
    status_before = index.load_collection(name)
    metrics = evaluate(index, s.retrieval_top_k, s.retrieval_min_score)
    report = {
        "collection": name,
        "collection_version": manifest.collection_version,
        "documents": [
            {"id": d.doc.document_id, "chunks": len(d.chunks), "checksum": d.checksum, "url": d.final_url} for d in docs
        ],
        "failures": failures,
        "metrics": metrics,
        "embedding_model": embedder.name,
        "evaluated_at": datetime.now(UTC).isoformat(),
        "points": status_before.points,
    }
    (cache / f"index-report-{manifest.collection_version}.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    if metrics["recall_at_3"] < args.min_recall:
        report["released"] = False
        report["reason"] = f"recall_at_3 {metrics['recall_at_3']:.2f} below {args.min_recall}"
        print(json.dumps(report, indent=2))
        sys.exit(2)
    if not args.no_release:
        index.release(name)
        report["released"] = True
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
