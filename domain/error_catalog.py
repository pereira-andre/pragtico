from __future__ import annotations

import json
from typing import Any


ERROR_DEFINITIONS: dict[str, dict[str, Any]] = {
    "EMPTY_QUESTION": {
        "code": 1001,
        "category": "validation",
        "message": "Pergunta vazia.",
        "user_message": "Escreve a pergunta ou comando que queres enviar.",
    },
    "CHAT_RUNTIME_FAILED": {
        "code": 9001,
        "category": "internal",
        "message": "Nao foi possivel processar a mensagem.",
        "user_message": "Erro inesperado ao processar a mensagem. Contacta o suporte com este codigo.",
    },
    "PENDING_ACTION_FAILED": {
        "code": 5001,
        "category": "business_rule",
        "message": "Nao foi possivel aplicar a acao operacional.",
        "user_message": "Nao foi possivel aplicar a acao operacional. Confirma os dados e tenta novamente.",
    },
    "UNHANDLED_EXCEPTION": {
        "code": 9000,
        "category": "internal",
        "message": "Erro inesperado.",
        "user_message": "Erro inesperado. Contacta o suporte com este codigo.",
    },
}


def error_definition(error_key: str) -> dict[str, Any]:
    return ERROR_DEFINITIONS.get(error_key, ERROR_DEFINITIONS["UNHANDLED_EXCEPTION"])


def error_ref(error_key: str) -> str:
    return f"#ERR-{int(error_definition(error_key)['code']):04d}"


def error_payload(
    error_key: str,
    *,
    detail: str = "",
    expose_detail: bool = False,
) -> dict[str, Any]:
    definition = error_definition(error_key)
    payload = {
        "error": definition["message"],
        "error_code": definition["code"],
        "error_ref": error_ref(error_key),
    }
    if detail and expose_detail:
        payload["detail"] = detail
    return payload


def user_error_message(error_key: str, *, detail: str = "", channel: str = "web") -> str:
    definition = error_definition(error_key)
    message = definition.get("user_message") or definition["message"]
    ref = error_ref(error_key)
    if channel == "whatsapp":
        text = f"*{ref}*\n{message}"
    else:
        text = f"{ref} {message}"
    if detail:
        text = f"{text}\nDetalhe: {detail}"
    return text


def log_error_event(logger, error_key: str, *, detail: str = "", **context: Any) -> None:
    definition = error_definition(error_key)
    payload = {
        "level": "error",
        "error_code": definition["code"],
        "error_ref": error_ref(error_key),
        "error_key": error_key,
        "category": definition.get("category", "internal"),
        "message": detail or definition["message"],
        **{key: value for key, value in context.items() if value not in (None, "")},
    }
    logger.error(json.dumps(payload, ensure_ascii=False, default=str))
