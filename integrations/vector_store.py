from __future__ import annotations

import json
import math
import os
from abc import ABC, abstractmethod
from typing import Dict, List


def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    if not vec_a or not vec_b:
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def has_embedding_payload(embedding) -> bool:
    if embedding is None:
        return False
    try:
        if isinstance(embedding, dict):
            return len(embedding.get("values", [])) > 0
        if isinstance(embedding, (list, tuple)):
            return len(embedding) > 0
        if hasattr(embedding, "tolist"):
            return len(embedding.tolist()) > 0
        if hasattr(embedding, "embedding"):
            return len(getattr(embedding, "embedding", [])) > 0
        return True
    except Exception:
        return True


class BaseIndexStore(ABC):
    backend_name = "base"

    @abstractmethod
    def load_index(self) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def replace_index(self, manifest: Dict, chunks: List[Dict]) -> None:
        raise NotImplementedError

    @abstractmethod
    def semantic_search(self, query_vector: List[float], top_k: int) -> List[Dict]:
        raise NotImplementedError


class LocalIndexStore(BaseIndexStore):
    backend_name = "local"

    def __init__(self, index_path: str) -> None:
        self.index_path = index_path

    def load_index(self) -> Dict:
        if os.path.exists(self.index_path):
            with open(self.index_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        return {"manifest": {}, "chunks": []}

    def replace_index(self, manifest: Dict, chunks: List[Dict]) -> None:
        payload = {"manifest": manifest, "chunks": chunks}
        with open(self.index_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def semantic_search(self, query_vector: List[float], top_k: int) -> List[Dict]:
        index = self.load_index()
        scored = []
        for item in index.get("chunks", []):
            embedding = item.get("embedding")
            if not has_embedding_payload(embedding):
                continue
            score = cosine_similarity(query_vector, embedding)
            scored.append({**item, "score": score, "retrieval_mode": "semantic"})
        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:top_k]


class PgvectorIndexStore(BaseIndexStore):
    backend_name = "pgvector"

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._ensure_schema()

    def _connect(self):
        try:
            import psycopg
            from pgvector.psycopg import register_vector
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError(
                "Instala `psycopg[binary]` e `pgvector` para usar RAG_INDEX_BACKEND=pgvector."
            ) from exc
        conn = psycopg.connect(self.database_url, row_factory=dict_row)
        register_vector(conn)
        return conn

    def _ensure_schema(self) -> None:
        project_root = os.path.dirname(os.path.dirname(__file__))
        schema_path = os.path.join(project_root, "sql", "pgvector_schema.sql")
        with open(schema_path, "r", encoding="utf-8") as handle:
            schema_sql = handle.read()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(schema_sql)
            conn.commit()

    def load_index(self) -> Dict:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT manifest FROM rag_state WHERE id = 1")
                state = cur.fetchone()
                cur.execute(
                    """
                    SELECT id, document_name AS document, chunk_id, text, embedding, metadata, (embedding IS NOT NULL) AS has_embedding
                    FROM rag_chunks
                    ORDER BY document_name, chunk_id
                    """
                )
                rows = cur.fetchall()
        chunks = []
        for row in rows:
            metadata = row.get("metadata") or {}
            chunks.append(
                {
                    "id": row["id"],
                    "document": row["document"],
                    "chunk_id": row["chunk_id"],
                    "text": row["text"],
                    "embedding": row.get("embedding"),
                    "has_embedding": bool(row.get("has_embedding")),
                    **metadata,
                }
            )
        return {
            "manifest": state["manifest"] if state else {},
            "chunks": chunks,
        }

    def replace_index(self, manifest: Dict, chunks: List[Dict]) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO rag_state (id, manifest)
                    VALUES (1, %s::jsonb)
                    ON CONFLICT (id) DO UPDATE SET manifest = EXCLUDED.manifest
                    """,
                    (json.dumps(manifest),),
                )
                cur.execute("DELETE FROM rag_chunks")
                for item in chunks:
                    metadata = {
                        "preview": item.get("text", "")[:220],
                    }
                    cur.execute(
                        """
                        INSERT INTO rag_chunks (id, document_name, chunk_id, text, embedding, metadata)
                        VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            item["id"],
                            item["document"],
                            item["chunk_id"],
                            item["text"],
                            item.get("embedding"),
                            json.dumps(metadata),
                        ),
                    )
            conn.commit()

    def semantic_search(self, query_vector: List[float], top_k: int) -> List[Dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        document_name AS document,
                        chunk_id,
                        text,
                        metadata,
                        1 - (embedding <=> %s::vector) AS score
                    FROM rag_chunks
                    WHERE embedding IS NOT NULL
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (query_vector, query_vector, top_k),
                )
                rows = cur.fetchall()
        return [
            {
                "id": row["id"],
                "document": row["document"],
                "chunk_id": row["chunk_id"],
                "text": row["text"],
                "score": float(row["score"]),
                "retrieval_mode": "semantic",
                **(row.get("metadata") or {}),
            }
            for row in rows
        ]


def create_index_store(data_dir: str) -> BaseIndexStore:
    backend = os.getenv("RAG_INDEX_BACKEND", "local").strip().lower()
    if backend == "pgvector":
        database_url = os.getenv("DATABASE_URL", "").strip()
        if not database_url:
            raise RuntimeError("Define DATABASE_URL para usar RAG_INDEX_BACKEND=pgvector.")
        return PgvectorIndexStore(database_url=database_url)
    return LocalIndexStore(index_path=os.path.join(data_dir, "rag_index.json"))
