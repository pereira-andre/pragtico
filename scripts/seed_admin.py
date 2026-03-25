#!/usr/bin/env python3
"""Seed the default admin account if it doesn't exist.

Reads from environment variables:
    ADMIN_EMAIL    — default: admin@porto.pt
    ADMIN_PASSWORD — default: 123456

Usage:
    python3 scripts/seed_admin.py

Or called automatically from run_local.sh.
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from storage import create_store


def seed_admin() -> None:
    load_dotenv()

    email = os.getenv("ADMIN_EMAIL", "admin@porto.pt").strip().lower()
    password = os.getenv("ADMIN_PASSWORD", "123456")

    store = create_store(
        data_dir=os.path.join(BASE_DIR, "data"),
        knowledge_dir=os.path.join(BASE_DIR, "knowledge"),
    )

    existing = store.get_user_profile(email)
    if existing:
        if (existing.get("role") or "").lower() != "admin":
            store.set_user_role(email, "admin")
            print(f"[seed] Utilizador {email} promovido a admin.")
        else:
            print(f"[seed] Admin {email} já existe.")
        return

    result = store.create_user(
        username=email,
        password=password,
        role="admin",
        full_name="Administrador",
        organization="APSS",
        email=email,
        phone="",
    )
    print(f"[seed] Admin criado: {result['username']} (password: {password})")


if __name__ == "__main__":
    seed_admin()
