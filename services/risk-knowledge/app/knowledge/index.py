"""Qdrant versioned collections + alias, BM25 side index, hybrid retrieval with RRF, filters, citation resolution.

Collection name: {prefix}_v{collection_version_slug}; alias {prefix}_active points at the released version.
Chunk payloads hold everything needed to resolve a citation (document, section, url, authority, dates, hash).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client import models as qm
from rank_bm25 import BM25Okapi
from sta_common.logging import get_logger
from sta_contracts.enums import DisasterEventType, SourceAuthority
from sta_contracts.models import RetrievedEvidence

from app.knowledge.embeddings import Embedder
from app.knowledge.ingest import Chunk, IngestedDocument

log = get_logger("knowledge-index")
_TOKEN = re.compile(r"[\w฀-๿]+", re.UNICODE)


def _slug(version: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", version.lower())


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


@dataclass(slots=True)
class KnowledgeStatus:
    collection_version: str | None
    collection_name: str | None
    alias: str
    points: int
    documents: int
    embedding_model: str
    indexed_at: str | None


class KnowledgeIndex:
    def __init__(self, client: QdrantClient, embedder: Embedder, prefix: str) -> None:
        self.client = client
        self.embedder = embedder
        self.prefix = prefix
        self.alias = f"{prefix}_active"
        self._bm25: BM25Okapi | None = None
        self._bm25_ids: list[str] = []
        self._payloads: dict[str, dict[str, Any]] = {}
        self._active: str | None = None
        self._version: str | None = None

    # ------------------------------------------------------------------ build / release
    def build(self, version: str, docs: list[IngestedDocument]) -> str:
        name = f"{self.prefix}_v{_slug(version)}"
        if self.client.collection_exists(name):
            self.client.delete_collection(name)
        self.client.create_collection(
            name, vectors_config=qm.VectorParams(size=self.embedder.dim, distance=qm.Distance.COSINE)
        )
        points: list[qm.PointStruct] = []
        for d in docs:
            texts = [c.text for c in d.chunks]
            vecs = self.embedder.embed_passages(texts)
            for c, v in zip(d.chunks, vecs, strict=True):
                payload = self._payload(c, d, version)
                points.append(
                    qm.PointStruct(id=str(uuid5(NAMESPACE_URL, c.chunk_id)), vector=v.tolist(), payload=payload)
                )
        for i in range(0, len(points), 128):
            self.client.upsert(name, points[i : i + 128])
        log.info("collection_built", collection=name, points=len(points), documents=len(docs))
        return name

    def release(self, name: str) -> None:
        """Atomically repoint the alias (rollback = release(previous_name))."""
        ops: list[Any] = (
            [qm.DeleteAliasOperation(delete_alias=qm.DeleteAlias(alias_name=self.alias))]
            if self._alias_exists()
            else []
        )
        ops.append(qm.CreateAliasOperation(create_alias=qm.CreateAlias(collection_name=name, alias_name=self.alias)))
        self.client.update_collection_aliases(change_aliases_operations=ops)
        self.load_active()

    def _alias_exists(self) -> bool:
        try:
            return any(a.alias_name == self.alias for a in self.client.get_aliases().aliases)
        except Exception:  # noqa: BLE001
            return False

    def load_active(self) -> KnowledgeStatus:
        active = None
        for a in self.client.get_aliases().aliases:
            if a.alias_name == self.alias:
                active = a.collection_name
        return self.load_collection(active)

    def load_collection(self, name: str | None) -> KnowledgeStatus:
        """Load payload/BM25 side index for a collection (the alias target, or a candidate for evaluation)."""
        self._active = name
        self._bm25 = None
        self._payloads = {}
        self._version = None
        if self._active is None:
            return self.status()
        ids: list[str] = []
        corpus: list[list[str]] = []
        offset = None
        while True:
            recs, offset = self.client.scroll(
                self._active, limit=256, offset=offset, with_payload=True, with_vectors=False
            )
            for r in recs:
                pid = str(r.id)
                payload = dict(r.payload or {})
                self._payloads[pid] = payload
                ids.append(pid)
                corpus.append(_tokens(str(payload.get("text", ""))))
                self._version = str(payload.get("collection_version"))
            if offset is None:
                break
        self._bm25_ids = ids
        self._bm25 = BM25Okapi(corpus) if corpus else None
        return self.status()

    def status(self) -> KnowledgeStatus:
        docs = {p.get("document_id") for p in self._payloads.values()}
        indexed = next((p.get("indexed_at") for p in self._payloads.values()), None)
        return KnowledgeStatus(
            collection_version=self._version,
            collection_name=self._active,
            alias=self.alias,
            points=len(self._payloads),
            documents=len(docs),
            embedding_model=self.embedder.name,
            indexed_at=str(indexed) if indexed else None,
        )

    def _payload(self, c: Chunk, d: IngestedDocument, version: str) -> dict[str, Any]:
        return {
            "chunk_id": c.chunk_id,
            "document_id": c.document_id,
            "title": d.doc.title,
            "authority": c.authority,
            "organization": d.doc.organization,
            "source_url": d.final_url,
            "section": c.section,
            "page": c.page,
            "order": c.order,
            "language": c.language,
            "country": c.country,
            "hazards": c.hazards,
            "effective_at": d.doc.effective_dt.astimezone(UTC).isoformat(),
            "expires_at": d.doc.expires_dt.astimezone(UTC).isoformat(),
            "text": c.text,
            "text_hash": c.text_hash,
            "document_checksum": d.checksum,
            "collection_version": version,
            "embedding_model": self.embedder.name,
            "indexed_at": datetime.now(UTC).isoformat(),
        }

    # ------------------------------------------------------------------ retrieval
    def retrieve(
        self,
        *,
        query: str,
        hazards: list[DisasterEventType],
        country: str | None,
        language: str,
        top_k: int,
        min_score: float,
        now: datetime | None = None,
    ) -> list[RetrievedEvidence]:
        if self._active is None or not self._payloads:
            return []
        now = now or datetime.now(UTC)
        qvec = self.embedder.embed_query(query)
        dense = self.client.query_points(self._active, query=qvec.tolist(), limit=top_k * 4, with_payload=False).points
        dense_rank = {str(p.id): (i + 1, float(p.score)) for i, p in enumerate(dense)}
        bm_rank: dict[str, tuple[int, float]] = {}
        if self._bm25 is not None:
            scores = self._bm25.get_scores(_tokens(query))
            order = np.argsort(-scores)[: top_k * 4]
            for i, idx in enumerate(order):
                if scores[idx] > 0:
                    bm_rank[self._bm25_ids[int(idx)]] = (i + 1, float(scores[idx]))
        # reciprocal rank fusion
        fused: dict[str, float] = {}
        for pid, (r, _) in dense_rank.items():
            fused[pid] = fused.get(pid, 0.0) + 1 / (60 + r)
        for pid, (r, _) in bm_rank.items():
            fused[pid] = fused.get(pid, 0.0) + 1 / (60 + r)
        wanted = {h.value for h in hazards}
        out: list[RetrievedEvidence] = []
        seen_docs: dict[str, int] = {}
        for pid, fscore in sorted(fused.items(), key=lambda kv: -kv[1]):
            p = self._payloads.get(pid)
            if p is None:
                continue
            # filters: expiry, geography, language, hazard
            if datetime.fromisoformat(p["expires_at"]) <= now or datetime.fromisoformat(p["effective_at"]) > now:
                continue
            if country and p["country"] not in ("GLOBAL", country):
                continue
            if language and p["language"] != language.split("-")[0] and p["language"] != "en":
                continue
            if wanted and not (set(p.get("hazards", [])) & wanted):
                continue
            dense_score = dense_rank.get(pid, (0, 0.0))[1]
            if dense_score < min_score and pid not in bm_rank:
                continue
            if seen_docs.get(p["document_id"], 0) >= 2:  # diversity: max 2 passages per document
                continue
            seen_docs[p["document_id"]] = seen_docs.get(p["document_id"], 0) + 1
            out.append(
                RetrievedEvidence(
                    evidence_id=uuid5(NAMESPACE_URL, f"{p['collection_version']}:{p['chunk_id']}"),
                    document_id=p["document_id"],
                    authority=SourceAuthority(p["authority"]),
                    title=p["title"],
                    source_url=p["source_url"],
                    page=p.get("page"),
                    section=p.get("section"),
                    language=p["language"],
                    hazards=[DisasterEventType(h) for h in p.get("hazards", []) if h in DisasterEventType.__members__],
                    effective_at=datetime.fromisoformat(p["effective_at"]),
                    expires_at=datetime.fromisoformat(p["expires_at"]),
                    passage=p["text"][:4000],
                    retrieval_score=round(dense_score, 4),
                    rerank_score=round(fscore, 6),
                    collection_version=p["collection_version"],
                    content_hash=p["text_hash"],
                )
            )
            if len(out) >= top_k:
                break
        return out

    def resolve_citation(self, evidence_id: str) -> dict[str, Any] | None:
        for p in self._payloads.values():
            if str(uuid5(NAMESPACE_URL, f"{p['collection_version']}:{p['chunk_id']}")) == evidence_id:
                return {
                    k: p[k]
                    for k in (
                        "document_id",
                        "title",
                        "source_url",
                        "section",
                        "page",
                        "text_hash",
                        "document_checksum",
                        "collection_version",
                    )
                }
        return None
