#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from integrations.whatsapp_cloud import WhatsAppCloudService


def _first_configured_target() -> str:
    explicit_target = os.getenv("WHATSAPP_TEST_TO", "").strip()
    if explicit_target:
        return explicit_target
    allowed_numbers = os.getenv("WHATSAPP_ALLOWED_NUMBERS", "").strip()
    if not allowed_numbers:
        return ""
    return allowed_numbers.split(",")[0].strip()


def _build_parser(default_target: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Envia uma mensagem simples de teste pela WhatsApp Cloud API.",
    )
    parser.add_argument(
        "--to",
        default=default_target,
        help="Número de destino em formato internacional. Ex.: 351962063664",
    )
    parser.add_argument(
        "--message",
        default=f"Teste PRAGtico OK às {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        help="Texto a enviar.",
    )
    parser.add_argument(
        "--template-name",
        default="",
        help="Template WhatsApp a enviar. Se vazio, usa WHATSAPP_WELCOME_TEMPLATE_NAME quando existir.",
    )
    parser.add_argument(
        "--template-language",
        default="",
        help="Idioma do template. Ex.: pt_PT (ou WHATSAPP_WELCOME_TEMPLATE_LANGUAGE).",
    )
    parser.add_argument(
        "--force-text",
        action="store_true",
        help="Força envio em texto simples, mesmo com template configurado.",
    )
    return parser


def main() -> int:
    load_dotenv(ROOT_DIR / ".env")
    parser = _build_parser(_first_configured_target())
    args = parser.parse_args()

    service = WhatsAppCloudService.from_env()
    target_number = service.normalize_phone_number(args.to)

    if not target_number:
        parser.error(
            "Falta o número de destino. Usa --to 351962063664 ou define WHATSAPP_TEST_TO na .env."
        )

    if not service.send_ready:
        missing = []
        if not service.enabled:
            missing.append("WHATSAPP_ENABLED=1")
        if not service.access_token:
            missing.append("WHATSAPP_ACCESS_TOKEN")
        if not service.phone_number_id:
            missing.append("WHATSAPP_PHONE_NUMBER_ID")
        parser.error("Configuração incompleta para envio WhatsApp: " + ", ".join(missing))

    template_name = (
        args.template_name.strip()
        or os.getenv("WHATSAPP_TEST_TEMPLATE_NAME", "").strip()
        or getattr(service, "welcome_template_name", "").strip()
    )
    template_language = (
        args.template_language.strip()
        or os.getenv("WHATSAPP_TEST_TEMPLATE_LANGUAGE", "").strip()
        or getattr(service, "welcome_template_language", "pt_PT").strip()
        or "pt_PT"
    )
    use_template = bool(template_name) and not args.force_text
    if use_template:
        response = service.send_template_message(
            target_number,
            template_name=template_name,
            language_code=template_language,
        )
    else:
        response = service.send_text_message(target_number, args.message)
    message_id = ""
    if isinstance(response, dict):
        messages = response.get("messages") or []
        if messages and isinstance(messages[0], dict):
            message_id = str(messages[0].get("id") or "").strip()

    print("Mensagem enviada com sucesso.")
    print(f"Modo: {'template' if use_template else 'text'}")
    if use_template:
        print(f"Template: {template_name} ({template_language})")
    print(f"Destino: {target_number}")
    print(f"Phone Number ID: {service.phone_number_id}")
    if message_id:
        print(f"Message ID: {message_id}")
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
