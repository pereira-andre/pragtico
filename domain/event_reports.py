from __future__ import annotations

import json
import mimetypes
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EVENT_REPORT_TAGS = {
    "AVARIA",
    "FALHA",
    "DANO",
    "FALTA",
    "INCIDENTE",
    "OBSERVACAO",
    "OBSERVAÇÃO",
}

NO_PHOTO_REPLIES = {
    "nao",
    "não",
    "n",
    "sem foto",
    "sem anexo",
    "no",
}

CANCEL_REPLIES = {
    "cancelar",
    "cancela",
    "anular",
    "anula",
    "desistir",
}

LOCATION_PREFIXES = {
    "barra",
    "cais",
    "canal",
    "doca",
    "estaleiro",
    "fundeadouro",
    "lisnave",
    "navio",
    "ponte",
    "rio",
    "terminal",
    "tms",
    "tms1",
    "tms2",
}

LOCATION_JOINERS = {
    "da",
    "das",
    "de",
    "do",
    "dos",
}

LOCATION_WORDS = {
    "barra",
    "norte",
    "sul",
    "sado",
    "troia",
    "tróia",
    "mitrena",
    "lisnave",
    "secíl",
    "secil",
    "sapec",
    "tanquisado",
    "tepor",
    "teporset",
    "eco-oil",
    "ecooil",
    "tms",
    "tms1",
    "tms2",
}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _lookup_key(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_only = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", ascii_only.casefold()).strip()


def normalize_event_description(description: str) -> str:
    clean = _clean_text(description)
    if not clean:
        return ""
    clean = clean[0].upper() + clean[1:]
    if clean[-1] not in ".!?":
        clean += "."
    return clean


def _is_location_token(token: str, local_tokens: list[str]) -> bool:
    clean = token.strip()
    lookup = _lookup_key(clean)
    if not clean:
        return False
    if lookup in LOCATION_JOINERS or lookup in LOCATION_WORDS:
        return True
    if any(char.isdigit() for char in clean):
        return True
    if clean.isupper() and len(clean) > 1:
        return True
    if clean[:1].isupper() and len(local_tokens) < 6:
        return True
    return False


def _split_location_description(rest: str) -> tuple[str, str]:
    tokens = _clean_text(rest).split()
    if not tokens:
        return "", ""
    first = _lookup_key(tokens[0])
    if first in LOCATION_PREFIXES:
        local_tokens = [tokens[0]]
        for token in tokens[1:]:
            if len(local_tokens) == 1:
                local_tokens.append(token)
                continue
            if len(local_tokens) >= 7:
                break
            if _is_location_token(token, local_tokens):
                local_tokens.append(token)
                continue
            break
        description = " ".join(tokens[len(local_tokens):])
        return " ".join(local_tokens), description

    local_token_count = 2 if len(tokens) >= 3 and any(char.isdigit() for char in tokens[1]) else 1
    return " ".join(tokens[:local_token_count]), " ".join(tokens[local_token_count:])


def _parse_labelled_event_report(argument: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    label_map = {
        "tag": "tag",
        "tipo": "tag",
        "local": "local",
        "descrição": "description",
        "descricao": "description",
        "description": "description",
    }
    for line in str(argument or "").splitlines():
        match = re.match(r"^\s*([^:]+)\s*:\s*(.+?)\s*$", line)
        if not match:
            continue
        label = _lookup_key(match.group(1))
        key = label_map.get(label)
        if key:
            fields[key] = _clean_text(match.group(2))
    return fields


def parse_event_report_command(argument: str) -> dict[str, Any]:
    raw = str(argument or "").strip()
    labelled = _parse_labelled_event_report(raw)
    if labelled:
        tag = _clean_text(labelled.get("tag")).upper()
        local = _clean_text(labelled.get("local"))
        description = _clean_text(labelled.get("description"))
    else:
        pipe_parts = [part.strip() for part in raw.split("|") if part.strip()]
        if len(pipe_parts) >= 3:
            tag = _clean_text(pipe_parts[0]).upper()
            local = _clean_text(pipe_parts[1])
            description = _clean_text(" | ".join(pipe_parts[2:]))
        else:
            head, _, rest = raw.partition(" ")
            tag = _clean_text(head).upper()
            local, description = _split_location_description(rest)

    if tag == "OBSERVAÇÃO":
        tag = "OBSERVACAO"

    missing = []
    if not tag:
        missing.append("TAG")
    if not local:
        missing.append("LOCAL")
    if not description:
        missing.append("DESCRIPTION")

    return {
        "ok": not missing,
        "missing": missing,
        "draft": {
            "tag": tag,
            "local": local,
            "description_original": description,
        },
    }


def build_event_report_template(missing: list[str] | None = None) -> str:
    missing_text = f"\n\nCampos em falta: {', '.join(missing)}" if missing else ""
    return (
        "Usa este formato:\n"
        "/reportar_evento TAG | LOCAL | DESCRIPTION\n\n"
        "Exemplo:\n"
        "/reportar_evento AVARIA | cais Teporset | o guincho do cais nao esta a funcionar\n\n"
        "Tags habituais: AVARIA, FALHA, DANO, FALTA, INCIDENTE, OBSERVACAO."
        f"{missing_text}"
    )


def pending_event_report_key(
    *,
    channel: str,
    username: str,
    conversation_id: str,
    channel_user_id: str = "",
) -> str:
    clean_channel = _lookup_key(channel) or "web"
    clean_channel_user = _clean_text(channel_user_id)
    if clean_channel == "whatsapp" and clean_channel_user:
        return f"event_report_pending:whatsapp:{clean_channel_user}"
    return f"event_report_pending:{clean_channel}:{_clean_text(username)}:{_clean_text(conversation_id)}"


def event_reports_root() -> Path:
    return Path(os.getenv("EVENT_REPORTS_DIR", "reportes")).expanduser()


def _events_file(root: Path) -> Path:
    return root / "eventos.json"


def _read_events(root: Path) -> list[dict[str, Any]]:
    path = _events_file(root)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def _write_events(root: Path, events: list[dict[str, Any]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    path = _events_file(root)
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _next_event_id(events: list[dict[str, Any]], now: datetime) -> str:
    prefix = f"EVT-{now.strftime('%Y%m%d')}-"
    sequence = 1
    for event in events:
        event_id = str(event.get("id") or "")
        if not event_id.startswith(prefix):
            continue
        suffix = event_id[len(prefix):]
        if suffix.isdigit():
            sequence = max(sequence, int(suffix) + 1)
    return f"{prefix}{sequence:03d}"


def _safe_photo_extension(filename: str = "", mime_type: str = "") -> str:
    suffix = Path(str(filename or "")).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".heic"}:
        return suffix
    guessed = mimetypes.guess_extension(str(mime_type or "").split(";")[0].strip())
    if guessed in {".jpg", ".jpeg", ".png", ".webp", ".heic"}:
        return guessed
    return ".jpg"


def register_event_report(
    draft: dict[str, Any],
    *,
    username: str,
    role: str,
    user_label: str,
    description_processed: str = "",
    attachment_bytes: bytes | None = None,
    attachment_mime_type: str = "",
    attachment_filename: str = "",
    media_id: str = "",
) -> dict[str, Any]:
    root = event_reports_root()
    now = datetime.now(timezone.utc)
    events = _read_events(root)
    event_id = _next_event_id(events, now)

    photo_path = ""
    if attachment_bytes:
        photo_dir = root / "fotos"
        photo_dir.mkdir(parents=True, exist_ok=True)
        extension = _safe_photo_extension(attachment_filename, attachment_mime_type)
        photo_file = photo_dir / f"{event_id}{extension}"
        photo_file.write_bytes(attachment_bytes)
        photo_path = str(photo_file)

    original_description = _clean_text(draft.get("description_original"))
    processed_description = _clean_text(description_processed) or normalize_event_description(original_description)
    event = {
        "id": event_id,
        "tag": _clean_text(draft.get("tag")).upper(),
        "local": _clean_text(draft.get("local")),
        "descricao_original": original_description,
        "descricao_processada": processed_description,
        "utilizador": _clean_text(user_label) or _clean_text(username),
        "username": _clean_text(username),
        "role": _clean_text(role),
        "timestamp": now.isoformat(),
        "foto_path": photo_path,
        "foto_mime_type": _clean_text(attachment_mime_type),
        "media_id": _clean_text(media_id),
    }
    events.append(event)
    _write_events(root, events)
    return {
        **event,
        "archive_path": str(_events_file(root)),
    }


def format_event_report_answer(event: dict[str, Any]) -> str:
    has_photo = bool((event or {}).get("foto_path"))
    lines = [
        "Reporte de evento registado",
        "",
        f"Referencia: #{event.get('id', '--')}",
        f"Tipo: {event.get('tag', '--')}",
        f"Local: {event.get('local', '--')}",
        f"Reportado por: {event.get('utilizador', '--')}",
        "",
        "Descricao:",
        str(event.get("descricao_processada") or event.get("descricao_original") or "--"),
        "",
        f"Anexo: {'1 foto guardada' if has_photo else 'sem foto'}",
        f"Arquivo: {event.get('archive_path', '--')}",
    ]
    return "\n".join(lines)


def is_no_photo_reply(text: str) -> bool:
    return _lookup_key(text) in NO_PHOTO_REPLIES


def is_cancel_reply(text: str) -> bool:
    return _lookup_key(text) in CANCEL_REPLIES
