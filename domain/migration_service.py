from __future__ import annotations

import json
import os
from typing import Dict, List


def _connect(database_url: str):
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError("Instala `psycopg[binary]` para usar migração Postgres.") from exc
    return psycopg.connect(database_url, row_factory=dict_row)


def _read_json(path: str, fallback):
    if not os.path.exists(path):
        return fallback
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _table_counts(conn) -> Dict[str, int]:
    with conn.cursor() as cur:
        counts = {}
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
            cur.execute(
                """
                SELECT value, updated_at
                FROM app_runtime_state
                WHERE key = 'local_json_migration'
                """
            )
            migration = cur.fetchone()
        counts = _table_counts(conn)
    return {
        "ok": True,
        "database_name": db_info["name"],
        "database_user": db_info["db_user"],
        "vector_installed": vector_installed,
        "counts": counts,
        "migration": {
            "value": migration["value"],
            "updated_at": migration["updated_at"].isoformat(),
        }
        if migration
        else None,
    }


def migrate_local_json_to_postgres(
    data_dir: str,
    knowledge_dir: str,
    database_url: str,
    force: bool = False,
) -> Dict:
    users = _read_json(os.path.join(data_dir, "users.json"), [])
    documents = _read_json(os.path.join(data_dir, "documents.json"), [])
    conversations = _read_json(os.path.join(data_dir, "conversations.json"), [])
    messages = _read_json(os.path.join(data_dir, "messages.json"), [])
    port_calls = _read_json(os.path.join(data_dir, "port_calls.json"), [])

    with _connect(database_url) as conn:
        before = _table_counts(conn)
        if not force and any(before[name] > 0 for name in ("app_users", "conversations", "messages")):
            summary = {
                "status": "skipped",
                "reason": "postgres já contém dados aplicacionais",
                "before": before,
                "migrated": {
                    "users": 0,
                    "documents": 0,
                    "conversations": 0,
                "messages": 0,
                "port_calls": 0,
            },
        }
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app_runtime_state (key, value)
                    VALUES ('local_json_migration', %s::jsonb)
                    ON CONFLICT (key) DO UPDATE SET
                        value = EXCLUDED.value,
                        updated_at = NOW()
                    """,
                    (json.dumps(summary),),
                )
            conn.commit()
            return summary

        migrated = {
            "users": 0,
            "documents": 0,
            "conversations": 0,
            "messages": 0,
            "port_calls": 0,
        }

        with conn.cursor() as cur:
            for user in users:
                cur.execute(
                    """
                    INSERT INTO app_users (username, password_hash, role)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (username) DO UPDATE SET
                        password_hash = EXCLUDED.password_hash,
                        role = EXCLUDED.role
                    """,
                    (user["username"], user["password_hash"], user["role"]),
                )
                migrated["users"] += 1

            for document in documents:
                file_path = os.path.join(knowledge_dir, document["name"])
                if not os.path.exists(file_path):
                    continue
                cur.execute(
                    """
                    INSERT INTO documents (
                        name, original_name, doc_type, size_bytes, updated_at, created_at,
                        uploaded_by, preview, file_path
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (name) DO UPDATE SET
                        original_name = EXCLUDED.original_name,
                        doc_type = EXCLUDED.doc_type,
                        size_bytes = EXCLUDED.size_bytes,
                        updated_at = EXCLUDED.updated_at,
                        created_at = EXCLUDED.created_at,
                        uploaded_by = EXCLUDED.uploaded_by,
                        preview = EXCLUDED.preview,
                        file_path = EXCLUDED.file_path
                    """,
                    (
                        document["name"],
                        document.get("original_name", document["name"]),
                        document["doc_type"],
                        document["size_bytes"],
                        document["updated_at"],
                        document.get("created_at", document["updated_at"]),
                        document.get("uploaded_by", "system"),
                        document.get("preview", ""),
                        file_path,
                    ),
                )
                migrated["documents"] += 1

            for conversation in conversations:
                cur.execute(
                    """
                    INSERT INTO conversations (id, username, title, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        username = EXCLUDED.username,
                        title = EXCLUDED.title,
                        created_at = EXCLUDED.created_at,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        conversation["id"],
                        conversation["username"],
                        conversation["title"],
                        conversation["created_at"],
                        conversation["updated_at"],
                    ),
                )
                migrated["conversations"] += 1

            for message in messages:
                cur.execute(
                    """
                    INSERT INTO messages (
                        id, conversation_id, role, content, citations, created_at,
                        feedback_status, feedback_note, feedback_updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        conversation_id = EXCLUDED.conversation_id,
                        role = EXCLUDED.role,
                        content = EXCLUDED.content,
                        citations = EXCLUDED.citations,
                        created_at = EXCLUDED.created_at,
                        feedback_status = EXCLUDED.feedback_status,
                        feedback_note = EXCLUDED.feedback_note,
                        feedback_updated_at = EXCLUDED.feedback_updated_at
                    """,
                    (
                        message["id"],
                        message["conversation_id"],
                        message["role"],
                        message["content"],
                        json.dumps(message.get("citations", [])),
                        message["created_at"],
                        message.get("feedback_status"),
                        message.get("feedback_note", ""),
                        message.get("feedback_updated_at"),
                    ),
                )
                migrated["messages"] += 1

            for port_call in port_calls:
                cur.execute(
                    """
                    INSERT INTO port_calls (
                        id, vessel_name, status, approval_status, approval_note, aborted_reason,
                        decided_by, decided_at, eta, ata, planned_departure_at, departure_plan_note, departure_at, berth,
                        last_port, next_port, created_by, notes, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        vessel_name = EXCLUDED.vessel_name,
                        status = EXCLUDED.status,
                        approval_status = EXCLUDED.approval_status,
                        approval_note = EXCLUDED.approval_note,
                        aborted_reason = EXCLUDED.aborted_reason,
                        decided_by = EXCLUDED.decided_by,
                        decided_at = EXCLUDED.decided_at,
                        eta = EXCLUDED.eta,
                        ata = EXCLUDED.ata,
                        planned_departure_at = EXCLUDED.planned_departure_at,
                        departure_plan_note = EXCLUDED.departure_plan_note,
                        departure_at = EXCLUDED.departure_at,
                        berth = EXCLUDED.berth,
                        last_port = EXCLUDED.last_port,
                        next_port = EXCLUDED.next_port,
                        created_by = EXCLUDED.created_by,
                        notes = EXCLUDED.notes,
                        created_at = EXCLUDED.created_at,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        port_call["id"],
                        port_call["vessel_name"],
                        port_call["status"],
                        port_call.get("approval_status", "pending"),
                        port_call.get("approval_note", ""),
                        port_call.get("aborted_reason", ""),
                        port_call.get("decided_by"),
                        port_call.get("decided_at"),
                        port_call.get("eta"),
                        port_call.get("ata"),
                        port_call.get("planned_departure_at"),
                        port_call.get("departure_plan_note", ""),
                        port_call.get("departure_at"),
                        port_call.get("berth"),
                        port_call.get("last_port"),
                        port_call.get("next_port"),
                        port_call.get("created_by", "system"),
                        port_call.get("notes", ""),
                        port_call.get("created_at"),
                        port_call.get("updated_at"),
                    ),
                )
                migrated["port_calls"] += 1

            after = _table_counts(conn)
            summary = {
                "status": "completed",
                "before": before,
                "after": after,
                "migrated": migrated,
                "source_data_dir": data_dir,
            }
            cur.execute(
                """
                INSERT INTO app_runtime_state (key, value)
                VALUES ('local_json_migration', %s::jsonb)
                ON CONFLICT (key) DO UPDATE SET
                    value = EXCLUDED.value,
                    updated_at = NOW()
                """,
                (json.dumps(summary),),
            )
        conn.commit()

    return summary


def main() -> None:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(base_dir, "data")
    knowledge_dir = os.path.join(base_dir, "knowledge")
    database_url = os.getenv("DATABASE_URL", "").strip()
    force = os.getenv("MIGRATION_FORCE", "0") == "1"
    if not database_url:
        raise SystemExit("Define DATABASE_URL antes de correr a migração.")
    result = migrate_local_json_to_postgres(
        data_dir=data_dir,
        knowledge_dir=knowledge_dir,
        database_url=database_url,
        force=force,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
