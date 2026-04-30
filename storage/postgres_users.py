"""User account persistence for the PostgreSQL store."""

from __future__ import annotations

import json
from typing import Dict, List, Optional

from werkzeug.security import check_password_hash, generate_password_hash

from .constants import PASSWORD_HASH_METHOD
from .utils import _normalize_user_profile_payload, _normalize_username


class PostgresUserMixin:
    def list_users(self) -> List[Dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        username, role, full_name, organization, email, phone,
                        whatsapp_number, whatsapp_opt_in, whatsapp_opt_in_at, profile_completed_at
                    FROM app_users
                    ORDER BY username
                    """
                )
                rows = cur.fetchall()
        return [_normalize_user_profile_payload(row) for row in rows]

    def create_user(
        self,
        username: str,
        password: str,
        role: str,
        full_name: str = "",
        organization: str = "",
        email: str = "",
        phone: str = "",
        whatsapp_number: str = "",
        whatsapp_opt_in: bool = False,
        whatsapp_opt_in_at: str = "",
    ) -> Dict:
        username = _normalize_username(username)
        if len(username) < 3:
            raise ValueError("O email deve ter pelo menos 3 caracteres.")
        if len(password) < 6:
            raise ValueError("A password deve ter pelo menos 6 caracteres.")
        if role not in {"admin", "agente", "piloto"}:
            raise ValueError("Role invalido.")
        profile = _normalize_user_profile_payload(
            {
                "username": username,
                "role": role,
                "full_name": full_name,
                "organization": organization,
                "email": email,
                "phone": phone,
                "whatsapp_number": whatsapp_number,
                "whatsapp_opt_in": whatsapp_opt_in,
                "whatsapp_opt_in_at": whatsapp_opt_in_at,
            }
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM app_users WHERE username = %s", (username,))
                if cur.fetchone():
                    raise ValueError("Esse utilizador ja existe.")
                cur.execute(
                    """
                    INSERT INTO app_users (
                        username, password_hash, role, full_name, organization, email, phone,
                        whatsapp_number, whatsapp_opt_in, whatsapp_opt_in_at, profile_completed_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        username,
                        generate_password_hash(password, method=PASSWORD_HASH_METHOD),
                        role,
                        profile["full_name"],
                        profile["organization"],
                        profile["email"],
                        profile["phone"],
                        profile["whatsapp_number"],
                        profile["whatsapp_opt_in"],
                        profile["whatsapp_opt_in_at"],
                        profile["profile_completed_at"],
                    ),
                )
            conn.commit()
        return profile

    def authenticate(self, username: str, password: str) -> Optional[Dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        username, password_hash, role, full_name, organization, email, phone,
                        whatsapp_number, whatsapp_opt_in, whatsapp_opt_in_at, profile_completed_at
                    FROM app_users
                    WHERE username = %s
                    """,
                    (_normalize_username(username),),
                )
                user = cur.fetchone()
        if user and check_password_hash(user["password_hash"], password):
            return _normalize_user_profile_payload(user)
        return None

    def get_user_profile(self, username: str) -> Optional[Dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        username, role, full_name, organization, email, phone,
                        whatsapp_number, whatsapp_opt_in, whatsapp_opt_in_at, profile_completed_at
                    FROM app_users
                    WHERE username = %s
                    """,
                    (_normalize_username(username),),
                )
                row = cur.fetchone()
        return _normalize_user_profile_payload(row) if row else None

    def rename_user(self, username: str, new_username: str) -> Dict:
        current_username = _normalize_username(username)
        target_username = _normalize_username(new_username)
        if len(target_username) < 3:
            raise ValueError("O email de acesso deve ter pelo menos 3 caracteres.")
        if current_username == target_username:
            profile = self.get_user_profile(current_username)
            if not profile:
                raise ValueError("Utilizador não encontrado.")
            return profile

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        username, password_hash, role, full_name, organization, email, phone,
                        whatsapp_number, whatsapp_opt_in, whatsapp_opt_in_at, profile_completed_at
                    FROM app_users
                    WHERE username = %s
                    """,
                    (current_username,),
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError("Utilizador não encontrado.")

                cur.execute("SELECT 1 FROM app_users WHERE username = %s", (target_username,))
                if cur.fetchone():
                    raise ValueError("Esse utilizador já existe.")

                cur.execute(
                    """
                    INSERT INTO app_users (
                        username, password_hash, role, full_name, organization, email, phone,
                        whatsapp_number, whatsapp_opt_in, whatsapp_opt_in_at, profile_completed_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        target_username,
                        row["password_hash"],
                        row["role"],
                        row["full_name"],
                        row["organization"],
                        target_username,
                        row["phone"],
                        row["whatsapp_number"],
                        row["whatsapp_opt_in"],
                        row["whatsapp_opt_in_at"],
                        row["profile_completed_at"],
                    ),
                )
                cur.execute(
                    "UPDATE conversations SET username = %s WHERE username = %s",
                    (target_username, current_username),
                )
                cur.execute(
                    "UPDATE channel_events SET username = %s WHERE username = %s",
                    (target_username, current_username),
                )
                cur.execute(
                    """
                    SELECT key, value
                    FROM app_runtime_state
                    WHERE key LIKE %s
                    """,
                    (f"chat_pending_action:{current_username}:%",),
                )
                runtime_rows = cur.fetchall()
                for runtime_row in runtime_rows:
                    source_key = runtime_row["key"]
                    target_key = source_key.replace(
                        f"chat_pending_action:{current_username}:",
                        f"chat_pending_action:{target_username}:",
                        1,
                    )
                    payload = runtime_row["value"] if isinstance(runtime_row["value"], dict) else {}
                    if payload.get("username") == current_username:
                        payload = {**payload, "username": target_username}
                    cur.execute(
                        """
                        INSERT INTO app_runtime_state (key, value, updated_at)
                        VALUES (%s, %s::jsonb, NOW())
                        ON CONFLICT (key)
                        DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                        """,
                        (target_key, json.dumps(payload)),
                    )
                    cur.execute("DELETE FROM app_runtime_state WHERE key = %s", (source_key,))

                cur.execute("DELETE FROM app_users WHERE username = %s", (current_username,))
            conn.commit()
        return self.get_user_profile(target_username) or {}

    def set_user_role(self, username: str, role: str) -> Dict:
        username = _normalize_username(username)
        if role not in {"admin", "agente", "piloto"}:
            raise ValueError("Role invalido.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app_users
                    SET role = %s,
                        profile_completed_at = CASE
                            WHEN COALESCE(full_name, '') <> ''
                             AND COALESCE(organization, '') <> ''
                             AND COALESCE(email, '') <> ''
                             AND COALESCE(phone, '') <> ''
                            THEN COALESCE(profile_completed_at, NOW())
                            ELSE NULL
                        END
                    WHERE username = %s
                    RETURNING
                        username, role, full_name, organization, email, phone,
                        whatsapp_number, whatsapp_opt_in, whatsapp_opt_in_at, profile_completed_at
                    """,
                    (role, username),
                )
                row = cur.fetchone()
            conn.commit()
        if not row:
            raise ValueError("Utilizador não encontrado.")
        return _normalize_user_profile_payload(row)

    def reset_user_password(self, username: str, new_password: str) -> bool:
        username = _normalize_username(username)
        if len(new_password) < 6:
            raise ValueError("A password deve ter pelo menos 6 caracteres.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE app_users SET password_hash = %s WHERE username = %s",
                    (generate_password_hash(new_password, method=PASSWORD_HASH_METHOD), username),
                )
                conn.commit()
                return cur.rowcount > 0

    def delete_user(self, username: str) -> None:
        normalized_username = _normalize_username(username)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT username, role
                    FROM app_users
                    WHERE username = %s
                    """,
                    (normalized_username,),
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError("Utilizador não encontrado.")
                if row["role"] == "admin":
                    cur.execute("SELECT COUNT(*) AS total FROM app_users WHERE role = 'admin'")
                    admin_total = cur.fetchone()["total"]
                    if admin_total <= 1:
                        raise ValueError("Não podes apagar o último admin.")
                cur.execute("DELETE FROM app_users WHERE username = %s", (normalized_username,))
            conn.commit()

    def update_user_profile(
        self,
        username: str,
        *,
        full_name: str,
        organization: str,
        email: str,
        phone: str,
        whatsapp_number: str = "",
        whatsapp_opt_in: bool = False,
        whatsapp_opt_in_at: str = "",
    ) -> Dict:
        profile = _normalize_user_profile_payload(
            {
                "username": username,
                "full_name": full_name,
                "organization": organization,
                "email": email,
                "phone": phone,
                "whatsapp_number": whatsapp_number,
                "whatsapp_opt_in": whatsapp_opt_in,
                "whatsapp_opt_in_at": whatsapp_opt_in_at,
                "role": (self.get_user_profile(username) or {}).get("role", "piloto"),
            }
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app_users
                    SET
                        full_name = %s,
                        organization = %s,
                        email = %s,
                        phone = %s,
                        whatsapp_number = %s,
                        whatsapp_opt_in = %s,
                        whatsapp_opt_in_at = %s,
                        profile_completed_at = %s
                    WHERE username = %s
                    RETURNING
                        username, role, full_name, organization, email, phone,
                        whatsapp_number, whatsapp_opt_in, whatsapp_opt_in_at, profile_completed_at
                    """,
                    (
                        profile["full_name"],
                        profile["organization"],
                        profile["email"],
                        profile["phone"],
                        profile["whatsapp_number"],
                        profile["whatsapp_opt_in"],
                        profile["whatsapp_opt_in_at"],
                        profile["profile_completed_at"],
                        _normalize_username(username),
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        if not row:
            raise ValueError("Utilizador não encontrado.")
        return _normalize_user_profile_payload(row)
