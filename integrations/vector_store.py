from __future__ import annotations

import json
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Dict, List

logger = logging.getLogger(__name__)


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


class PgvectorIndexStore(BaseIndexStore):
    backend_name = "pgvector"

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._ensure_schema()

    def _connect(self, *, register_vector_types: bool = True):
        try:
            import psycopg
            from psycopg.rows import dict_row
            if register_vector_types:
                from pgvector.psycopg import register_vector
        except ImportError as exc:
            raise RuntimeError(
                "Instala `psycopg[binary]` e `pgvector` para usar o índice PostgreSQL."
            ) from exc
        max_wait_seconds = max(float(os.getenv("DATABASE_CONNECT_MAX_WAIT_SECONDS", "45")), 0.0)
        retry_interval_seconds = max(float(os.getenv("DATABASE_CONNECT_RETRY_INTERVAL_SECONDS", "2")), 0.1)
        started_at = time.monotonic()
        last_exc = None

        while True:
            conn = None
            try:
                conn = psycopg.connect(self.database_url, row_factory=dict_row)
                if register_vector_types:
                    register_vector(conn)
                return conn
            except Exception as exc:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
                last_exc = exc
                elapsed = time.monotonic() - started_at
                if elapsed >= max_wait_seconds:
                    raise RuntimeError(
                        "Falha ao ligar ao PostgreSQL/pgvector durante o arranque. "
                        "Confirma DATABASE_URL e se a base Railway com pgvector já está pronta."
                    ) from exc
                logger.warning(
                    "Pgvector ainda não está pronto; nova tentativa em %.1fs (%s)",
                    retry_interval_seconds,
                    exc,
                )
                time.sleep(retry_interval_seconds)

    def _ensure_schema(self) -> None:
        project_root = os.path.dirname(os.path.dirname(__file__))
        schema_path = os.path.join(project_root, "sql", "pgvector_schema.sql")
        with open(schema_path, "r", encoding="utf-8") as handle:
            schema_sql = handle.read()
        try:
            # The extension must exist before pgvector can register the `vector` type.
            with self._connect(register_vector_types=False) as conn:
                with conn.cursor() as cur:
                    cur.execute(schema_sql)
                conn.commit()
        except Exception as exc:
            message = str(exc).lower()
            if "extension" in message and "vector" in message:
                raise RuntimeError(
                    "A base de dados ligada ao Railway não tem a extensão pgvector disponível. "
                    "Usa um serviço PostgreSQL com pgvector."
                ) from exc
            raise

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
                    metadata.update(
                        {
                            key: value
                            for key, value in item.items()
                            if key
                            not in {
                                "id",
                                "document",
                                "chunk_id",
                                "text",
                                "embedding",
                                "raw_text",
                                "has_embedding",
                                "score",
                                "retrieval_mode",
                            }
                            and value not in (None, "")
                        }
                    )
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
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("Define DATABASE_URL para inicializar o índice pgvector.")
    return PgvectorIndexStore(database_url=database_url)
