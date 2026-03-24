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
        raise SystemExit("Uso: python3 scripts/set_user_role.py <email> <admin|agente|piloto>")

    load_dotenv()
    username = sys.argv[1].strip().lower()
    role = sys.argv[2].strip().lower()

    store = create_store(
        data_dir=os.path.join(BASE_DIR, "data"),
        knowledge_dir=os.path.join(BASE_DIR, "knowledge"),
    )
    result = store.set_user_role(username=username, role=role)
    print(f"{result['username']} -> {result['role']}")


if __name__ == "__main__":
    main()
