"""Structured admin-governed operational experience loaded from knowledge JSON."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from core.validators import validate_operational_feedback_status
from domain.document_processing import iso_now


PRACTICE_EXPERIENCE_STATE_KEY = "practice_maneuver_experience"
PRACTICE_EXPERIENCE_VERSION = 1
PRACTICE_EXPERIENCE_ACTIVE_STATUSES = {"approved", "avoid"}
PRACTICE_EXPERIENCE_KIND = "pragtico.practice_maneuver_experience"
PRACTICE_EXPERIENCE_KNOWLEDGE_FILENAME = "practice_maneuver_experience.json"


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _status_meta(value: str) -> tuple[str, str]:
    clean = (value or "").strip().lower()
    if clean == "approved":
        return "Aprovado", "online"
    if clean == "avoid":
        return "Evitar", "degraded"
    if clean == "review":
        return "Rever", "degraded"
    return "Por rever", "neutral"


def _read_json_source(source: Any) -> str:
    if isinstance(source, Path):
        return source.read_text(encoding="utf-8-sig")
    if isinstance(source, (bytes, bytearray)):
        return bytes(source).decode("utf-8-sig")
    if hasattr(source, "read"):
        data = source.read()
        return data.decode("utf-8-sig") if isinstance(data, (bytes, bytearray)) else str(data)
    return str(source or "")


def _record_maneuver_types_label(records: list[dict]) -> str:
    counter = Counter(_clean_text(item.get("maneuver_type_label") or item.get("maneuver_type")) for item in records)
    return ", ".join(f"{label} ({count})" for label, count in counter.most_common() if label)


def load_practice_experience_records_from_json(source: Any) -> tuple[list[dict], dict]:
    """Load generated practice maneuver experience without requiring spreadsheet dependencies."""
    raw_payload = _read_json_source(source)
    if not raw_payload.strip():
        raise ValueError("O JSON de experiência prática está vazio.")
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON de experiência prática inválido: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("O JSON de experiência prática tem de conter um objeto principal.")
    kind = _clean_text(payload.get("kind"))
    if kind and kind != PRACTICE_EXPERIENCE_KIND:
        raise ValueError(f"Tipo de JSON de experiência prática não suportado: {kind}.")
    raw_records = payload.get("records")
    if not isinstance(raw_records, list):
        raise ValueError("O JSON de experiência prática tem de conter a lista 'records'.")

    records: list[dict] = []
    skipped = 0
    for item in raw_records:
        if not isinstance(item, dict) or not _clean_text(item.get("id")):
            skipped += 1
            continue
        record = dict(item)
        record["source_type"] = "practice_import"
        record["source_label"] = record.get("source_label") or "Experiência prática importada"
        records.append(record)
    if not records:
        raise ValueError("O JSON de experiência prática não contém padrões válidos.")

    stats = dict(payload.get("stats") or {})
    stats["pattern_count"] = len(records)
    stats["skipped_records"] = skipped
    stats["raw_rows"] = stats.get("raw_rows") or len(records)
    stats["comments_count"] = stats.get("comments_count") or sum(
        1 for item in records if (item.get("practice_metrics") or {}).get("comments")
    )
    stats["maneuver_types_label"] = stats.get("maneuver_types_label") or _record_maneuver_types_label(records)
    return records, stats


def prepare_practice_experience_records_for_import(
    records: list[dict],
    *,
    source_filename: str,
    imported_by: str,
    feedback_status: str = "approved",
) -> list[dict]:
    """Apply the admin import decision to generated practice records before storing them."""
    feedback_status = validate_operational_feedback_status(feedback_status)
    now = iso_now()
    prepared: list[dict] = []
    for item in records:
        record = dict(item)
        record["source_filename"] = source_filename
        record["source_type"] = "practice_import"
        record["source_label"] = record.get("source_label") or "Experiência prática importada"
        record["feedback_status"] = feedback_status
        record["feedback_note"] = (
            "Carregado do JSON de conhecimento e aprovado pelo admin."
            if feedback_status == "approved"
            else ""
        )
        record["feedback_updated_by"] = _clean_text(imported_by)
        record["feedback_updated_at"] = now
        record["created_at"] = record.get("created_at") or now
        record["updated_at"] = now
        if not record.get("latest_event_at"):
            record["latest_event_at"] = now
        prepared.append(record)
    return prepared


def practice_experience_state(store: Any) -> dict:
    state = store.get_runtime_state(PRACTICE_EXPERIENCE_STATE_KEY) or {}
    records = state.get("records") or []
    return {
        "version": PRACTICE_EXPERIENCE_VERSION,
        "records": [item for item in records if isinstance(item, dict)],
        "sources": state.get("sources") or {},
        "updated_at": state.get("updated_at") or "",
        "updated_by": state.get("updated_by") or "",
    }


def list_practice_experience_records(
    store: Any,
    *,
    feedback_statuses: set[str] | None = None,
    limit: int | None = None,
) -> list[dict]:
    records = list(practice_experience_state(store)["records"])
    if feedback_statuses is not None:
        allowed = {(value or "").strip().lower() for value in feedback_statuses}
        records = [item for item in records if (item.get("feedback_status") or "").strip().lower() in allowed]
    records.sort(
        key=lambda item: (
            item.get("updated_at") or item.get("created_at") or "",
            int((item.get("practice_metrics") or {}).get("case_count") or 0),
        ),
        reverse=True,
    )
    decorated = []
    for item in records:
        label, badge = _status_meta(item.get("feedback_status", ""))
        metrics = item.get("practice_metrics") or {}
        decorated.append(
            {
                **item,
                "feedback_status_label": label,
                "feedback_badge": badge,
                "case_count": int(metrics.get("case_count") or 0),
                "duration_median_label": metrics.get("duration_median_label") or "--",
                "tug_distribution_label": metrics.get("tug_distribution_label") or "--",
                "vessel_examples_label": ", ".join(metrics.get("vessel_examples") or []) or "--",
                "comments_label": " | ".join(metrics.get("comments") or []) or "--",
                "profile_label": (
                    f"{(item.get('vessel_snapshot') or {}).get('type') or '--'} · "
                    f"LOA {metrics.get('loa_band') or '--'} · "
                    f"Boca {metrics.get('beam_band') or '--'} · "
                    f"Calado {metrics.get('draft_band') or '--'}"
                ),
                "route_label": f"{item.get('origin_label') or '--'} → {item.get('destination_label') or '--'}",
            }
        )
    if limit is not None:
        return decorated[: max(limit, 0)]
    return decorated


def save_practice_experience_records(
    store: Any,
    records: list[dict],
    *,
    source_filename: str,
    updated_by: str,
    replace_source: bool = True,
) -> dict:
    state = practice_experience_state(store)
    existing = list(state["records"])
    if replace_source:
        existing = [item for item in existing if item.get("source_filename") != source_filename]
    by_id = {item.get("id"): item for item in existing if item.get("id")}
    for record in records:
        by_id[record["id"]] = record
    next_records = list(by_id.values())
    now = iso_now()
    sources = dict(state.get("sources") or {})
    sources[source_filename] = {
        "record_count": len(records),
        "updated_by": updated_by,
        "updated_at": now,
    }
    payload = {
        "version": PRACTICE_EXPERIENCE_VERSION,
        "records": next_records,
        "sources": sources,
        "updated_at": now,
        "updated_by": updated_by,
    }
    store.set_runtime_state(PRACTICE_EXPERIENCE_STATE_KEY, payload)
    return payload


def update_practice_experience_feedback(
    store: Any,
    record_id: str,
    *,
    feedback_status: str,
    feedback_note: str = "",
    feedback_by: str = "",
) -> dict:
    feedback_status = validate_operational_feedback_status(feedback_status)
    state = practice_experience_state(store)
    now = iso_now()
    updated = None
    for record in state["records"]:
        if record.get("id") != record_id:
            continue
        record["feedback_status"] = feedback_status
        record["feedback_note"] = _clean_text(feedback_note)
        record["feedback_updated_by"] = _clean_text(feedback_by)
        record["feedback_updated_at"] = now
        record["updated_at"] = now
        updated = record
        break
    if not updated:
        raise ValueError("Experiência prática não encontrada.")
    state["updated_at"] = now
    state["updated_by"] = _clean_text(feedback_by)
    store.set_runtime_state(PRACTICE_EXPERIENCE_STATE_KEY, state)
    return updated


def delete_practice_experience_record(store: Any, record_id: str, *, deleted_by: str = "") -> int:
    state = practice_experience_state(store)
    records = [item for item in state["records"] if item.get("id") != record_id]
    removed = len(state["records"]) - len(records)
    if not removed:
        return 0
    state["records"] = records
    state["updated_at"] = iso_now()
    state["updated_by"] = _clean_text(deleted_by)
    store.set_runtime_state(PRACTICE_EXPERIENCE_STATE_KEY, state)
    return removed


def clear_practice_experience_records(store: Any, *, cleared_by: str = "") -> int:
    state = practice_experience_state(store)
    removed = len(state["records"])
    store.set_runtime_state(
        PRACTICE_EXPERIENCE_STATE_KEY,
        {
            "version": PRACTICE_EXPERIENCE_VERSION,
            "records": [],
            "sources": {},
            "updated_at": iso_now(),
            "updated_by": _clean_text(cleared_by),
        },
    )
    return removed
