#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from integrations.whatsapp_cloud import WhatsAppCloudService


def _default_icon_path() -> Path:
    return ROOT_DIR / "img" / "icon.png"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Atualiza o business profile do WhatsApp Cloud API, incluindo a foto do perfil.",
    )
    parser.add_argument(
        "--picture",
        default=str(_default_icon_path()),
        help="Caminho da imagem a usar no perfil. Por omissão: img/icon.png",
    )
    parser.add_argument("--about", default="", help="Texto do campo About do perfil.")
    parser.add_argument("--description", default="", help="Descrição do negócio.")
    parser.add_argument("--address", default="", help="Morada do negócio.")
    parser.add_argument("--email", default="", help="Email de contacto.")
    parser.add_argument(
        "--website",
        action="append",
        default=[],
        help="Website a associar ao perfil. Pode ser usado até duas vezes.",
    )
    parser.add_argument("--vertical", default="", help="Vertical/indústria do negócio.")
    parser.add_argument(
        "--skip-picture",
        action="store_true",
        help="Não atualiza a foto; apenas envia os restantes campos indicados.",
    )
    parser.add_argument(
        "--show-profile",
        action="store_true",
        help="Mostra o business profile devolvido pela Meta depois da atualização.",
    )
    return parser


def main() -> int:
    load_dotenv(ROOT_DIR / ".env")
    parser = _build_parser()
    args = parser.parse_args()

    try:
        service = WhatsAppCloudService.from_env()
        if not service.business_profile_ready:
            missing = []
            if not service.enabled:
                missing.append("WHATSAPP_ENABLED=1")
            if not service.access_token:
                missing.append("WHATSAPP_ACCESS_TOKEN")
            if not service.phone_number_id:
                missing.append("WHATSAPP_PHONE_NUMBER_ID")
            parser.error("Configuração incompleta para business profile: " + ", ".join(missing))

        picture_path = Path(args.picture).expanduser()
        profile_picture_handle = ""
        if not args.skip_picture:
            profile_picture_handle = service.upload_profile_picture(picture_path)

        response = service.update_business_profile(
            about=args.about or None,
            address=args.address or None,
            description=args.description or None,
            email=args.email or None,
            profile_picture_handle=profile_picture_handle or None,
            vertical=args.vertical or None,
            websites=args.website or None,
        )

        print("Business profile atualizado com sucesso.")
        print(f"Phone Number ID: {service.phone_number_id}")
        if profile_picture_handle:
            print(f"Profile picture handle: {profile_picture_handle}")
        print(json.dumps(response, ensure_ascii=False, indent=2))

        if args.show_profile:
            profile = service.get_business_profile(
                fields=[
                    "about",
                    "address",
                    "description",
                    "email",
                    "profile_picture_url",
                    "websites",
                    "vertical",
                ]
            )
            print("\nPerfil atual:")
            print(json.dumps(profile, ensure_ascii=False, indent=2))
        return 0
    except requests.HTTPError as exc:
        print("Falha ao atualizar o business profile do WhatsApp.", file=sys.stderr)
        response = exc.response
        if response is not None:
            try:
                print(json.dumps(response.json(), ensure_ascii=False, indent=2), file=sys.stderr)
            except ValueError:
                print(response.text, file=sys.stderr)
        else:
            print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Falha ao atualizar o business profile do WhatsApp: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
