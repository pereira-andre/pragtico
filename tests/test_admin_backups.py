from __future__ import annotations

from io import BytesIO
import json
import zipfile

from flask import Flask

from blueprints import admin as admin_module
from core import services


class BackupFakeStore:
    backend_name = "fake"

    def __init__(self, knowledge_dir: str) -> None:
        self.knowledge_dir = knowledge_dir

    def list_feedback_eval_cases(self) -> list[dict]:
        return [{"id": "eval-1", "document": "doc", "question": "q", "expected_answer": "a"}]

    def list_reviewable_chat_messages(self, limit: int = 5000) -> list[dict]:
        return [{"id": "msg-1", "content": "ok"}]

    def list_maneuver_cases(self, limit: int = 5000) -> list[dict]:
        return [{"maneuver_id": "man-1", "feedback_status": "approved"}]

    def get_runtime_state(self, key: str) -> dict:
        return {"records": []}


def test_system_backup_zip_contains_json_and_readme(tmp_path, monkeypatch) -> None:
    app = Flask(__name__)
    app.config["TESTING"] = True
    backup_dir = tmp_path / "backups"
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "manual.txt").write_text("conteudo operacional", encoding="utf-8")
    fake_store = BackupFakeStore(str(knowledge_dir))
    monkeypatch.setattr(services, "store", fake_store)
    monkeypatch.setattr(services, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(services, "KNOWLEDGE_DIR", str(knowledge_dir))
    monkeypatch.setenv("BACKUP_DIR", str(backup_dir))
    monkeypatch.setenv("BACKUP_EMAIL_ENABLED", "0")
    monkeypatch.setattr(
        admin_module,
        "_postgres_table_rows",
        lambda: {
            "app_users": [{"username": "admin@porto.pt", "role": "admin"}],
            "conversations": [{"id": "conv-1", "username": "admin@porto.pt"}],
            "messages": [{"id": "msg-1", "conversation_id": "conv-1", "channel": "whatsapp"}],
            "channel_events": [{"id": "evt-1", "channel": "whatsapp"}],
        },
    )

    with app.app_context():
        record = admin_module._create_system_backup(
            created_by="admin@porto.pt",
            send_email=True,
            source="manual",
        )

    backup_path = backup_dir / record["filename"]
    assert backup_path.exists()
    assert record["filename"].endswith(".zip")
    assert record["counts"]["users"] == 1
    assert record["counts"]["whatsapp_messages"] == 1

    with zipfile.ZipFile(backup_path) as archive:
        assert set(archive.namelist()) == {"backup.json", "README.md"}
        payload = json.loads(archive.read("backup.json").decode("utf-8"))
        readme = archive.read("README.md").decode("utf-8")

    assert payload["kind"] == admin_module.SYSTEM_DATABASE_EXPORT_KIND
    assert payload["backup"]["filename"] == record["filename"]
    assert payload["payload"]["tables"]["app_users"][0]["username"] == "admin@porto.pt"
    assert "Conversas, mensagens" in readme
    assert "backup.json" in readme


def test_json_upload_accepts_backup_zip(tmp_path, monkeypatch) -> None:
    app = Flask(__name__)
    payload = {"kind": admin_module.SYSTEM_DATABASE_EXPORT_KIND, "payload": {"tables": {}}}
    package = BytesIO()
    with zipfile.ZipFile(package, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("backup.json", json.dumps(payload))
        archive.writestr("README.md", "detalhes")
    package.seek(0)

    with app.test_request_context(
        "/admin/system/import",
        method="POST",
        data={"system_database_file": (package, "backup.zip")},
        content_type="multipart/form-data",
    ):
        parsed = admin_module._read_admin_json_upload("system_database_file")

    assert parsed == payload
