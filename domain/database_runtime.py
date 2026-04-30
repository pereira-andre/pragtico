from __future__ import annotations

from typing import Dict


def _connect(database_url: str):
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError("Instala `psycopg[binary]` para usar PostgreSQL.") from exc
    return psycopg.connect(database_url, row_factory=dict_row)


def _table_counts(conn) -> Dict[str, int]:
    counts = {}
    with conn.cursor() as cur:
        for table in ("app_users", "documents", "conversations", "messages", "port_calls", "rag_chunks"):
            cur.execute(f"SELECT COUNT(*) AS total FROM {table}")
            counts[table] = int(cur.fetchone()["total"])
    return counts


def get_database_runtime_status(database_url: str) -> Dict:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database() AS name, current_user AS db_user")
            db_info = cur.fetchone()
            cur.execute(
                """
                SELECT EXISTS(
                    SELECT 1
                    FROM pg_extension
                    WHERE extname = 'vector'
                ) AS installed
                """
            )
            vector_installed = bool(cur.fetchone()["installed"])
        counts = _table_counts(conn)
    return {
        "ok": True,
        "database_name": db_info["name"],
        "database_user": db_info["db_user"],
        "vector_installed": vector_installed,
        "counts": counts,
    }
