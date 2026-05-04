from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import re
import zipfile

from flask import Flask

from blueprints import admin as admin_module
from core import services


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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


class FakeAuthService:
    def authenticate(self, username: str, password: str) -> dict | None:
        if username == "admin@porto.pt" and password == "valid-password":
            return {"username": username, "role": "admin"}
        return None


class FakeCursor:
    def __init__(self) -> None:
        self.rowcount = 0
        self.statements: list[tuple[str, tuple]] = []
        self._selected_admin = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def execute(self, sql: str, params: tuple = ()) -> None:
        clean_sql = " ".join(sql.split())
        self.statements.append((clean_sql, params))
        if clean_sql.startswith("SELECT username, role FROM app_users"):
            self._selected_admin = {"username": "admin@porto.pt", "role": "admin"}
            self.rowcount = 1
            return
        if clean_sql.startswith("DELETE FROM app_users WHERE username <>"):
            self.rowcount = 2
            return
        if clean_sql.startswith("DELETE FROM "):
            self.rowcount = 3
            return
        self.rowcount = 0

    def fetchone(self):
        return self._selected_admin


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self.cursor_obj = cursor
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self.cursor_obj

    def commit(self) -> None:
        self.committed = True


class WipeFakeStore:
    backend_name = "fake-postgres"

    def __init__(self) -> None:
        self.cursor_obj = FakeCursor()
        self.connection = FakeConnection(self.cursor_obj)

    def _connect(self) -> FakeConnection:
        return self.connection


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


def test_admin_backups_post_forms_include_csrf_token() -> None:
    template = (PROJECT_ROOT / "templates" / "admin_backups.html").read_text(encoding="utf-8")
    post_forms = re.findall(r"<form\b[^>]*method=\"post\"[\s\S]*?</form>", template)

    assert post_forms
    assert all('name="csrf_token"' in form for form in post_forms)


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


def test_wipe_confirmation_requires_current_admin_password(monkeypatch) -> None:
    app = Flask(__name__)
    app.secret_key = "test"
    monkeypatch.setattr(services, "auth_service", FakeAuthService())

    with app.test_request_context("/"):
        from flask import session

        session["username"] = "admin@porto.pt"
        session["role"] = "admin"

        try:
            admin_module._validate_database_wipe_confirmation(
                password="wrong",
                phrase=admin_module.DATABASE_WIPE_CONFIRMATION_PHRASE,
                checkbox=True,
            )
        except ValueError as exc:
            assert "Password inválida" in str(exc)
        else:
            raise AssertionError("invalid password accepted")


def test_wipe_confirmation_requires_exact_phrase(monkeypatch) -> None:
    app = Flask(__name__)
    app.secret_key = "test"
    monkeypatch.setattr(services, "auth_service", FakeAuthService())

    with app.test_request_context("/"):
        from flask import session

        session["username"] = "admin@porto.pt"
        session["role"] = "admin"

        try:
            admin_module._validate_database_wipe_confirmation(
                password="valid-password",
                phrase="limpar",
                checkbox=True,
            )
        except ValueError as exc:
            assert "Frase de confirmação inválida" in str(exc)
        else:
            raise AssertionError("invalid confirmation phrase accepted")


def test_wipe_database_preserves_current_admin(monkeypatch) -> None:
    fake_store = WipeFakeStore()
    monkeypatch.setattr(services, "store", fake_store)

    stats = admin_module._wipe_database_preserving_admin("admin@porto.pt")

    statements = [sql for sql, _params in fake_store.cursor_obj.statements]
    assert any(sql == "DELETE FROM app_users WHERE username <> %s" for sql in statements)
    assert not any(sql == "DELETE FROM app_users" for sql in statements)
    assert fake_store.connection.committed is True
    assert stats["preserved_admin"] == "admin@porto.pt"
    assert stats["records"] > 0
