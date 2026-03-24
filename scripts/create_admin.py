#!/usr/bin/env python3
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from storage import create_store


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Uso: python3 scripts/create_admin.py <email> <password>")

    load_dotenv()
    email = sys.argv[1].strip().lower()
    password = sys.argv[2]

    store = create_store(
        data_dir=os.path.join(BASE_DIR, "data"),
        knowledge_dir=os.path.join(BASE_DIR, "knowledge"),
    )

    existing = store.get_user_profile(email)
    if existing:
        result = store.set_user_role(email, "admin")
        print(f"Promovido para admin: {result['username']}")
        return

    result = store.create_user(
        username=email,
        password=password,
        role="admin",
        email=email,
    )
    print(f"Admin criado: {result['username']}")


if __name__ == "__main__":
    main()
