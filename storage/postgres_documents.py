"""Document persistence methods for the PostgreSQL store."""

from __future__ import annotations

import os
import uuid
from typing import Dict, List, Optional

from werkzeug.datastructures import FileStorage

from domain.document_processing import (
    build_preview,
    ensure_unique_filename,
    extract_text_from_path,
    file_metadata,
    format_bytes,
    infer_document_type,
    is_allowed_document,
    is_text_editable,
    iso_now,
    sanitize_upload_filename,
    slugify,
)

from .utils import _utc_iso_to_label


class PostgresDocumentMixin:
    def save_document(self, title: str, content: str, created_by: str = "manual") -> str:
        filename = ensure_unique_filename(self.knowledge_dir, f"{slugify(title)}.md")
        path = os.path.join(self.knowledge_dir, filename)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content.strip() + "\n")
        meta = file_metadata(path)
        self._upsert_document_record(
            {
                "name": filename,
                "original_name": filename,
                "doc_type": infer_document_type(filename),
                "size_bytes": meta["size_bytes"],
                "updated_at": meta["updated_at"],
                "created_at": iso_now(),
                "uploaded_by": created_by,
                "preview": build_preview(content),
                "editable": is_text_editable(filename),
            },
            path,
        )
        return filename

    def save_uploaded_document(self, uploaded_file: FileStorage, created_by: str) -> str:
        filename = sanitize_upload_filename(uploaded_file.filename or "")
        if not is_allowed_document(filename):
            raise ValueError("Formato não suportado. Usa .pdf, .md, .txt, .docx ou .csv.")

        path = os.path.join(self.knowledge_dir, filename)
        stem, suffix = os.path.splitext(path)
        temp_path = f"{stem}.upload-{uuid.uuid4().hex}{suffix}"
        uploaded_file.save(temp_path)

        try:
            text = extract_text_from_path(temp_path)
        except Exception as exc:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise ValueError(f"Falha ao processar ficheiro: {exc}") from exc
        if not text.strip():
            os.remove(temp_path)
            raise ValueError("Não foi possível extrair texto útil do ficheiro.")

        os.replace(temp_path, path)

        meta = file_metadata(path)
        self._upsert_document_record(
            {
                "name": filename,
                "original_name": uploaded_file.filename or filename,
                "doc_type": infer_document_type(filename),
                "size_bytes": meta["size_bytes"],
                "updated_at": meta["updated_at"],
                "created_at": iso_now(),
                "uploaded_by": created_by,
                "preview": build_preview(text),
                "editable": is_text_editable(filename),
            },
            path,
        )
        return filename

    def list_documents(self) -> List[Dict]:
        self._sync_document_records()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        name, original_name, doc_type, size_bytes, updated_at, created_at,
                        uploaded_by, preview
                    FROM documents
                    ORDER BY updated_at DESC, name
                    """
                )
                rows = cur.fetchall()
        return [
            {
                **row,
                "size_label": format_bytes(row["size_bytes"]),
                "updated_at_label": _utc_iso_to_label(row["updated_at"].isoformat()),
                "editable": is_text_editable(row["name"]),
            }
            for row in rows
        ]

    def get_document(self, name: str) -> Optional[Dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        name, original_name, doc_type, size_bytes, updated_at, created_at,
                        uploaded_by, preview, file_path
                    FROM documents
                    WHERE name = %s
                    """,
                    (name,),
                )
                row = cur.fetchone()
        if not row:
            return None
        return {
            **row,
            "size_label": format_bytes(row["size_bytes"]),
            "updated_at": row["updated_at"].isoformat(),
            "created_at": row["created_at"].isoformat(),
            "updated_at_label": _utc_iso_to_label(row["updated_at"].isoformat()),
            "editable": is_text_editable(row["name"]),
        }

    def get_document_text(self, name: str) -> str:
        record = self.get_document(name)
        if not record:
            raise ValueError("Documento não encontrado.")
        return extract_text_from_path(record["file_path"])

    def get_document_file_path(self, name: str) -> str:
        record = self.get_document(name)
        if not record:
            raise ValueError("Documento não encontrado.")
        return record["file_path"]

    def update_document_text(self, name: str, content: str, updated_by: str) -> Dict:
        if not content.strip():
            raise ValueError("O conteúdo não pode estar vazio.")
        record = self.get_document(name)
        if not record:
            raise ValueError("Documento não encontrado.")
        if not is_text_editable(name):
            raise ValueError("Este tipo de ficheiro não pode ser editado no browser.")

        with open(record["file_path"], "w", encoding="utf-8") as handle:
            handle.write(content.strip() + "\n")
        meta = file_metadata(record["file_path"])

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE documents
                    SET
                        size_bytes = %s,
                        updated_at = %s,
                        uploaded_by = %s,
                        preview = %s
                    WHERE name = %s
                    """,
                    (
                        meta["size_bytes"],
                        meta["updated_at"],
                        updated_by,
                        build_preview(content),
                        name,
                    ),
                )
            conn.commit()
        return self.get_document(name)

    def delete_document(self, name: str) -> None:
        record = self.get_document(name)
        if not record:
            raise ValueError("Documento não encontrado.")
        if os.path.exists(record["file_path"]):
            os.remove(record["file_path"])
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM documents WHERE name = %s", (name,))
            conn.commit()
