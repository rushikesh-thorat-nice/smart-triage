import json
from pathlib import Path
from typing import Any

import chromadb
from chromadb.utils import embedding_functions

from .config import settings
from .db import KBEntry, get_session, init_db


_client: chromadb.PersistentClient | None = None
_collection = None


def _get_collection():
    global _client, _collection
    if _collection is not None:
        return _collection

    _client = chromadb.PersistentClient(path=settings.chroma_dir)
    embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="all-MiniLM-L6-v2"
    )
    _collection = _client.get_or_create_collection(
        name="kb_incidents", embedding_function=embed_fn, metadata={"hnsw:space": "cosine"}
    )
    return _collection


def seed_from_file(path: str | None = None) -> int:
    """Load kb_seed.json into both Chroma and SQLite. Idempotent."""
    path = path or settings.kb_seed_path
    entries = json.loads(Path(path).read_text(encoding="utf-8"))

    init_db()
    collection = _get_collection()

    # Wipe and reload for idempotency during dev
    existing = collection.get()
    if existing["ids"]:
        collection.delete(ids=existing["ids"])

    ids, docs, metas = [], [], []
    for e in entries:
        doc = f"{e['pattern_description']}\n{e['symptoms']}"
        ids.append(e["id"])
        docs.append(doc)
        metas.append({
            "product": e["product"],
            "owner_team": e["owner_team"],
            "severity": e["severity"],
        })

    collection.add(ids=ids, documents=docs, metadatas=metas)

    with get_session() as session:
        for row in session.query(KBEntry).all():
            session.delete(row)
        for e in entries:
            steps = e.get("resolution_steps")
            session.add(KBEntry(
                id=e["id"],
                product=e["product"],
                owner_team=e["owner_team"],
                severity=e["severity"],
                pattern_description=e["pattern_description"],
                symptoms=e["symptoms"],
                action_type=e.get("action_type", "execute"),
                resolution_steps=json.dumps(steps) if steps else None,
                resolution_summary=e["resolution_summary"],
                scenario_slug=e.get("scenario_slug"),
                auto_execute=e.get("auto_execute", False),
            ))
        session.commit()

    return len(entries)


def search(log_line: str, top_k: int = 3) -> list[dict[str, Any]]:
    """Return top-k KB candidates for a log line, ordered by similarity (highest first)."""
    collection = _get_collection()
    if collection.count() == 0:
        return []

    results = collection.query(
        query_texts=[log_line],
        n_results=min(top_k, collection.count()),
    )

    hits: list[dict[str, Any]] = []
    for kb_id, doc, meta, dist in zip(
        results["ids"][0],
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        # Chroma returns cosine distance in [0, 2]; similarity = 1 - distance for cosine on normalized vectors.
        similarity = max(0.0, 1.0 - dist)
        hits.append({
            "kb_id": kb_id,
            "document": doc,
            "metadata": meta,
            "similarity": round(similarity, 4),
        })
    return hits


def get_entry(kb_id: str) -> KBEntry | None:
    with get_session() as session:
        return session.query(KBEntry).filter(KBEntry.id == kb_id).one_or_none()


def list_entries() -> list[dict[str, Any]]:
    with get_session() as session:
        rows = session.query(KBEntry).all()
        return [
            {
                "id": r.id,
                "product": r.product,
                "owner_team": r.owner_team,
                "severity": r.severity,
                "pattern_description": r.pattern_description,
                "resolution_summary": r.resolution_summary,
                "resolution_steps": json.loads(r.resolution_steps) if r.resolution_steps else None,
                "action_type": r.action_type,
                "scenario_slug": r.scenario_slug,
                "auto_execute": r.auto_execute,
            }
            for r in rows
        ]
